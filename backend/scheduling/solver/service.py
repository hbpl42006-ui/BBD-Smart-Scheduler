import hashlib,json,logging,time
from dataclasses import replace
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from ortools.sat.python import cp_model
from scheduling.models import GenerationRun,ScheduleEntry,ScheduleEntryFaculty,TimetableVersion
from academics.models import Section
from scheduling.services.audit import record
from scheduling.services.validation import validate_version
from .engine import build_requirements,build_candidates,candidate_dict,fixed_conflicts,resource_capacity_diagnostics
from .objective import build_objective_context,candidate_score
logger=logging.getLogger(__name__)
class GenerationApplyError(ValueError):
 def __init__(self,code,message,conflicts=None):self.code=code;self.message=message;self.conflicts=conflicts or [];super().__init__(message)
def fingerprint(version):
 rows=list(version.entries.order_by('id').values('id','section_id','course_offering_id','weekday','start_slot_id','block_length','room_id','delivery_mode','locked'));rows += list(version.entries.order_by('id').values_list('faculty_assignments__faculty_id','faculty_assignments__role'));return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()
def preflight_generation(version,config,include_internal=False):
 mode=config.get('mode','FILL_GAPS');scope=set(map(str,config.get('section_ids') or []));preserved=version.entries.all()
 if mode=='REBUILD_UNLOCKED':
  preserved=preserved.filter(Q(locked=True)|~Q(section_id__in=scope)) if scope else preserved.filter(locked=True)
 statistics={'fully_satisfied_offerings_skipped':0,'preserved_entry_count':preserved.count(),'preserved_period_count':sum(preserved.values_list('block_length',flat=True))};reqs,errors=build_requirements(version,config,statistics);errors += fixed_conflicts(version,mode,config.get('section_ids'));errors += scope_errors(version,config);errors += resource_capacity_diagnostics(version,reqs,config,statistics);result={'valid':not errors,'errors':errors,'warnings':[],'mode':mode,'statistics':statistics,'requirements':[{'requirement_id':r.requirement_id,'course_offering_id':r.course_offering_id,'section_id':r.section_id,'block_length':r.block_length,'faculty':[x.__dict__ for x in r.faculty]} for r in reqs]}
 if include_internal:result['_requirements']=reqs
 return result
def scope_errors(version,config):
 from academics.models import Section,CourseOffering
 from faculty.models import Faculty
 errors=[];scope=set(map(str,config.get('section_ids') or []));valid_sections=Section.objects.filter(semester=version.timetable.semester,program__department__institution=version.timetable.institution)
 valid_ids={str(x) for x in valid_sections.values_list('id',flat=True)}
 for sid in scope-valid_ids:errors.append({'code':'INVALID_SECTION_SCOPE','section_id':sid,'message':'Section is outside the timetable institution/session scope.'})
 allowed=scope or valid_ids
 for section in Section.objects.filter(pk__in=allowed,delivery_policy=Section.DeliveryPolicy.HYBRID,offline_weekday__isnull=True).only('id','name'):
  errors.append({'code':'MISSING_OFFLINE_DAY','section_id':str(section.pk),'section':section.name,'message':f'{section.name} is configured as hybrid but has no offline weekday.'})
 rules=config.get('offering_rules',[]);offering_ids={rule.get('course_offering_id') for rule in rules if rule.get('course_offering_id')};faculty_ids={member.get('faculty_id') for rule in rules for member in rule.get('faculty',[]) if member.get('faculty_id')}
 offering_sections={str(row['id']):str(row['section_id']) for row in CourseOffering.objects.filter(pk__in=offering_ids).values('id','section_id')}
 valid_faculty={str(value) for value in Faculty.objects.filter(pk__in=faculty_ids,department__institution=version.timetable.institution).values_list('id',flat=True)}
 for rule in rules:
  offering_id=str(rule.get('course_offering_id'))
  if offering_id not in offering_sections or offering_sections[offering_id] not in allowed:errors.append({'code':'INVALID_OFFERING_SCOPE','course_offering_id':offering_id,'message':'Course offering is outside the selected section scope.'})
  for faculty in rule.get('faculty',[]):
   faculty_id=str(faculty.get('faculty_id'))
   if faculty_id not in valid_faculty:errors.append({'code':'INVALID_FACULTY_SCOPE','faculty_id':faculty_id,'message':'Faculty is outside the timetable institution scope.'})
 return errors
def _gen_log(message):
 print(f'[GEN RUN] {message}',flush=True)

def run_generation(run,validated_requirements=None):
 started=time.perf_counter()
 max_solve_seconds=min(max(float(run.input_config.get('max_solve_seconds',30)),1),60)
 _gen_log(f'generation service start run_id={run.pk} max_solve_seconds={max_solve_seconds}')
 persistence_started=time.perf_counter()
 run.status=GenerationRun.Status.RUNNING;run.started_at=timezone.now();run.save(update_fields=['status','started_at'])
 _gen_log(f'persistence start/end elapsed={time.perf_counter()-persistence_started:.3f}s state=RUNNING')

 requirements_started=time.perf_counter();_gen_log('requirements start')
 if validated_requirements is None:
  reqs,errors=build_requirements(run.source_version,run.input_config)
  errors += fixed_conflicts(run.source_version,run.input_config.get('mode','FILL_GAPS'),run.input_config.get('section_ids'))
  errors += scope_errors(run.source_version,run.input_config)
  errors += resource_capacity_diagnostics(run.source_version,reqs,run.input_config)
 else:reqs,errors=validated_requirements,[]
 _gen_log(f'requirements end elapsed={time.perf_counter()-requirements_started:.3f}s count={len(reqs)} errors={len(errors)} reused_preflight={validated_requirements is not None}')
 if errors:return _finish(run,GenerationRun.Status.INFEASIBLE,'INFEASIBLE',{'errors':errors},started)

 candidates_started=time.perf_counter();_gen_log('candidates start')
 candidate_profile={}
 candidates,diagnostics=build_candidates(run.source_version,reqs,run.input_config,candidate_profile)
 candidate_count=sum(len(items) for items in candidates.values())
 room_count=len({candidate.room_id for items in candidates.values() for candidate in items if candidate.room_id})
 slot_count=len({slot for items in candidates.values() for candidate in items for slot in candidate.occupied_slot_ids})
 faculty_count=len({assignment.faculty_id for requirement in reqs for assignment in requirement.faculty})
 section_count=len({requirement.section_id for requirement in reqs})
 _gen_log(f'candidates end elapsed={time.perf_counter()-candidates_started:.3f}s count={candidate_count} rooms={room_count} slots={slot_count} faculty={faculty_count} sections={section_count} diagnostics={len(diagnostics)} average_per_requirement={candidate_profile.get("average_candidates_per_requirement",0):.1f} p50={candidate_profile.get("p50_candidates_per_requirement",0)} p95={candidate_profile.get("p95_candidates_per_requirement",0)} max={candidate_profile.get("max_candidates_per_requirement",0)}')
 _gen_log(f'candidate profile top30={json.dumps(candidate_profile.get("top_30",[]),separators=(",",":"))}')
 if diagnostics:return _finish(run,GenerationRun.Status.INFEASIBLE,'INFEASIBLE',{'errors':diagnostics},started)

 model_started=time.perf_counter();_gen_log('model build start')
 model=cp_model.CpModel();variables=[];room_resources={};section_resources={};faculty_resources={};placement_variable_count=0
 hard_started=time.perf_counter();_gen_log('hard constraints start')
 for req in reqs:
  placements={}
  for candidate in candidates[req.requirement_id]:
   placement_key=(candidate.weekday,candidate.start_slot_id,candidate.occupied_slot_ids)
   placement=placements.get(placement_key)
   if placement is None:
    placement=model.NewBoolVar(f'{req.requirement_id}:{candidate.weekday}:{candidate.start_slot_id}:placement');placements[placement_key]={'variable':placement,'room_variables':[],'candidate':candidate,'online':candidate.delivery_mode=='ONLINE'};placement_variable_count+=1
   if candidate.delivery_mode=='ONLINE':
    variables.append((placement,candidate))
   else:
    variable=model.NewBoolVar(f'{req.requirement_id}:{candidate.weekday}:{candidate.start_slot_id}:{candidate.room_id}:room');placements[placement_key]['room_variables'].append(variable);variables.append((variable,candidate))
    for slot_id in candidate.occupied_slot_ids:
     room_resources.setdefault((candidate.room_id,candidate.weekday,slot_id),[]).append(variable)
  for placement in placements.values():
   if not placement['online']:model.Add(sum(placement['room_variables'])==placement['variable'])
   candidate=placement['candidate']
   for slot_id in candidate.occupied_slot_ids:
    section_resources.setdefault((candidate.section_id,candidate.weekday,slot_id),[]).append(placement['variable'])
    for assignment in candidate.faculty:
     faculty_resources.setdefault((assignment.faculty_id,candidate.weekday,slot_id),[]).append(placement['variable'])
  model.Add(sum(placement['variable'] for placement in placements.values())==1)
 for resource_variables in section_resources.values():model.Add(sum(resource_variables)<=1)
 for resource_variables in faculty_resources.values():model.Add(sum(resource_variables)<=1)
 room_group_sizes=candidate_profile['_room_group_sizes']
 for (room_group_id,_weekday,_slot_id),resource_variables in room_resources.items():model.Add(sum(resource_variables)<=room_group_sizes[room_group_id])
 hard_elapsed=time.perf_counter()-hard_started;_gen_log(f'hard constraints end elapsed={hard_elapsed:.3f}s section_groups={len(section_resources)} faculty_groups={len(faculty_resources)} room_groups={len(room_resources)} placements={placement_variable_count}')
 objective_started=time.perf_counter();_gen_log('objective context load start')
 context_started=time.perf_counter();objective_context=build_objective_context(run.source_version,candidate_profile['_room_group_by_room'])
 _gen_log(f'objective context load end elapsed={time.perf_counter()-context_started:.3f}s entries={sum(len(items) for items in objective_context["entries_by_offering"].values())} offerings={len(objective_context["entries_by_offering"])} faculty_day_keys={len(objective_context["entries_by_faculty_day"])}')
 _gen_log('objective coefficient creation start')
 coefficient_started=time.perf_counter();objective_variables=[];objective_coefficients=[]
 for variable,candidate in variables:
  coefficient=int(candidate_score(candidate,run.source_version,run.input_config,objective_context))
  if coefficient:
   objective_variables.append(variable);objective_coefficients.append(coefficient)
 _gen_log(f'objective coefficient creation end elapsed={time.perf_counter()-coefficient_started:.3f}s terms={len(objective_variables)} skipped_zero={len(variables)-len(objective_variables)} score_cache_entries={len(objective_context["base_score_cache"])}')
 expression_started=time.perf_counter()
 if objective_variables:model.Maximize(cp_model.LinearExpr.weighted_sum(objective_variables,objective_coefficients))
 else:model.Maximize(0)
 profile=objective_context['objective_profile']
 objective_elapsed=time.perf_counter()-objective_started
 _gen_log(f'objective sub-stages seconds room_preference={profile["room_preference"]:.3f} course_spread={profile["course_spread"]:.3f} section_gaps={profile["section_gaps"]:.3f} faculty_gaps={profile["faculty_gaps"]:.3f} daily_balance={profile["daily_balance"]:.3f} other_penalties={profile["other_penalties"]:.3f} final_expression={time.perf_counter()-expression_started:.3f} auxiliary_variables=0 auxiliary_constraints=0')
 _gen_log(f'objective creation end elapsed={objective_elapsed:.3f}s')
 proto=model.Proto()
 _gen_log(f'model build end elapsed={time.perf_counter()-model_started:.3f}s vars={len(proto.variables)} candidate_room_vars={len(variables)} placement_vars={placement_variable_count} constraints={len(proto.constraints)} objective_terms={len(objective_variables)} objective_auxiliary_vars=0 objective_auxiliary_constraints=0')

 solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=max_solve_seconds;solver.parameters.random_seed=int(run.input_config.get('random_seed',42))
 _gen_log(f'solver configured max_time_in_seconds={solver.parameters.max_time_in_seconds}')
 _gen_log(f'solver start limit={solver.parameters.max_time_in_seconds}')
 solver_started=time.perf_counter();status=solver.Solve(model);solver_elapsed=time.perf_counter()-solver_started
 _gen_log(f'solver end elapsed={solver_elapsed:.3f}s wall_time={solver.WallTime():.3f}s status={solver.StatusName(status)}')
 mapping={cp_model.OPTIMAL:('OPTIMAL',GenerationRun.Status.SUCCEEDED),cp_model.FEASIBLE:('FEASIBLE',GenerationRun.Status.SUCCEEDED),cp_model.INFEASIBLE:('INFEASIBLE',GenerationRun.Status.INFEASIBLE),cp_model.MODEL_INVALID:('MODEL_INVALID',GenerationRun.Status.FAILED)}
 solver_status,application=mapping.get(status,('UNKNOWN',GenerationRun.Status.FAILED))

 extraction_started=time.perf_counter();_gen_log('extraction start')
 selected=[candidate for variable,candidate in variables if application==GenerationRun.Status.SUCCEEDED and solver.Value(variable)]
 selected.sort(key=lambda candidate:(candidate.weekday, candidate_profile['_room_slot_orders'].get(candidate.start_slot_id,0),candidate.requirement_id))
 room_occupancy=set();result=[]
 for candidate in selected:
  if candidate.delivery_mode=='ONLINE':
   result.append(candidate_dict(candidate)|{'room_id':None,'delivery_mode':'ONLINE','origin':'GENERATED'});continue
  group_rooms=candidate_profile['_room_group_members'][candidate.room_id]
  room_id=next((room for room in group_rooms if all((room,candidate.weekday,slot_id) not in room_occupancy for slot_id in candidate.occupied_slot_ids)),None)
  if room_id is None:raise RuntimeError(f'Could not assign a physical room for room group {candidate.room_id}.')
  room_occupancy.update((room_id,candidate.weekday,slot_id) for slot_id in candidate.occupied_slot_ids)
  result.append(candidate_dict(replace(candidate,room_id=room_id))|{'delivery_mode':'OFFLINE','origin':'GENERATED'})
 objective_score=float(solver.ObjectiveValue())
 extraction_elapsed=time.perf_counter()-extraction_started
 _gen_log(f'extraction end elapsed={extraction_elapsed:.3f}s entries={len(result)}')

 persistence_started=time.perf_counter();_gen_log('persistence start')
 online_periods=sum(item['block_length'] for item in result if item['delivery_mode']=='ONLINE');offline_periods=sum(item['block_length'] for item in result if item['delivery_mode']=='OFFLINE')
 run.status=application;run.solver_status=solver_status;run.result={'entries':result};run.statistics={'requirement_count':len(reqs),'candidate_count':candidate_count,'variable_count':len(proto.variables),'candidate_room_variable_count':len(variables),'placement_variable_count':placement_variable_count,'constraint_count':len(proto.constraints),'objective_term_count':len(objective_variables),'generated_entry_count':len(result),'online_periods':online_periods,'offline_periods':offline_periods,'fixed_entry_count':run.source_version.entries.count(),'wall_time':solver.WallTime(),'num_conflicts':solver.NumConflicts(),'num_branches':solver.NumBranches()};run.objective_score=objective_score
 if application==GenerationRun.Status.SUCCEEDED:run.diagnostics={'errors':[]}
 elif solver_status=='UNKNOWN':run.diagnostics={'errors':[{'code':'TIME_LIMIT','message':'No feasible timetable was found within the solver time limit.'}]}
 else:run.diagnostics={'errors':[{'code':solver_status,'message':'No feasible timetable was found.'}]}
 run.completed_at=timezone.now();run.save();record('GENERATION_SUCCEEDED' if application==GenerationRun.Status.SUCCEEDED else 'GENERATION_INFEASIBLE',run.created_by,timetable=run.timetable,version=run.source_version,entity_type='GenerationRun',entity_id=run.id,metadata={'solver_status':solver_status})
 _gen_log(f'persistence end elapsed={time.perf_counter()-persistence_started:.3f}s')
 _gen_log(f'total elapsed={time.perf_counter()-started:.3f}s solver_status={solver_status} candidates={candidate_count} vars={len(proto.variables)} constraints={len(proto.constraints)}')
 return run

def _finish(run,status,solver_status,diagnostics,started=None):
 persistence_started=time.perf_counter();_gen_log('persistence start (terminal diagnostic)')
 run.status=status;run.solver_status=solver_status;run.diagnostics=diagnostics;run.completed_at=timezone.now();run.save();record('GENERATION_INFEASIBLE',run.created_by,timetable=run.timetable,version=run.source_version,entity_type='GenerationRun',entity_id=run.id,metadata={'solver_status':solver_status})
 _gen_log(f'persistence end elapsed={time.perf_counter()-persistence_started:.3f}s')
 if started is not None:_gen_log(f'total elapsed={time.perf_counter()-started:.3f}s status={solver_status} stage=pre-solver')
 return run
@transaction.atomic
def apply_generation(run):
 run=GenerationRun.objects.select_for_update().select_related('source_version','timetable').get(pk=run.pk)
 if run.status==GenerationRun.Status.APPLIED:raise GenerationApplyError('GENERATION_ALREADY_APPLIED','Generation run has already been applied.')
 if run.status!=GenerationRun.Status.SUCCEEDED or run.solver_status not in ('OPTIMAL','FEASIBLE'):raise GenerationApplyError('INVALID_GENERATION_STATUS','Generation run is not applicable.')
 if fingerprint(run.source_version)!=run.source_fingerprint:raise GenerationApplyError('SOURCE_VERSION_CHANGED','The source version changed after generation.')
 latest=TimetableVersion.objects.select_for_update().filter(timetable=run.timetable).order_by('-version_no').first();new=TimetableVersion.objects.create(timetable=run.timetable,version_no=latest.version_no+1,previous_version=run.source_version,created_by=run.created_by,notes=f'Generated from version {run.source_version.version_no}')
 scope=set(run.input_config.get('section_ids') or []);mode=run.mode
 for entry in run.source_version.entries.prefetch_related('faculty_assignments').all():
  if mode=='REBUILD_UNLOCKED' and (not scope or str(entry.section_id) in scope) and not entry.locked:continue
  clone=ScheduleEntry.objects.create(version=new,section=entry.section,course_offering=entry.course_offering,weekday=entry.weekday,start_slot=entry.start_slot,block_length=entry.block_length,room=entry.room,entry_type=entry.entry_type,delivery_mode=entry.delivery_mode,locked=entry.locked,note=entry.note);clone.faculty_assignments.set(entry.faculty_assignments.all())
 generated_entries=run.result.get('entries',[]) if isinstance(run.result,dict) else []
 section_map={str(section_id):section for section_id,section in Section.objects.in_bulk({item.get('section_id') for item in generated_entries if item.get('section_id')}).items()}
 for item in generated_entries:
  section=section_map.get(str(item.get('section_id')))
  weekday=item['weekday']
  is_online=bool(section and section.delivery_policy==Section.DeliveryPolicy.HYBRID and weekday!=section.offline_weekday)
  entry=ScheduleEntry.objects.create(version=new,section_id=item['section_id'],course_offering_id=item['course_offering_id'],weekday=weekday,start_slot_id=item['start_slot_id'],block_length=item['block_length'],room_id=None if is_online else item.get('room_id'),entry_type=item['entry_type'],delivery_mode='ONLINE' if is_online else 'OFFLINE')
  ScheduleEntryFaculty.objects.bulk_create([ScheduleEntryFaculty(schedule_entry=entry,faculty_id=a['faculty_id'],role=a['role']) for a in item.get('faculty',[])])
 conflicts=validate_version(new)
 if conflicts.get('conflicts'):
  raise GenerationApplyError('GENERATED_TIMETABLE_VALIDATION_FAILED','Generated timetable failed final validation.',conflicts.get('conflicts',[]))
 run.status=GenerationRun.Status.APPLIED;run.applied_version=new;run.applied_at=timezone.now();run.save(update_fields=['status','applied_version','applied_at']);record('GENERATION_APPLIED',run.created_by,timetable=run.timetable,version=new,entity_type='GenerationRun',entity_id=run.id,metadata={'generation_run_id':str(run.id),'source_version':str(run.source_version_id)});return new
