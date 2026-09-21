import hashlib,json
from django.db import transaction
from django.utils import timezone
from ortools.sat.python import cp_model
from scheduling.models import GenerationRun,ScheduleEntry,ScheduleEntryFaculty,TimetableVersion
from scheduling.services.audit import record
from scheduling.services.validation import validate_version
from .engine import build_requirements,build_candidates,candidate_dict,fixed_conflicts
from .objective import candidate_score
class GenerationApplyError(ValueError):
 def __init__(self,code,message,conflicts=None):self.code=code;self.message=message;self.conflicts=conflicts or [];super().__init__(message)
def fingerprint(version):
 rows=list(version.entries.order_by('id').values('id','section_id','course_offering_id','weekday','start_slot_id','block_length','room_id','locked'));rows += list(version.entries.order_by('id').values_list('faculty_assignments__faculty_id','faculty_assignments__role'));return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()
def preflight_generation(version,config):
 reqs,errors=build_requirements(version,config);errors += fixed_conflicts(version,config.get('mode','FILL_GAPS'),config.get('section_ids'));errors += scope_errors(version,config);return {'valid':not errors,'errors':errors,'warnings':[],'requirements':[{'requirement_id':r.requirement_id,'course_offering_id':r.course_offering_id,'section_id':r.section_id,'block_length':r.block_length,'faculty':[x.__dict__ for x in r.faculty]} for r in reqs]}
def scope_errors(version,config):
 from academics.models import Section,CourseOffering
 from faculty.models import Faculty
 errors=[];scope=set(map(str,config.get('section_ids') or []));valid_sections=Section.objects.filter(semester=version.timetable.semester,program__department__institution=version.timetable.institution)
 valid_ids={str(x) for x in valid_sections.values_list('id',flat=True)}
 for sid in scope-valid_ids:errors.append({'code':'INVALID_SECTION_SCOPE','section_id':sid,'message':'Section is outside the timetable institution/session scope.'})
 allowed=scope or valid_ids
 for rule in config.get('offering_rules',[]):
  offering=CourseOffering.objects.filter(pk=rule.get('course_offering_id')).select_related('section').first()
  if not offering or str(offering.section_id) not in allowed:errors.append({'code':'INVALID_OFFERING_SCOPE','course_offering_id':str(rule.get('course_offering_id')),'message':'Course offering is outside the selected section scope.'})
  for faculty in rule.get('faculty',[]):
   person=Faculty.objects.filter(pk=faculty.get('faculty_id'),department__institution=version.timetable.institution).exists()
   if not person:errors.append({'code':'INVALID_FACULTY_SCOPE','faculty_id':str(faculty.get('faculty_id')),'message':'Faculty is outside the timetable institution scope.'})
 return errors
def run_generation(run):
 run.status=GenerationRun.Status.RUNNING;run.started_at=timezone.now();run.save(update_fields=['status','started_at']);reqs,errors=build_requirements(run.source_version,run.input_config)
 errors += fixed_conflicts(run.source_version,run.input_config.get('mode','FILL_GAPS'),run.input_config.get('section_ids'))
 errors += scope_errors(run.source_version,run.input_config)
 if errors:return _finish(run,GenerationRun.Status.INFEASIBLE,'INFEASIBLE',{'errors':errors})
 candidates,diagnostics=build_candidates(run.source_version,reqs,run.input_config)
 if diagnostics:return _finish(run,GenerationRun.Status.INFEASIBLE,'INFEASIBLE',{'errors':diagnostics})
 model=cp_model.CpModel();variables=[];resources={}
 for req in reqs:
  local=[]
  for c in candidates[req.requirement_id]:
   v=model.NewBoolVar(f'{req.requirement_id}:{c.start_slot_id}:{c.room_id}');local.append((v,c));variables.append((v,c))
   for sid in c.occupied_slot_ids:
    keys=[('section',c.section_id,c.weekday,sid),('room',c.room_id,c.weekday,sid)]+[('faculty',a.faculty_id,c.weekday,sid) for a in c.faculty]
    for key in keys:resources.setdefault(key,[]).append(v)
  model.Add(sum(v for v,_ in local)==1)
 for values in resources.values():model.Add(sum(values)<=1)
 model.Maximize(sum(int(candidate_score(c,run.source_version,run.input_config))*v for v,c in variables))
 solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=min(max(float(run.input_config.get('max_solve_seconds',30)),1),60);solver.parameters.random_seed=int(run.input_config.get('random_seed',42));status=solver.Solve(model);mapping={cp_model.OPTIMAL:('OPTIMAL',GenerationRun.Status.SUCCEEDED),cp_model.FEASIBLE:('FEASIBLE',GenerationRun.Status.SUCCEEDED),cp_model.INFEASIBLE:('INFEASIBLE',GenerationRun.Status.INFEASIBLE),cp_model.MODEL_INVALID:('MODEL_INVALID',GenerationRun.Status.FAILED)};solver_status,application=mapping.get(status,('UNKNOWN',GenerationRun.Status.FAILED));result=[candidate_dict(c)|{'origin':'GENERATED'} for v,c in variables if application==GenerationRun.Status.SUCCEEDED and solver.Value(v)];run.status=application;run.solver_status=solver_status;run.result={'entries':result};run.statistics={'requirement_count':len(reqs),'candidate_count':len(variables),'variable_count':len(variables),'generated_entry_count':len(result),'fixed_entry_count':run.source_version.entries.count(),'wall_time':solver.WallTime(),'num_conflicts':solver.NumConflicts(),'num_branches':solver.NumBranches()};run.objective_score=float(solver.ObjectiveValue());run.diagnostics={'errors':[]} if application==GenerationRun.Status.SUCCEEDED else {'errors':[{'code':'INFEASIBLE','message':'No feasible timetable was found.'}]};run.completed_at=timezone.now();run.save();record('GENERATION_SUCCEEDED' if application==GenerationRun.Status.SUCCEEDED else 'GENERATION_INFEASIBLE',run.created_by,timetable=run.timetable,version=run.source_version,entity_type='GenerationRun',entity_id=run.id,metadata={'solver_status':solver_status});return run
def _finish(run,status,solver_status,diagnostics):run.status=status;run.solver_status=solver_status;run.diagnostics=diagnostics;run.completed_at=timezone.now();run.save();record('GENERATION_INFEASIBLE',run.created_by,timetable=run.timetable,version=run.source_version,entity_type='GenerationRun',entity_id=run.id,metadata={'solver_status':solver_status});return run
@transaction.atomic
def apply_generation(run):
 run=GenerationRun.objects.select_for_update().select_related('source_version','timetable').get(pk=run.pk)
 if run.status==GenerationRun.Status.APPLIED:raise GenerationApplyError('GENERATION_ALREADY_APPLIED','Generation run has already been applied.')
 if run.status!=GenerationRun.Status.SUCCEEDED or run.solver_status not in ('OPTIMAL','FEASIBLE'):raise GenerationApplyError('INVALID_GENERATION_STATUS','Generation run is not applicable.')
 if fingerprint(run.source_version)!=run.source_fingerprint:raise GenerationApplyError('SOURCE_VERSION_CHANGED','The source version changed after generation.')
 latest=TimetableVersion.objects.select_for_update().filter(timetable=run.timetable).order_by('-version_no').first();new=TimetableVersion.objects.create(timetable=run.timetable,version_no=latest.version_no+1,previous_version=run.source_version,created_by=run.created_by,notes=f'Generated from version {run.source_version.version_no}')
 scope=set(run.input_config.get('section_ids') or []);mode=run.mode
 for entry in run.source_version.entries.prefetch_related('faculty_assignments').all():
  if mode=='REBUILD_UNLOCKED' and scope and str(entry.section_id) in scope and not entry.locked:continue
  clone=ScheduleEntry.objects.create(version=new,section=entry.section,course_offering=entry.course_offering,weekday=entry.weekday,start_slot=entry.start_slot,block_length=entry.block_length,room=entry.room,entry_type=entry.entry_type,locked=entry.locked,note=entry.note);clone.faculty_assignments.set(entry.faculty_assignments.all())
 for item in run.result.get('entries',[]):
  entry=ScheduleEntry.objects.create(version=new,section_id=item['section_id'],course_offering_id=item['course_offering_id'],weekday=item['weekday'],start_slot_id=item['start_slot_id'],block_length=item['block_length'],room_id=item.get('room_id'),entry_type=item['entry_type'])
  ScheduleEntryFaculty.objects.bulk_create([ScheduleEntryFaculty(schedule_entry=entry,faculty_id=a['faculty_id'],role=a['role']) for a in item.get('faculty',[])])
 conflicts=validate_version(new)
 if conflicts.get('conflicts'):
  raise GenerationApplyError('GENERATED_TIMETABLE_VALIDATION_FAILED','Generated timetable failed final validation.',conflicts.get('conflicts',[]))
 run.status=GenerationRun.Status.APPLIED;run.applied_version=new;run.applied_at=timezone.now();run.save(update_fields=['status','applied_version','applied_at']);record('GENERATION_APPLIED',run.created_by,timetable=run.timetable,version=new,entity_type='GenerationRun',entity_id=run.id,metadata={'generation_run_id':str(run.id),'source_version':str(run.source_version_id)});return new
