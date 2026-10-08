import hashlib,json,logging,time
from collections import Counter,defaultdict
from dataclasses import replace
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from ortools.sat.python import cp_model
from scheduling.models import GenerationRun,ScheduleEntry,ScheduleEntryFaculty,TimetableVersion
from academics.models import Section
from scheduling.services.audit import record
from scheduling.services.validation import validate_version,validate_generated_entries
from .engine import build_requirements,build_candidates,candidate_dict,fixed_conflicts,fixed_occupancy,resource_capacity_diagnostics
from .objective import build_objective_context,candidate_score
from scheduling.weekdays import WORKING_DAYS, WEEKEND_DAYS, WEEKEND_ERROR_CODE, WEEKDAY_NAMES, is_working_day
logger=logging.getLogger(__name__)
class GenerationApplyError(ValueError):
 def __init__(self,code,message,conflicts=None):self.code=code;self.message=message;self.conflicts=conflicts or [];super().__init__(message)
def fingerprint(version):
 rows=list(version.entries.order_by('id').values('id','section_id','course_offering_id','weekday','start_slot_id','block_length','room_id','delivery_mode','locked'));rows += list(version.entries.order_by('id').values_list('faculty_assignments__faculty_id','faculty_assignments__role'));return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()
def preflight_generation(version,config,include_internal=False):
 mode=config.get('mode','FILL_GAPS');scope=set(map(str,config.get('section_ids') or []));preserved=version.entries.all()
 if mode=='REBUILD_UNLOCKED':
  preserved=preserved.filter(Q(locked=True)|~Q(section_id__in=scope)) if scope else preserved.filter(locked=True)
 statistics={'fully_satisfied_offerings_skipped':0,'preserved_entry_count':preserved.count(),'preserved_period_count':sum(preserved.values_list('block_length',flat=True))};reqs,errors=build_requirements(version,config,statistics);errors += fixed_conflicts(version,mode,config.get('section_ids'));scope_diagnostics=scope_errors(version,config);errors += scope_diagnostics;errors += resource_capacity_diagnostics(version,reqs,config,statistics)
 from academics.models import CourseOffering,Section,SectionWeeklyOffPolicy
 policy_qs=SectionWeeklyOffPolicy.objects.filter(is_active=True,academic_session=version.timetable.academic_session,semester=version.timetable.semester,section__program__code='BTECH-CSE',section__program__department__institution=version.timetable.institution,section__year__in=(2,3))
 if scope:policy_qs=policy_qs.filter(section_id__in=scope)
 statistics['weekly_off_policy_count']=policy_qs.filter(policy_type=SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF).count()
 statistics['no_weekly_off_policy_count']=policy_qs.filter(policy_type=SectionWeeklyOffPolicy.PolicyType.NO_WEEKLY_OFF).count()
 statistics['source_confirmed_section_count']=policy_qs.count()
 target_sections=Section.objects.filter(semester=version.timetable.semester,program__code='BTECH-CSE',program__department__institution=version.timetable.institution,year__in=(2,3),semester__session=version.timetable.academic_session)
 if scope:target_sections=target_sections.filter(pk__in=scope)
 active_target_ids=set(CourseOffering.objects.filter(section__in=target_sections,active=True).values_list('section_id',flat=True))
 mapped_ids=set(policy_qs.values_list('section_id',flat=True))
 statistics['missing_off_day_source_count']=len(active_target_ids-mapped_ids)
 statistics['weekly_off_day_mappings']=[{'section':policy.section.name,'section_id':str(policy.section_id),'year':policy.section.year,'policy_type':policy.policy_type,'weekday':policy.weekday,'day':WEEKDAY_NAMES[policy.weekday] if policy.policy_type==SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF else 'No weekly OFF day (Mon-Fri allowed)'} for policy in policy_qs.select_related('section').order_by('section__year','section__name')]
 statistics['locked_off_day_conflict_count']=sum(item['code']=='LOCKED_ENTRY_ON_SECTION_OFF_DAY' for item in scope_diagnostics)
 statistics['section_weekly_capacity_conflict_count']=sum(item['code']=='SECTION_WEEKLY_CAPACITY_EXCEEDED' for item in scope_diagnostics)
 result={'valid':not errors,'errors':errors,'warnings':[],'mode':mode,'statistics':statistics,'requirements':[{'requirement_id':r.requirement_id,'course_offering_id':r.course_offering_id,'section_id':r.section_id,'block_length':r.block_length,'faculty':[x.__dict__ for x in r.faculty]} for r in reqs]}
 if include_internal:result['_requirements']=reqs
 return result
def scope_errors(version,config):
 from academics.models import Section,CourseOffering,SectionWeeklyOffPolicy
 from faculty.models import Faculty
 from common.models import TimeSlot
 from scheduling.models import ScheduleEntry
 from collections import defaultdict
 from django.db.models import Sum
 errors=[];mode=config.get('mode','FILL_GAPS');scope=set(map(str,config.get('section_ids') or []));valid_sections=Section.objects.filter(semester=version.timetable.semester,program__department__institution=version.timetable.institution)
 valid_ids={str(x) for x in valid_sections.values_list('id',flat=True)}
 for sid in scope-valid_ids:errors.append({'code':'INVALID_SECTION_SCOPE','section_id':sid,'message':'Section is outside the timetable institution/session scope.'})
 allowed=scope or valid_ids
 from academics.delivery import get_section_delivery_policy
 selected_sections=list(Section.objects.filter(pk__in=allowed).select_related('program','semester__session').prefetch_related('period_delivery_policies','weekly_off_policies'))
 for section in selected_sections:
  policy,offline_weekday=get_section_delivery_policy(section,version.timetable.semester)
  if policy=='HYBRID' and offline_weekday is None:
   errors.append({'code':'MISSING_OFFLINE_DAY','section_id':str(section.pk),'section':section.name,'message':f'{section.name} is configured as hybrid but has no offline weekday.'})
  elif policy=='HYBRID' and offline_weekday not in WORKING_DAYS:
   day_name=WEEKDAY_NAMES[offline_weekday] if isinstance(offline_weekday,int) and 0<=offline_weekday<len(WEEKDAY_NAMES) else str(offline_weekday)
   errors.append({'code':'INVALID_HYBRID_OFFLINE_DAY','section_id':str(section.pk),'section':section.name,'offline_weekday':offline_weekday,'day':day_name,'message':'Hybrid offline day must be a working day (Monday-Friday).'})
 target_sections=[section for section in selected_sections if section.program.code=='BTECH-CSE' and section.year in (2,3) and section.semester.session.name=='2026-27' and section.semester.type=='ODD']
 target_ids=[section.pk for section in target_sections]
 offering_totals={str(row['section_id']):row['required_periods'] or 0 for row in CourseOffering.objects.filter(section_id__in=target_ids,active=True).values('section_id').annotate(required_periods=Sum('weekly_periods'))}
 has_active_offering=set(offering_totals)
 weekly_policies={str(policy.section_id):policy for policy in SectionWeeklyOffPolicy.objects.filter(section_id__in=target_ids,academic_session=version.timetable.academic_session,semester=version.timetable.semester,is_active=True)}
 teaching_by_template=defaultdict(int)
 for template_id,is_break in TimeSlot.objects.filter(template__institution=version.timetable.institution).values_list('template_id','is_break'):
  if not is_break:teaching_by_template[template_id]+=1
 day_capacity=max(teaching_by_template.values(),default=0)
 for section in target_sections:
  policy=weekly_policies.get(str(section.pk))
  if str(section.pk) in has_active_offering and policy is None:
   errors.append({'code':'OFF_DAY_SOURCE_MISSING','section_id':str(section.pk),'section':section.name,'program':section.program.code,'year':section.year,'message':f'{section.name} Year {section.year} has active offerings but no authoritative weekly OFF-day mapping.'})
  if policy and str(section.pk) in has_active_offering:
   weekly_off=policy.policy_type==SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF
   day_name=WEEKDAY_NAMES[policy.weekday] if weekly_off else None
   available_days=len(WORKING_DAYS)-1 if weekly_off else len(WORKING_DAYS)
   available_capacity=max(0,day_capacity*available_days)
   required_periods=offering_totals.get(str(section.pk),0)
   if required_periods>available_capacity:
    reason=f' because {day_name} is configured as its official weekly OFF day' if weekly_off else ' across the five teaching weekdays'
    errors.append({'code':'SECTION_WEEKLY_CAPACITY_EXCEEDED','section_id':str(section.pk),'section':section.name,'program':section.program.code,'year':section.year,'required_periods':required_periods,'available_periods':available_capacity,'weekday':policy.weekday,'day':day_name,'message':f'{section.name} requires {required_periods} periods but only {available_capacity} teaching periods are available{reason}.'})
 offday_section_ids={str(section.pk):weekly_policies[str(section.pk)] for section in target_sections if str(section.pk) in weekly_policies}
 locked_rows=ScheduleEntry.objects.filter(version=version,locked=True,section_id__in=offday_section_ids).select_related('section','course_offering__course','start_slot','room').prefetch_related('faculty_assignments__faculty')
 for entry in locked_rows:
  policy=offday_section_ids[str(entry.section_id)]
  if policy.policy_type==SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF and entry.weekday==policy.weekday:
   errors.append({'code':'LOCKED_ENTRY_ON_SECTION_OFF_DAY','section':entry.section.name,'section_id':str(entry.section_id),'weekday':entry.weekday,'day':WEEKDAY_NAMES[entry.weekday],'course':entry.course_offering.course.code,'course_name':entry.course_offering.course.name,'time_slot':entry.start_slot.label,'room':entry.room.code if entry.room_id else None,'faculty':[assignment.faculty.name or assignment.faculty.initials or assignment.faculty.employee_code for assignment in entry.faculty_assignments.all()],'entry_id':str(entry.pk),'version_id':str(version.pk),'message':f'Locked entry {entry.pk} for {entry.course_offering.course.code} is scheduled on {entry.section.name}’s configured {WEEKDAY_NAMES[entry.weekday]} OFF day and must be reconciled before generation.'})
 # Final validation preserves locked entries and every out-of-scope section in
 # REBUILD_UNLOCKED mode. Check that same preserved set here so an existing
 # official OFF-day conflict is reported before spending time in CP-SAT.
 preserved_entries=ScheduleEntry.objects.filter(version=version).select_related('section','course_offering__course','start_slot','room').prefetch_related('faculty_assignments__faculty')
 if mode=='REBUILD_UNLOCKED':
  preserved_entries=(preserved_entries.filter(Q(locked=True)|~Q(section_id__in=scope)) if scope else preserved_entries.filter(locked=True))
 preserved_section_ids=set(preserved_entries.values_list('section_id',flat=True))
 preserved_policies={str(policy.section_id):policy for policy in SectionWeeklyOffPolicy.objects.filter(section_id__in=preserved_section_ids,academic_session=version.timetable.academic_session,semester=version.timetable.semester,is_active=True)}
 reported_entry_ids={str(item.get('entry_id')) for item in errors if item.get('entry_id')}
 for entry in preserved_entries:
  policy=preserved_policies.get(str(entry.section_id))
  if not policy or policy.policy_type!=SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF or entry.weekday!=policy.weekday or str(entry.pk) in reported_entry_ids:continue
  day=WEEKDAY_NAMES[entry.weekday]
  errors.append({'code':'SECTION_WEEKLY_OFF_DAY_VIOLATION','type':'SECTION_WEEKLY_OFF_DAY_VIOLATION','severity':'ERROR','section_id':str(entry.section_id),'section':entry.section.name,'weekday':entry.weekday,'day':day,'course':entry.course_offering.course.code,'time_slot':entry.start_slot.label,'room':entry.room.code if entry.room_id else None,'faculty':[assignment.faculty.name or assignment.faculty.initials or assignment.faculty.employee_code for assignment in entry.faculty_assignments.all()],'entry_id':str(entry.pk),'version_id':str(version.pk),'locked':entry.locked,'message':f'{entry.section.name} has {day} configured as its official weekly OFF day.'})
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

def _solver_metric(getter,default=0):
 try:return getter()
 except (AttributeError,RuntimeError,TypeError):return default

class _FirstSolutionTelemetry(cp_model.CpSolverSolutionCallback):
 def __init__(self):
  super().__init__();self.started_at=None;self.solutions_found=0;self.time_to_first_solution=None;self.objective_at_first_solution=None;self.best_objective=None
 def OnSolutionCallback(self):
  self.solutions_found+=1
  objective=_solver_metric(self.ObjectiveValue,0.0)
  if self.time_to_first_solution is None:
   self.time_to_first_solution=time.perf_counter()-self.started_at if self.started_at is not None else None
   self.objective_at_first_solution=objective
  self.best_objective=objective

def _validation_errors(items):
 normalized=[]
 for item in items or []:
  if not isinstance(item,dict):continue
  error=dict(item);code=str(error.get('code') or error.get('type') or 'VALIDATION_ERROR')
  error['code']=code
  if not error.get('message'):error['message']=str(code).replace('_',' ').capitalize()
  error.setdefault('category', 'room' if 'ROOM' in code else 'faculty' if 'FACULTY' in code else 'section' if 'SECTION' in code else 'schedule')
  normalized.append(error)
 return normalized

def validate_generation_apply(run):
 """Read-only checks shared by the validation endpoint and Apply preflight."""
 from scheduling.provenance import provenance_for_run
 errors=[]
 if run.status==GenerationRun.Status.APPLIED:
  errors.append({'code':'GENERATION_ALREADY_APPLIED','message':'Generation run has already been applied.','category':'generation'})
 elif run.status!=GenerationRun.Status.SUCCEEDED or run.solver_status not in ('OPTIMAL','FEASIBLE'):
  errors.append({'code':'INVALID_GENERATION_STATUS','message':'Generation run is not applicable.','category':'generation'})
 source_matches=fingerprint(run.source_version)==run.source_fingerprint
 if not source_matches:errors.append({'code':'SOURCE_VERSION_CHANGED','message':'The source version changed after generation.','category':'source'})
 generated_entries=run.result.get('entries',[]) if isinstance(run.result,dict) else []
 weekend_entries=[item for item in generated_entries if not is_working_day(item.get('weekday'))]
 for item in weekend_entries:
  weekday=item.get('weekday')
  errors.append({'code':WEEKEND_ERROR_CODE,'weekday':weekday,'day':WEEKDAY_NAMES[weekday] if isinstance(weekday,int) and 0<=weekday<len(WEEKDAY_NAMES) else str(weekday),'section_id':item.get('section_id'),'course_offering_id':item.get('course_offering_id'),'message':'Classes can only be scheduled Monday through Friday.','category':'schedule'})
 requirement_errors=[]
 requirements,requirement_errors=build_requirements(run.source_version,run.input_config)
 errors.extend(requirement_errors)
 candidate=validate_generated_entries(run.source_version,generated_entries,requirements,run.mode,run.input_config.get('section_ids')) if not requirement_errors else {'conflicts':[],'valid':False}
 errors.extend(candidate.get('conflicts',[]))
 if run.statistics.get('final_validation_passed') is False and not candidate.get('conflicts'):
  errors.extend(run.diagnostics.get('blocking_errors',[]) or run.diagnostics.get('errors',[]) or [{'code':'POST_VALIDATION_FAILED','message':'Generation-time hard-rule validation failed.','category':'generation'}])
 source_entries=run.source_version.entries.all()
 scope=set(run.input_config.get('section_ids') or [])
 if run.mode=='REBUILD_UNLOCKED':source_entries=source_entries.filter(Q(locked=True)|~Q(section_id__in=scope)) if scope else source_entries.filter(locked=True)
 weekend_source=list(source_entries.filter(weekday__in=WEEKEND_DAYS).values('id','weekday','section_id','course_offering_id'))
 for item in weekend_source:
  weekday=item['weekday'];errors.append({'code':WEEKEND_ERROR_CODE,'weekday':weekday,'day':WEEKDAY_NAMES[weekday],'entry_id':str(item['id']),'section_id':str(item['section_id']),'course_offering_id':str(item['course_offering_id']),'message':'A preserved source entry is scheduled on a weekend.','category':'schedule'})
 errors=_validation_errors(errors)
 provenance=provenance_for_run(run)
 return {'valid':not errors,'blocking_error_count':len(errors),'warning_count':0,'errors':errors,'conflicts':errors,'source_fingerprint_matches':source_matches,'provenance_status':provenance['provenance_status'],'generation_input_fingerprint':provenance['generation_input_fingerprint'],'current_input_fingerprint':provenance['current_input_fingerprint'],'input_fingerprint_matches':provenance['input_fingerprint_matches']}

def _candidate_key(candidate):
 return (candidate.course_offering_id,candidate.section_id,candidate.weekday,candidate.start_slot_id,candidate.occupied_slot_ids,candidate.block_length,candidate.room_id,tuple((item.faculty_id,item.role) for item in candidate.faculty),candidate.entry_type,candidate.delivery_mode)

def _reusable_generation_seed(run,groups,requirements):
 from scheduling.services.validation import validate_generated_entries
 comparable_config={key:value for key,value in run.input_config.items() if key!='max_solve_seconds'}
 prior_runs=GenerationRun.objects.filter(source_version_id=run.source_version_id,status=GenerationRun.Status.SUCCEEDED,solver_status__in=('FEASIBLE','OPTIMAL')).exclude(pk=run.pk).order_by('-completed_at')[:10]
 requirement_group={item.requirement_id:group['requirement_id'] for group in groups for item in group['requirements']}
 for prior in prior_runs:
  source_match=prior.source_fingerprint==run.source_fingerprint
  config_match={key:value for key,value in prior.input_config.items() if key!='max_solve_seconds'}==comparable_config
  if not source_match or not config_match:
   _gen_log(f'prior seed audit run_id={prior.pk} source_fingerprint_match={source_match} configuration_match={config_match} current_policy_validation=NOT_RUN reusable=False reason='+('SOURCE_FINGERPRINT_MISMATCH' if not source_match else 'CONFIGURATION_MISMATCH'))
   continue
  entries=prior.result.get('entries',[]) if isinstance(prior.result,dict) else []
  if not entries:
   _gen_log(f'prior seed audit run_id={prior.pk} source_fingerprint_match=True configuration_match=True weekly_hybrid_room_locked_validation=NOT_RUN current_final_validation=False reusable=False reason=EMPTY_RESULT')
   continue
  validation=validate_generated_entries(run.source_version,entries,requirements,run.mode,run.input_config.get('section_ids'))
  if not validation['valid']:
   _gen_log(f'prior seed audit run_id={prior.pk} source_fingerprint_match=True configuration_match=True weekly_hybrid_room_locked_validation=False current_final_validation=False reusable=False reason=CURRENT_VALIDATION_FAILED errors={len(validation.get("conflicts",[]))}')
   continue
  wanted_by_group=defaultdict(set)
  entry_groups=[]
  for item in entries:
   group_id=requirement_group.get(item.get('requirement_id'))
   if not group_id:break
   faculty=tuple((str(member.get('faculty_id')),member.get('role','PRIMARY')) for member in item.get('faculty',[]))
   key=(str(item.get('course_offering_id')),str(item.get('section_id')),item.get('weekday'),str(item.get('start_slot_id')),tuple(map(str,item.get('occupied_slot_ids',[]))),int(item.get('block_length') or 0),str(item.get('room_pool_id') or ''),faculty,item.get('entry_type'),item.get('delivery_mode'))
   wanted_by_group[group_id].add(key);entry_groups.append((group_id,key))
  else:
   candidate_maps={group_id:{_candidate_key(candidate):candidate for candidate in group['candidates'] if _candidate_key(candidate) in wanted_by_group[group_id]} for group_id,group in ((group['requirement_id'],group) for group in groups)}
   assignments=[]
   for group_id,key in entry_groups:
    candidate=candidate_maps.get(group_id,{}).get(key)
    if candidate is None:break
    assignments.append((group_id,candidate))
   else:
    expected=sum(group['occurrence_count'] for group in groups)
    counts=Counter(group_id for group_id,_candidate in assignments)
    if len(assignments)==expected and all(counts[group['requirement_id']]==group['occurrence_count'] for group in groups):
     _gen_log(f'prior seed audit run_id={prior.pk} source_fingerprint_match=True configuration_match=True weekly_hybrid_room_locked_validation=True current_final_validation=True reusable=True reason=CURRENT_VALIDATION_PASSED')
     return {'valid':True,'assignments':assignments,'nodes':0,'reason':'REUSED_VALIDATED_GENERATION','status':'REUSED_VALIDATED','strategy':'VALIDATED_PRIOR_RUN','search_elapsed':0.0,'validation_elapsed':0.0,'seed_run_id':str(prior.pk)}
  _gen_log(f'prior seed audit run_id={prior.pk} source_fingerprint_match=True configuration_match=True weekly_hybrid_room_locked_validation=True current_final_validation=True reusable=False reason=CANDIDATES_DO_NOT_MATCH_CURRENT_MODEL')
 return None

def _group_occurrences(requirements,candidates):
 buckets={}
 for requirement in requirements:
  key=(requirement.course_offering_id,requirement.section_id,requirement.entry_type,requirement.block_length,requirement.faculty,requirement.room_type_requirement,requirement.required_capacity)
  signature=tuple(sorted(_candidate_key(item) for item in candidates[requirement.requirement_id]))
  buckets.setdefault((key,signature),[]).append(requirement)
 groups=[]
 for index,((key,_signature),members) in enumerate(buckets.items()):
  group_id=f'OCCURRENCE_GROUP:{index}:{key[0]}'
  source=candidates[members[0].requirement_id]
  canonical={_candidate_key(item):replace(item,requirement_id=group_id) for item in source}
  groups.append({'requirement_id':group_id,'occurrence_count':len(members),'requirements':members,'candidates':list(canonical.values()),'section_id':members[0].section_id,'block_length':members[0].block_length,'faculty':members[0].faculty})
 return groups

def _build_occurrence_model(groups,room_group_sizes,with_assumptions=False,disabled_families=(),required_offline_section_ids=()):
 disabled=set(disabled_families);model=cp_model.CpModel();variables=[];assumptions={};assumption_descriptions={}
 def family_literal(family,description):
  if family in disabled:return None
  if not with_assumptions:return None
  literal=model.NewBoolVar(f'assumption_{len(assumption_descriptions)}');model.AddAssumption(literal);assumptions[family]=literal;assumption_descriptions[literal.Index()]={'code':family,'constraint':description};return literal
 gates={family:family_literal(family,message) for family,message in [('SECTION_NO_OVERLAP','Section classes cannot overlap.'),('FACULTY_NO_OVERLAP','A faculty member cannot teach two classes in the same period.'),('ROOM_NO_OVERLAP','A physical room cannot host two classes in the same period.') ]}
 group_gates={}
 for group_index,group in enumerate(groups):
  if group_index % 100 == 0:_gen_log(f'model build progress groups={group_index}/{len(groups)}')
  literal=family_literal('REQUIRED_OCCURRENCES',f"Select exactly {group['occurrence_count']} occurrences for {group['requirement_id']}.") if with_assumptions and 'REQUIRED_OCCURRENCES' not in disabled else None
  group_gates[group['requirement_id']]=literal
 section_resources={};faculty_resources={};room_resources={};group_variables={};offline_section_variables=defaultdict(list)
 for group in groups:
  group_vars=[]
  for candidate in group['candidates']:
   room_key='ONLINE' if candidate.delivery_mode=='ONLINE' else candidate.room_id
   variable=model.NewBoolVar(f"{group['requirement_id']}:{candidate.weekday}:{candidate.start_slot_id}:{room_key}:choice")
   variables.append((variable,candidate,group['requirement_id']));group_vars.append(variable)
   for slot_id in candidate.occupied_slot_ids:
    section_resources.setdefault((candidate.section_id,candidate.weekday,slot_id),[]).append(variable)
    for assignment in candidate.faculty:faculty_resources.setdefault((assignment.faculty_id,candidate.weekday,slot_id),[]).append(variable)
    if candidate.delivery_mode!='ONLINE':
     room_resources.setdefault((candidate.room_id,candidate.weekday,slot_id),[]).append(variable)
     offline_section_variables[str(candidate.section_id)].append(variable)
  group_variables[group['requirement_id']]=group_vars
  if 'REQUIRED_OCCURRENCES' not in disabled:
   constraint=model.Add(sum(group_vars)==group['occurrence_count'])
   if group_gates[group['requirement_id']] is not None:constraint.OnlyEnforceIf(group_gates[group['requirement_id']])
 for family,resources,capacity in [('SECTION_NO_OVERLAP',section_resources,None),('FACULTY_NO_OVERLAP',faculty_resources,None),('ROOM_NO_OVERLAP',room_resources,room_group_sizes)]:
  if family in disabled:continue
  for (resource_id,_weekday,_slot_id),resource_variables in resources.items():
   maximum=1 if capacity is None else capacity[resource_id]
   constraint=model.Add(sum(resource_variables)<=maximum)
   if gates[family] is not None:constraint.OnlyEnforceIf(gates[family])
 for section_id in required_offline_section_ids:
  model.Add(sum(offline_section_variables.get(str(section_id),[]))>=1)
 return model,variables,assumption_descriptions,group_variables

class _SeedBudgetExpired(Exception):
 pass

def _constructive_seed(groups,room_group_sizes,seed=42,node_limit=150000,required_offline_section_ids=(),time_budget_seconds=4.0):
 required_offline_section_ids=set(map(str,required_offline_section_ids))
 seed_search_started=time.perf_counter();deadline=seed_search_started+max(0.0,float(time_budget_seconds))
 def check_budget():
  if time.perf_counter()>=deadline:raise _SeedBudgetExpired
 try:
  group_by_id={group['requirement_id']:group for group in groups};remaining={key:group['occurrence_count'] for key,group in group_by_id.items()};options={}
  for group in groups:
   check_budget()
   options[group['requirement_id']]=sorted(group['candidates'],key=lambda item:hashlib.sha256(f"{seed}|{group['requirement_id']}|{item.weekday}|{item.start_slot_id}|{item.room_id}".encode()).digest())
 except _SeedBudgetExpired:
  return {'valid':False,'assignments':[],'nodes':0,'reason':'SEED_TIME_BUDGET_EXCEEDED','status':'TIMEOUT','search_elapsed':time.perf_counter()-seed_search_started,'validation_elapsed':0}
 section_used=set();faculty_used=set();room_used={};group_selected={key:set() for key in group_by_id};assignments=[];frames=[];nodes=0
 def feasible(group_id,candidate):
  check_budget()
  for slot_id in candidate.occupied_slot_ids:
   if (candidate.section_id,candidate.weekday,slot_id) in section_used:return False
   if any((assignment.faculty_id,candidate.weekday,slot_id) in faculty_used for assignment in candidate.faculty):return False
   if candidate.delivery_mode!='ONLINE' and room_used.get((candidate.room_id,candidate.weekday,slot_id),0)>=room_group_sizes[candidate.room_id]:return False
  return _candidate_key(candidate) not in group_selected[group_id]
 def occupy(candidate,value):
  for slot_id in candidate.occupied_slot_ids:
   key=(candidate.section_id,candidate.weekday,slot_id)
   if value:section_used.add(key)
   else:section_used.remove(key)
   for assignment in candidate.faculty:
    key=(assignment.faculty_id,candidate.weekday,slot_id)
    if value:faculty_used.add(key)
    else:faculty_used.remove(key)
   if candidate.delivery_mode!='ONLINE':
    key=(candidate.room_id,candidate.weekday,slot_id);room_used[key]=room_used.get(key,0)+(1 if value else -1)
    if room_used[key]==0:del room_used[key]
 def feasible_options(group_id):
  choices=[]
  for candidate in options[group_id]:
   check_budget()
   if feasible(group_id,candidate):choices.append(candidate)
  section_id=str(group_by_id[group_id]['section_id'])
  if section_id in required_offline_section_ids and not any(str(candidate.section_id)==section_id and candidate.delivery_mode!='ONLINE' for _selected_group,candidate in assignments):
   choices.sort(key=lambda candidate:candidate.delivery_mode=='ONLINE')
  return choices
 # Most production seeds do not backtrack.  Recomputing MRV by rescanning all
 # candidates for every remaining group after every placement made this common
 # case quadratic in groups.  Try a deterministic constrained-first pass;
 # retain the original MRV/backtracking algorithm below as the safe fallback.
 greedy_order=sorted(groups,key=lambda group:(len(options[group['requirement_id']])/max(1,group['occurrence_count']),-group['block_length'],-len(group['faculty']),group['requirement_id']))
 try:
  for group in greedy_order:
   check_budget();group_id=group['requirement_id']
   while remaining[group_id]>0:
    check_budget();choices=feasible_options(group_id)
    if not choices:break
    candidate=choices[0];nodes+=1
    group_selected[group_id].add(_candidate_key(candidate));occupy(candidate,True);remaining[group_id]-=1;assignments.append((group_id,candidate))
   if remaining[group_id]>0:break
  used_backtracking=any(remaining.values())
  if used_backtracking:
   # Roll back the inexpensive pass before entering the established search.
   for group_id,candidate in reversed(assignments):
    check_budget();occupy(candidate,False);group_selected[group_id].remove(_candidate_key(candidate));remaining[group_id]+=1
   assignments.clear();nodes=0
  while any(remaining.values()) and nodes<node_limit:
   check_budget()
   if not frames or frames[-1].get('assigned') is not None:
    viable=[];dead_end=False
    for group_id,count in remaining.items():
     check_budget()
     if count<=0:continue
     choices=feasible_options(group_id)
     if len(choices)<count:dead_end=True;break
     viable.append((len(choices)-count,len(choices),-group_by_id[group_id]['block_length'],-len(group_by_id[group_id]['faculty']),group_id,choices))
    if not dead_end and viable:
     _slack,_available,_block,_faculty,group_id,choices=min(viable,key=lambda item:item[:5])
     frames.append({'group_id':group_id,'choices':choices,'cursor':0,'assigned':None})
     continue
    if not frames:break
    parent=frames[-1];previous=parent['assigned'];previous_group=parent['group_id']
    if previous is None:frames.pop();continue
    occupy(previous,False);group_selected[previous_group].remove(_candidate_key(previous));remaining[previous_group]+=1;assignments.pop();parent['assigned']=None
    continue
   if not frames:break
   frame=frames[-1];group_id=frame['group_id'];candidate=None
   while frame['cursor']<len(frame['choices']) and nodes<node_limit:
    check_budget()
    option=frame['choices'][frame['cursor']];frame['cursor']+=1;nodes+=1
    if feasible(group_id,option):candidate=option;break
   if candidate is not None:
    frame['assigned']=candidate;group_selected[group_id].add(_candidate_key(candidate));occupy(candidate,True);remaining[group_id]-=1;assignments.append((group_id,candidate))
    continue
   frames.pop()
   if not frames:break
   parent=frames[-1];previous=parent['assigned'];previous_group=parent['group_id']
   if previous is not None:
    occupy(previous,False);group_selected[previous_group].remove(_candidate_key(previous));remaining[previous_group]+=1;assignments.pop();parent['assigned']=None
 except _SeedBudgetExpired:
  return {'valid':False,'assignments':[],'nodes':nodes,'reason':'SEED_TIME_BUDGET_EXCEEDED','status':'TIMEOUT','search_elapsed':time.perf_counter()-seed_search_started,'validation_elapsed':0}
 if any(remaining.values()):return {'valid':False,'assignments':[],'nodes':nodes,'reason':'SEARCH_LIMIT' if nodes>=node_limit else 'NO_CONSTRUCTIVE_ASSIGNMENT','status':'NOT_AVAILABLE'}
 seed_search_elapsed=time.perf_counter()-seed_search_started
 seed_validation_started=time.perf_counter()
 try:
  check_budget();counts=Counter(group_id for group_id,_candidate in assignments)
  selected_offline_sections=set()
  for _group_id,candidate in assignments:
   check_budget()
   if candidate.delivery_mode!='ONLINE':selected_offline_sections.add(str(candidate.section_id))
  valid=all(counts[group['requirement_id']]==group['occurrence_count'] for group in groups) and len(assignments)==sum(group['occurrence_count'] for group in groups) and required_offline_section_ids.issubset(selected_offline_sections)
 except _SeedBudgetExpired:
  return {'valid':False,'assignments':[],'nodes':nodes,'reason':'SEED_TIME_BUDGET_EXCEEDED','status':'TIMEOUT','search_elapsed':seed_search_elapsed,'validation_elapsed':time.perf_counter()-seed_validation_started}
 if not required_offline_section_ids.issubset(selected_offline_sections):
  return {'valid':False,'assignments':[],'nodes':nodes,'reason':'MISSING_REQUIRED_OFFLINE_SECTION','status':'INVALID','search_elapsed':seed_search_elapsed,'validation_elapsed':time.perf_counter()-seed_validation_started}
 validated_sections=set();validated_faculty=set();validated_rooms={};validated_group_candidates=set()
 try:
  for group_id,candidate in assignments:
   check_budget();group_candidate=(group_id,_candidate_key(candidate))
   if group_candidate in validated_group_candidates:valid=False;break
   validated_group_candidates.add(group_candidate)
   for slot_id in candidate.occupied_slot_ids:
    check_budget();section_key=(candidate.section_id,candidate.weekday,slot_id)
    if section_key in validated_sections:valid=False;break
    validated_sections.add(section_key)
    for faculty_assignment in candidate.faculty:
     faculty_key=(faculty_assignment.faculty_id,candidate.weekday,slot_id)
     if faculty_key in validated_faculty:valid=False;break
     validated_faculty.add(faculty_key)
    if candidate.delivery_mode!='ONLINE':
     room_key=(candidate.room_id,candidate.weekday,slot_id);validated_rooms[room_key]=validated_rooms.get(room_key,0)+1
     if validated_rooms[room_key]>room_group_sizes[candidate.room_id]:valid=False;break
   if not valid:break
 except _SeedBudgetExpired:
  return {'valid':False,'assignments':[],'nodes':nodes,'reason':'SEED_TIME_BUDGET_EXCEEDED','status':'TIMEOUT','search_elapsed':seed_search_elapsed,'validation_elapsed':time.perf_counter()-seed_validation_started}
 return {'valid':valid,'assignments':assignments if valid else [],'nodes':nodes,'reason':'VALIDATED' if valid else 'SEED_VALIDATION_FAILED','status':'CONSTRUCTED' if valid else 'INVALID','strategy':'MRV_BACKTRACK' if used_backtracking else 'GREEDY','search_elapsed':seed_search_elapsed,'validation_elapsed':time.perf_counter()-seed_validation_started}

def _required_offline_sections(version,requirements,mode,scope):
 from academics.models import Section
 from academics.delivery import get_section_delivery_policy_record
 requirement_section_ids={str(item.section_id) for item in requirements}
 sections={str(section.pk):section for section in Section.objects.filter(pk__in=requirement_section_ids).select_related('semester__session').prefetch_related('period_delivery_policies')}
 official_hybrid={section_id for section_id,section in sections.items() if (policy:=get_section_delivery_policy_record(section,version.timetable.semester)) and policy.active and policy.mode=='HYBRID' and policy.source=='OFFICIAL_TIMETABLE'}
 if not official_hybrid:return set()
 preserved=version.entries.all()
 scope=set(map(str,scope or []))
 if mode=='REBUILD_UNLOCKED':
  preserved=preserved.filter(Q(locked=True)|~Q(section_id__in=scope)) if scope else preserved.filter(locked=True)
 already_physical=set()
 for entry in preserved.filter(section_id__in=official_hybrid,delivery_mode='OFFLINE',room_id__isnull=False).select_related('section__semester__session').prefetch_related('section__period_delivery_policies'):
  policy=get_section_delivery_policy_record(entry.section,version.timetable.semester)
  if policy and entry.weekday==policy.offline_weekday:already_physical.add(str(entry.section_id))
 return official_hybrid-already_physical

def run_generation(run,validated_requirements=None):
 started=time.perf_counter()
 timings={}
 max_solve_seconds=int(run.input_config.get('max_solve_seconds',120))
 _gen_log(f'generation service start run_id={run.pk} max_solve_seconds={max_solve_seconds}')
 persistence_started=time.perf_counter()
 run.status=GenerationRun.Status.RUNNING;run.started_at=timezone.now();run.save(update_fields=['status','started_at'])
 _gen_log(f'persistence start/end elapsed={time.perf_counter()-persistence_started:.3f}s state=RUNNING')

 requirements_started=time.perf_counter();_gen_log('load requirements start')
 if validated_requirements is None:
  reqs,errors=build_requirements(run.source_version,run.input_config)
  errors += fixed_conflicts(run.source_version,run.input_config.get('mode','FILL_GAPS'),run.input_config.get('section_ids'))
  errors += scope_errors(run.source_version,run.input_config)
  errors += resource_capacity_diagnostics(run.source_version,reqs,run.input_config)
 else:reqs,errors=validated_requirements,[]
 _gen_log(f'load requirements end elapsed={time.perf_counter()-requirements_started:.3f}s count={len(reqs)} errors={len(errors)} reused_preflight={validated_requirements is not None}')
 timings['requirement_construction_seconds']=time.perf_counter()-requirements_started
 if errors:return _finish(run,GenerationRun.Status.INFEASIBLE,'PRECHECK_FAILED',{'errors':errors,'warnings':[]},started,{'requirement_count':len(reqs)})

 candidates_started=time.perf_counter();_gen_log('candidate build start')
 candidate_profile={}
 candidates,diagnostics=build_candidates(run.source_version,reqs,run.input_config,candidate_profile)
 candidate_count=sum(len(items) for items in candidates.values())
 room_count=len({candidate.room_id for items in candidates.values() for candidate in items if candidate.room_id})
 slot_count=len({slot for items in candidates.values() for candidate in items for slot in candidate.occupied_slot_ids})
 faculty_count=len({assignment.faculty_id for requirement in reqs for assignment in requirement.faculty})
 section_count=len({requirement.section_id for requirement in reqs})
 _gen_log(f'candidate build end elapsed={time.perf_counter()-candidates_started:.3f}s count={candidate_count} rooms={room_count} slots={slot_count} faculty={faculty_count} sections={section_count} diagnostics={len(diagnostics)} average_per_requirement={candidate_profile.get("average_candidates_per_requirement",0):.1f} p50={candidate_profile.get("p50_candidates_per_requirement",0)} p95={candidate_profile.get("p95_candidates_per_requirement",0)} max={candidate_profile.get("max_candidates_per_requirement",0)}')
 timings['candidate_construction_seconds']=time.perf_counter()-candidates_started
 timings['room_pool_construction_seconds']=candidate_profile.get('room_pool_construction_seconds',0)
 timings['hybrid_candidate_construction_seconds']=candidate_profile.get('hybrid_candidate_construction_seconds',0)
 _gen_log(f'candidate profile top30={json.dumps(candidate_profile.get("top_30",[]),separators=(",",":"))}')
 candidate_warnings=[item for item in diagnostics if item.get('severity')=='WARNING' or item.get('code')=='ROOM_ELIGIBILITY_EXCEPTION_APPLIED']
 candidate_errors=[item for item in diagnostics if item not in candidate_warnings]
 if candidate_errors:
  candidate_stats={'stage':'CANDIDATE_BUILD','requirement_count':len(reqs),'candidate_count':candidate_count,'zero_candidate_count':sum(not items for items in candidates.values()),'room_count':room_count,'slot_count':slot_count,'faculty_count':faculty_count,'section_count':section_count}
  return _finish(run,GenerationRun.Status.INFEASIBLE,'PRECHECK_FAILED',{'errors':candidate_errors,'warnings':candidate_warnings},started,candidate_stats)

 model_started=time.perf_counter();_gen_log('model build start')
 requirement_profile={row['requirement_id']:row for row in candidate_profile.get('requirements',[])}
 groups=_group_occurrences(reqs,candidates)
 groups.sort(key=lambda group:(len(group['candidates']),min((requirement_profile.get(req.requirement_id,{}).get('eligible_room_count',0) for req in group['requirements']),default=0),min((requirement_profile.get(req.requirement_id,{}).get('eligible_start_count',0) for req in group['requirements']),default=0),-group['block_length'],-len(group['faculty']),group['requirement_id']))
 group_by_id={group['requirement_id']:group for group in groups}
 room_group_sizes=candidate_profile['_room_group_sizes']
 hybrid_requirement_ids={key for key,row in requirement_profile.items() if row.get('delivery_policy')=='HYBRID'}
 hybrid_groups=[group for group in groups if any(item.requirement_id in hybrid_requirement_ids for item in group['requirements'])]
 hybrid_candidate_metrics={'offering_count':len({item.course_offering_id for group in hybrid_groups for item in group['requirements']}),'occurrence_group_count':len(hybrid_groups),'candidate_variables':sum(len(group['candidates']) for group in hybrid_groups),'online_candidate_variables':sum(candidate.delivery_mode=='ONLINE' for group in hybrid_groups for candidate in group['candidates']),'offline_candidate_variables':sum(candidate.delivery_mode!='ONLINE' for group in hybrid_groups for candidate in group['candidates']),'candidates_per_group_max':max((len(group['candidates']) for group in hybrid_groups),default=0),'candidates_per_group_average':sum(len(group['candidates']) for group in hybrid_groups)/len(hybrid_groups) if hybrid_groups else 0,'candidates_per_group_median':sorted(len(group['candidates']) for group in hybrid_groups)[len(hybrid_groups)//2] if hybrid_groups else 0}
 required_offline_section_ids=_required_offline_sections(run.source_version,reqs,run.mode,run.input_config.get('section_ids'))
 model,variables,assumption_descriptions,group_variables=_build_occurrence_model(groups,room_group_sizes,required_offline_section_ids=required_offline_section_ids)
 timings['model_construction_seconds']=time.perf_counter()-model_started
 decision_variables=[variable for variable,_candidate,_group_id in variables]
 raw_candidate_count=candidate_count
 grouped_candidate_count=len(variables)
 room_pool_choice_variable_count=sum(candidate.delivery_mode!='ONLINE' for _variable,candidate,_group_id in variables)
 online_choice_variable_count=len(variables)-room_pool_choice_variable_count
 unique_time_placements=len({(group_id,candidate.weekday,candidate.start_slot_id,candidate.occupied_slot_ids) for _variable,candidate,group_id in variables})
 section_constraint_count=len({(candidate.section_id,candidate.weekday,slot_id) for _variable,candidate,_group_id in variables for slot_id in candidate.occupied_slot_ids})
 faculty_constraint_count=len({(assignment.faculty_id,candidate.weekday,slot_id) for _variable,candidate,_group_id in variables for assignment in candidate.faculty for slot_id in candidate.occupied_slot_ids})
 room_pool_constraint_count=len({(candidate.room_id,candidate.weekday,slot_id) for _variable,candidate,_group_id in variables if candidate.delivery_mode!='ONLINE' for slot_id in candidate.occupied_slot_ids})
 hybrid_constraint_count=len(required_offline_section_ids)
 _gen_log('previous-run lookup start');reuse_started=time.perf_counter();_gen_log('seed validation start');seed_result=_reusable_generation_seed(run,groups,reqs)
 timings['previous_run_lookup_seconds']=time.perf_counter()-reuse_started
 _gen_log(f'seed validation end elapsed={time.perf_counter()-reuse_started:.3f}s reusable={seed_result is not None}')
 _gen_log(f'previous-run lookup end elapsed={time.perf_counter()-reuse_started:.3f}s reusable={seed_result is not None}')
 constructive_started=time.perf_counter()
 if seed_result is None:
  _gen_log('constructive seed start')
  seed_result=_constructive_seed(groups,room_group_sizes,seed=int(run.input_config.get('random_seed',42)),required_offline_section_ids=required_offline_section_ids,time_budget_seconds=min(5.0,max(0.1,float(run.input_config.get('constructive_seed_budget_seconds',4.0)))))
  _gen_log(f'constructive seed end elapsed={time.perf_counter()-constructive_started:.3f}s status={seed_result.get("status","INVALID")} reason={seed_result.get("reason")}')
 seed_elapsed=time.perf_counter()-constructive_started
 timings['constructive_seed_seconds']=seed_elapsed
 timings['seed_search_seconds']=seed_result.get('search_elapsed',seed_elapsed)
 timings['seed_validation_seconds']=seed_result.get('validation_elapsed',0)
 seed_hint_count=0
 seed_mapping_started=time.perf_counter()
 selected_keys={(group_id,_candidate_key(candidate)) for group_id,candidate in seed_result['assignments']}
 timings['seed_hint_mapping_seconds']=time.perf_counter()-seed_mapping_started
 hint_creation_started=time.perf_counter()
 if seed_result['valid']:
  for hint_index,(variable,candidate,group_id) in enumerate(variables):
   model.AddHint(variable,int((group_id,_candidate_key(candidate)) in selected_keys));seed_hint_count+=1
   if hint_index and hint_index % 100000 == 0:_gen_log(f'hints progress variables={hint_index}/{len(variables)}')
 timings['cp_sat_hint_creation_seconds']=time.perf_counter()-hint_creation_started
 _gen_log(f'model build end elapsed={timings["model_construction_seconds"]:.3f}s occurrence_groups={len(groups)} requirements={len(reqs)} raw_candidates={raw_candidate_count} grouped_candidates={grouped_candidate_count} variables={len(model.Proto().variables)} constraints={len(model.Proto().constraints)}')
 _gen_log(f'hints end elapsed={timings["cp_sat_hint_creation_seconds"]:.3f}s count={seed_hint_count}; seed_status={seed_result.get("status","INVALID")} seed_elapsed={seed_elapsed:.3f}s')
 proto=model.Proto()
 solver_budget=float(max_solve_seconds)
 phase1_budget=float(max_solve_seconds)
 phase1_solver=cp_model.CpSolver();phase1_solver.parameters.max_time_in_seconds=phase1_budget;phase1_solver.parameters.random_seed=int(run.input_config.get('random_seed',42))
 phase1_telemetry=_FirstSolutionTelemetry()
 _gen_log(f'phase 1 solve start requested_max_solve_seconds={max_solve_seconds} actual_phase1_max_time_in_seconds={phase1_solver.parameters.max_time_in_seconds:.3f} global_deadline_if_any=None remaining_request_budget_if_any=None phase1_start_monotonic_pending occurrence_groups={len(groups)} hints={seed_hint_count}')
 phase1_started=time.perf_counter();phase1_telemetry.started_at=phase1_started
 _gen_log(f'phase 1 budget confirmation requested_max_solve_seconds={max_solve_seconds} actual_phase1_max_time_in_seconds={phase1_solver.parameters.max_time_in_seconds:.3f} global_deadline_if_any=None remaining_request_budget_if_any=None phase1_start_monotonic={phase1_started:.6f}')
 phase1_status=phase1_solver.Solve(model,phase1_telemetry);phase1_ended=time.perf_counter();phase1_elapsed=phase1_ended-phase1_started
 phase1_wall_time=_solver_metric(phase1_solver.WallTime,None)
 timings['phase1_solver_seconds']=phase1_elapsed
 phase1_name={cp_model.OPTIMAL:'OPTIMAL',cp_model.FEASIBLE:'FEASIBLE',cp_model.INFEASIBLE:'INFEASIBLE',cp_model.MODEL_INVALID:'MODEL_INVALID'}.get(phase1_status,'UNKNOWN')
 phase1_best_bound=_solver_metric(phase1_solver.BestObjectiveBound,None)
 _gen_log(f'phase 1 solve end requested_max_solve_seconds={max_solve_seconds} actual_phase1_max_time_in_seconds={phase1_solver.parameters.max_time_in_seconds:.3f} global_deadline_if_any=None remaining_request_budget_if_any=None phase1_start_monotonic={phase1_started:.6f} phase1_end_monotonic={phase1_ended:.6f} elapsed={phase1_elapsed:.3f}s wall_time={phase1_wall_time} status={phase1_name} stop_search_calls=0 callback_stop_conditions=none solutions_found={phase1_telemetry.solutions_found} time_to_first_solution={phase1_telemetry.time_to_first_solution} objective_at_first_solution={phase1_telemetry.objective_at_first_solution} best_objective={phase1_telemetry.best_objective} best_bound={phase1_best_bound}')
 if phase1_status not in (cp_model.OPTIMAL,cp_model.FEASIBLE) and not (phase1_status==cp_model.UNKNOWN and seed_result['valid']):
  infeasible_core=[];diagnostic_model_stats={}
  if phase1_status==cp_model.INFEASIBLE:
   diagnostic_model,diagnostic_variables,diagnostic_descriptions,_diagnostic_groups=_build_occurrence_model(groups,room_group_sizes,with_assumptions=True)
   diagnostic_solver=cp_model.CpSolver();diagnostic_solver.parameters.max_time_in_seconds=min(5.0,max(0.1,max_solve_seconds/10));diagnostic_started=time.perf_counter();diagnostic_status=diagnostic_solver.Solve(diagnostic_model);diagnostic_elapsed=time.perf_counter()-diagnostic_started
   if diagnostic_status==cp_model.INFEASIBLE:
    for literal_index in diagnostic_solver.SufficientAssumptionsForInfeasibility():
     assumption=diagnostic_descriptions.get(literal_index if literal_index>=0 else -literal_index-1)
     if assumption:infeasible_core.append(assumption)
   diagnostic_model_stats={'diagnostic_status':diagnostic_solver.StatusName(diagnostic_status),'diagnostic_elapsed':diagnostic_elapsed,'diagnostic_variable_count':len(diagnostic_model.Proto().variables),'diagnostic_constraint_count':len(diagnostic_model.Proto().constraints)}
  binary_propagations=_solver_metric(lambda:phase1_solver.num_binary_propagations);integer_propagations=_solver_metric(lambda:phase1_solver.num_integer_propagations);phase1_response=_solver_metric(phase1_solver.ResponseProto,None)
  application=GenerationRun.Status.INFEASIBLE if phase1_status==cp_model.INFEASIBLE else GenerationRun.Status.FAILED
  run.status=application;run.solver_status=phase1_name;run.result={'entries':[]};run.objective_score=None
  run.statistics={'stage':'CP_SAT','solver_invoked':True,'requested_solve_seconds':max_solve_seconds,'phase1_status':phase1_name,'phase1_wall_time':phase1_wall_time,'phase2_status':'NOT_RUN','phase2_wall_time':0,'requirement_count':len(reqs),'occurrence_group_count':len(groups),'candidate_count':grouped_candidate_count,'raw_candidate_count':raw_candidate_count,'grouped_candidate_count':grouped_candidate_count,'variable_count':len(proto.variables),'candidate_decision_variable_count':len(variables),'candidate_room_variable_count':room_pool_choice_variable_count,'online_candidate_variable_count':online_choice_variable_count,'placement_variable_count':0,'unique_time_placement_count':unique_time_placements,'assumption_variable_count':0,'constraint_count':len(proto.constraints),'objective_term_count':0,'generated_entry_count':0,'constructive_seed_validated':seed_result['valid'],'constructive_seed_reason':seed_result['reason'],'constructive_seed_nodes':seed_result['nodes'],'constructive_seed_elapsed':seed_elapsed,'cp_sat_hint_count':seed_hint_count,'fixed_entry_count':run.source_version.entries.count(),'wall_time':phase1_wall_time,'user_time':getattr(phase1_response,'user_time',None),'num_conflicts':_solver_metric(phase1_solver.NumConflicts),'num_branches':_solver_metric(phase1_solver.NumBranches),'num_booleans':_solver_metric(phase1_solver.NumBooleans),'num_binary_propagations':binary_propagations,'num_integer_propagations':integer_propagations,'num_propagations':binary_propagations+integer_propagations,'response_stats':_solver_metric(phase1_solver.ResponseStats,''),'infeasible_core_count':len(infeasible_core),**diagnostic_model_stats}
  run.statistics.update({'phase1_requested_max_time_seconds':max_solve_seconds,'phase1_actual_max_time_seconds':phase1_solver.parameters.max_time_in_seconds,'phase1_start_monotonic':phase1_started,'phase1_end_monotonic':phase1_ended,'phase1_elapsed_seconds':phase1_elapsed,'phase1_solutions_found':phase1_telemetry.solutions_found,'phase1_time_to_first_solution':phase1_telemetry.time_to_first_solution,'phase1_objective_at_first_solution':phase1_telemetry.objective_at_first_solution,'phase1_best_objective':phase1_telemetry.best_objective,'phase1_best_bound':phase1_best_bound,'phase1_stop_search_calls':0,'phase1_callback_stop_conditions':[],'phase1_objective_term_count':0})
  if phase1_name=='UNKNOWN':run.diagnostics={'errors':[{'code':'TIME_LIMIT','message':'No feasible timetable was found within the solver time limit.'}],'warnings':candidate_warnings}
  elif phase1_name=='INFEASIBLE':run.diagnostics={'errors':[{'code':'INFEASIBLE_CORE','message':'CP-SAT proved the model infeasible. Diagnostic-only assumptions were used to identify a sufficient infeasibility core.','core':infeasible_core}],'warnings':[]}
  else:run.diagnostics={'errors':[{'code':phase1_name,'message':'No feasible timetable was found.'}],'warnings':candidate_warnings}
  timings['total_service_seconds']=time.perf_counter()-started
  run.statistics.update({'constructive_seed_status':seed_result.get('status','INVALID'),'seed_elapsed_seconds':seed_elapsed,'seed_run_id':seed_result.get('seed_run_id'),'timings':timings,'constraint_families':{'section_no_overlap':section_constraint_count,'faculty_no_overlap':faculty_constraint_count,'room_pool_capacity':room_pool_constraint_count,'hybrid_offline_requirement':hybrid_constraint_count,'exact_occurrence_count':len(groups)},'room_pool_count':len(room_group_sizes),'hybrid_model':hybrid_candidate_metrics,'offline_candidate_variable_count':room_pool_choice_variable_count,'online_candidate_variable_count':online_choice_variable_count,'blocking_error_count':0,'warning_count':len(candidate_warnings)})
  _gen_log('result persistence start stage=phase1_terminal')
  result_persistence_started=time.perf_counter()
  run.completed_at=timezone.now();run.save();record('GENERATION_INFEASIBLE',run.created_by,timetable=run.timetable,version=run.source_version,entity_type='GenerationRun',entity_id=run.id,metadata={'solver_status':phase1_name})
  timings['result_persistence_seconds']=time.perf_counter()-result_persistence_started
  timings['total_service_seconds']=time.perf_counter()-started
  run.statistics['timings']=timings;run.save(update_fields=['statistics'])
  _gen_log(f'result persistence end elapsed={timings["result_persistence_seconds"]:.3f}s')
  _gen_log(f'generation ended after phase 1 status={phase1_name} elapsed={time.perf_counter()-started:.3f}s')
  return run
 phase1_cp_has_solution=phase1_status in (cp_model.OPTIMAL,cp_model.FEASIBLE)
 if phase1_cp_has_solution:
  phase1_values={variable.Index():phase1_solver.Value(variable) for variable in decision_variables}
  phase1_selected=[candidate for variable,candidate,_group_id in variables if phase1_values.get(variable.Index(),0)]
 else:
  phase1_values={variable.Index():int((group_id,_candidate_key(candidate)) in selected_keys) for variable,candidate,group_id in variables}
  phase1_selected=[candidate for _group_id,candidate in seed_result['assignments']]
 model.ClearHints()
 for variable in decision_variables:model.AddHint(variable,phase1_values[variable.Index()])
 objective_started=time.perf_counter();_gen_log('objective context load start')
 preferred_room_groups={row['course_offering_id']:row['preferred_room_group_id'] for row in candidate_profile.get('requirements',[]) if row.get('preferred_room_candidate_count') and row.get('preferred_room_group_id')}
 context_started=time.perf_counter();objective_context=build_objective_context(run.source_version,candidate_profile['_room_group_by_room'],candidate_profile['_room_slot_orders'],preferred_room_groups=preferred_room_groups)
 _gen_log(f'objective context load end elapsed={time.perf_counter()-context_started:.3f}s entries={sum(len(items) for items in objective_context["entries_by_offering"].values())} offerings={len(objective_context["entries_by_offering"])} faculty_day_keys={len(objective_context["entries_by_faculty_day"])}')
 _gen_log('objective coefficient creation start')
 coefficient_started=time.perf_counter();objective_variables=[];objective_coefficients=[]
 for objective_index,(variable,candidate,_group_id) in enumerate(variables):
  coefficient=int(candidate_score(candidate,run.source_version,run.input_config,objective_context))
  if coefficient:
   objective_variables.append(variable);objective_coefficients.append(coefficient)
  if objective_index and objective_index % 100000 == 0:_gen_log(f'objective build progress variables={objective_index}/{len(variables)} elapsed={time.perf_counter()-coefficient_started:.1f}s')
 _gen_log(f'objective coefficient creation end elapsed={time.perf_counter()-coefficient_started:.3f}s terms={len(objective_variables)} skipped_zero={len(variables)-len(objective_variables)} score_cache_entries={len(objective_context["base_score_cache"])}')
 expression_started=time.perf_counter()
 if objective_variables:model.Maximize(cp_model.LinearExpr.weighted_sum(objective_variables,objective_coefficients))
 else:model.Maximize(0)
 profile=objective_context['objective_profile']
 objective_elapsed=time.perf_counter()-objective_started
 timings['objective_and_final_model_construction_seconds']=objective_elapsed
 timings['model_build_total_seconds']=timings['model_construction_seconds']+timings['seed_hint_mapping_seconds']+timings['cp_sat_hint_creation_seconds']+objective_elapsed
 _gen_log(f'objective sub-stages seconds room_preference={profile["room_preference"]:.3f} course_spread={profile["course_spread"]:.3f} section_gaps={profile["section_gaps"]:.3f} faculty_gaps={profile["faculty_gaps"]:.3f} daily_balance={profile["daily_balance"]:.3f} other_penalties={profile["other_penalties"]:.3f} final_expression={time.perf_counter()-expression_started:.3f} auxiliary_variables=0 auxiliary_constraints=0')
 _gen_log(f'objective creation end elapsed={objective_elapsed:.3f}s')
 proto=model.Proto()
 _gen_log(f'model build end elapsed={time.perf_counter()-model_started:.3f}s vars={len(proto.variables)} candidate_choice_vars={len(variables)} unique_time_placements={unique_time_placements} separate_time_vars=0 constraints={len(proto.constraints)} objective_terms={len(objective_variables)} objective_auxiliary_vars=0 objective_auxiliary_constraints=0')

 phase1_actual_elapsed=phase1_elapsed
 phase2_budget=max(0.0,solver_budget-phase1_actual_elapsed)
 solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=phase2_budget;solver.parameters.random_seed=int(run.input_config.get('random_seed',42))
 _gen_log(f'phase 2 solve start limit={phase2_budget:.3f}s requested_total={max_solve_seconds}s')
 solver_started=time.perf_counter();status=solver.Solve(model) if phase2_budget>0 else cp_model.UNKNOWN;solver_elapsed=time.perf_counter()-solver_started
 phase2_wall_time=_solver_metric(solver.WallTime,0) if phase2_budget>0 else 0
 timings['phase2_solver_seconds']=solver_elapsed
 timings['cp_sat_actual_seconds']=phase1_elapsed+solver_elapsed
 _gen_log(f'phase 2 solve end elapsed={solver_elapsed:.3f}s wall_time={phase2_wall_time} status={solver.StatusName(status) if phase2_budget>0 else "SKIPPED"}')
 mapping={cp_model.OPTIMAL:('OPTIMAL',GenerationRun.Status.SUCCEEDED),cp_model.FEASIBLE:('FEASIBLE',GenerationRun.Status.SUCCEEDED),cp_model.INFEASIBLE:('INFEASIBLE',GenerationRun.Status.INFEASIBLE),cp_model.MODEL_INVALID:('MODEL_INVALID',GenerationRun.Status.FAILED)}
 phase2_status=mapping.get(status,('UNKNOWN',GenerationRun.Status.FAILED))[0] if phase2_budget>0 else 'NOT_RUN'
 has_phase2_solution=phase2_budget>0 and status in (cp_model.OPTIMAL,cp_model.FEASIBLE)
 solver_status,application=mapping.get(status,('UNKNOWN',GenerationRun.Status.FAILED)) if has_phase2_solution else ('FEASIBLE',GenerationRun.Status.SUCCEEDED)
 infeasible_core=[]

 extraction_started=time.perf_counter();allocation_started=extraction_started;_gen_log('room allocation start')
 selected=[candidate for variable,candidate,_group_id in variables if solver.Value(variable)] if has_phase2_solution else phase1_selected
 occurrence_indexes={group_id:0 for group_id in group_by_id}
 expanded_selected=[]
 for candidate in selected:
  group=group_by_id[candidate.requirement_id];member_index=occurrence_indexes[candidate.requirement_id];occurrence_indexes[candidate.requirement_id]+=1
  expanded_selected.append(replace(candidate,requirement_id=group['requirements'][member_index].requirement_id))
 selected=expanded_selected
 selected.sort(key=lambda candidate:(candidate.weekday, candidate_profile['_room_slot_orders'].get(candidate.start_slot_id,0),candidate.requirement_id))
 from academics.models import CourseOffering
 from rooms.models import Room,RoomAvailability,RoomEligibilityException
 from rooms.services.eligibility import room_eligibility_error
 requirement_by_id={item.requirement_id:item for item in reqs}
 allocation_offerings={str(item.pk):item for item in CourseOffering.objects.filter(pk__in={item.course_offering_id for item in reqs}).select_related('course','section','section__program','preferred_room')}
 allocation_sections={str(item.pk):item for item in Section.objects.filter(pk__in={item.section_id for item in reqs}).select_related('program')}
 pool_room_ids={room_id for members in candidate_profile['_room_group_members'].values() for room_id in members}
 allocation_rooms={str(item.pk):item for item in Room.objects.filter(pk__in=pool_room_ids).select_related('reserved_program')}
 allocation_exceptions=list(RoomEligibilityException.objects.filter(room_id__in=pool_room_ids,active=True).select_related('program','course'))
 allocation_blocked={(str(item.room_id),item.weekday,str(item.time_slot_id)) for item in RoomAvailability.objects.filter(room_id__in=pool_room_ids,status__in=('BLOCKED','MAINTENANCE'))}
 result=[];allocation_errors=[];jobs_by_bucket=defaultdict(list);allocation_eligibility_cache={};matching_edge_count=0
 fixed_room_occupancy=fixed_occupancy(run.source_version,run.mode,run.input_config.get('section_ids'))[2]
 fixed_room_slots={(room_id,day,slot_id) for (room_id,day,slot_id),ids in fixed_room_occupancy.items() if ids}
 for candidate in selected:
  if candidate.delivery_mode=='ONLINE':
   result.append(candidate_dict(candidate)|{'room_id':None,'delivery_mode':'ONLINE','origin':'GENERATED'});continue
  group_rooms=candidate_profile['_room_group_members'][candidate.room_id]
  req=requirement_by_id[candidate.requirement_id];offering=allocation_offerings.get(candidate.course_offering_id);section=allocation_sections.get(candidate.section_id)
  eligible_room_ids=[]
  for candidate_room_id in group_rooms:
   room=allocation_rooms.get(candidate_room_id)
   eligibility_key=(candidate_room_id,candidate.course_offering_id,candidate.section_id,req.room_type_requirement,req.entry_type,candidate.weekday,candidate.occupied_slot_ids)
   if eligibility_key not in allocation_eligibility_cache:
    allocation_eligibility_cache[eligibility_key]=room_eligibility_error(room,offering,section,required_room_type=req.room_type_requirement,activity_type=req.entry_type,weekday=candidate.weekday,slot_ids=candidate.occupied_slot_ids,blocked_slots=allocation_blocked,occupied_slots=fixed_room_slots,exceptions=allocation_exceptions)
   error=allocation_eligibility_cache[eligibility_key]
   if error:continue
   eligible_room_ids.append(candidate_room_id)
  if not eligible_room_ids:
   allocation_errors.append({'code':'ROOM_ASSIGNMENT_FAILED','severity':'ERROR','requirement_id':candidate.requirement_id,'course_offering_id':candidate.course_offering_id,'section_id':candidate.section_id,'weekday':candidate.weekday,'slot_ids':list(candidate.occupied_slot_ids),'room_pool_id':candidate.room_id,'message':f'No canonically eligible room exists in pool {candidate.room_id} for this complete block.'})
   continue
  preferred_room_id=str(offering.preferred_room_id) if offering and offering.preferred_room_id else None
  eligible_room_ids.sort(key=lambda room_id:(room_id!=preferred_room_id,room_id))
  jobs_by_bucket[(candidate.weekday,candidate.room_id)].append({'candidate':candidate,'eligible_rooms':eligible_room_ids})
 room_orders=candidate_profile['_room_slot_orders']
 room_matching_started=time.perf_counter()
 for (weekday,pool_id),jobs in jobs_by_bucket.items():
  # Block-aware bipartite assignment: every job has edges only to rooms that
  # pass canonical policy/availability checks for its full contiguous window.
  jobs.sort(key=lambda job:(room_orders.get(job['candidate'].start_slot_id,0),len(job['eligible_rooms']),job['candidate'].requirement_id))
  matching_edge_count+=sum(len(job['eligible_rooms']) for job in jobs)
  assigned={};used_slots=defaultdict(set)
  def match_job(index):
   if index>=len(jobs):return True
   job=jobs[index];candidate=job['candidate'];window=set(candidate.occupied_slot_ids)
   for room_id in job['eligible_rooms']:
    if window & used_slots[room_id]:continue
    assigned[index]=room_id;used_slots[room_id].update(window)
    if match_job(index+1):return True
    used_slots[room_id].difference_update(window);assigned.pop(index,None)
   return False
  if not match_job(0):
   for job in jobs:
    candidate=job['candidate']
    allocation_errors.append({'code':'ROOM_ASSIGNMENT_FAILED','severity':'ERROR','requirement_id':candidate.requirement_id,'course_offering_id':candidate.course_offering_id,'section_id':candidate.section_id,'weekday':weekday,'slot_ids':list(candidate.occupied_slot_ids),'room_pool_id':pool_id,'message':f'Eligible physical rooms cannot be matched across the selected blocks in pool {pool_id}.'})
   continue
  for index,job in enumerate(jobs):
   candidate=job['candidate'];room_id=assigned[index]
   result.append(candidate_dict(replace(candidate,room_id=room_id))|{'delivery_mode':'OFFLINE','origin':'GENERATED','room_pool_id':pool_id})
 timings['room_matching_seconds']=time.perf_counter()-room_matching_started
 timings['physical_entries_matched']=sum(1 for item in result if item['delivery_mode']=='OFFLINE' and item.get('origin')=='GENERATED')
 timings['room_matching_graph_edges']=matching_edge_count
 if not allocation_errors:
  allocation_finished=time.perf_counter()
  timings['physical_room_assignment_seconds']=allocation_finished-allocation_started
  validation_started=allocation_finished
  _gen_log('final validation start')
  final_validation=validate_generated_entries(run.source_version,result,reqs,run.mode,run.input_config.get('section_ids'))
  allocation_errors.extend(final_validation['conflicts'])
  timings['final_validation_seconds']=time.perf_counter()-validation_started
 else:
  timings['physical_room_assignment_seconds']=time.perf_counter()-allocation_started
  timings['final_validation_seconds']=0
  final_validation={'valid':False,'error_count':len(allocation_errors),'conflicts':allocation_errors}
 final_validation_passed=final_validation['valid'] and not allocation_errors
 if not final_validation_passed:
  application=GenerationRun.Status.FAILED;solver_status='POST_VALIDATION_FAILED'
 objective_score=float(solver.ObjectiveValue()) if has_phase2_solution else None
 extraction_elapsed=time.perf_counter()-extraction_started
 _gen_log(f'final validation end elapsed={timings.get("final_validation_seconds",0):.3f}s valid={final_validation_passed} blockers={len(allocation_errors)}')
 _gen_log(f'room allocation end elapsed={timings.get("physical_room_assignment_seconds",0):.3f}s entries={len(result)}')

 persistence_started=time.perf_counter();_gen_log('persistence start')
 online_periods=sum(item['block_length'] for item in result if item['delivery_mode']=='ONLINE');offline_periods=sum(item['block_length'] for item in result if item['delivery_mode']=='OFFLINE')
 metric_solver=solver if has_phase2_solution else phase1_solver
 binary_propagations=_solver_metric(lambda:phase1_solver.num_binary_propagations)+(_solver_metric(lambda:solver.num_binary_propagations) if has_phase2_solution else 0);integer_propagations=_solver_metric(lambda:phase1_solver.num_integer_propagations)+(_solver_metric(lambda:solver.num_integer_propagations) if has_phase2_solution else 0)
 response_proto=_solver_metric(metric_solver.ResponseProto,None)
 timings['post_processing_seconds']=time.perf_counter()-extraction_started
 run.status=application;run.solver_status=solver_status;run.result={'entries':result};run.generation_validation={'valid':final_validation_passed,'blocker_count':len(allocation_errors),'validated_at':timezone.now().isoformat(),'errors':_validation_errors(allocation_errors)};run.statistics={'stage':'CP_SAT','solver_invoked':True,'requested_solve_seconds':max_solve_seconds,'actual_cp_sat_seconds':phase1_elapsed+solver_elapsed,'phase1_status':phase1_name,'phase1_wall_time':phase1_wall_time,'phase2_status':phase2_status,'phase2_wall_time':phase2_wall_time,'requirement_count':len(reqs),'occurrence_group_count':len(groups),'candidate_count':grouped_candidate_count,'raw_candidate_count':raw_candidate_count,'grouped_candidate_count':grouped_candidate_count,'variable_count':len(proto.variables),'candidate_decision_variable_count':len(variables),'candidate_room_variable_count':room_pool_choice_variable_count,'online_candidate_variable_count':online_choice_variable_count,'placement_variable_count':0,'unique_time_placement_count':unique_time_placements,'assumption_variable_count':0,'constraint_count':len(proto.constraints),'constraint_families':{'section_no_overlap':section_constraint_count,'faculty_no_overlap':faculty_constraint_count,'room_pool_capacity':room_pool_constraint_count,'hybrid_offline_requirement':hybrid_constraint_count,'exact_occurrence_count':len(groups)},'objective_term_count':len(objective_variables),'generated_entry_count':len(result),'final_validation_passed':final_validation_passed,'blocking_error_count':len(allocation_errors),'online_periods':online_periods,'offline_periods':offline_periods,'constructive_seed_validated':seed_result['valid'],'constructive_seed_reason':seed_result['reason'],'constructive_seed_strategy':seed_result.get('strategy'),'constructive_seed_nodes':seed_result['nodes'],'constructive_seed_elapsed':seed_elapsed,'cp_sat_hint_count':seed_hint_count,'feasible_solution_source':'CP_SAT' if has_phase2_solution or phase1_cp_has_solution else ('CONSTRUCTIVE_SEED' if seed_result['valid'] else None),'fixed_entry_count':run.source_version.entries.count(),'wall_time':phase1_wall_time+phase2_wall_time,'user_time':getattr(response_proto,'user_time',None),'num_conflicts':_solver_metric(phase1_solver.NumConflicts)+(_solver_metric(solver.NumConflicts) if has_phase2_solution else 0),'num_branches':_solver_metric(phase1_solver.NumBranches)+(_solver_metric(solver.NumBranches) if has_phase2_solution else 0),'num_booleans':_solver_metric(metric_solver.NumBooleans),'num_binary_propagations':binary_propagations,'num_integer_propagations':integer_propagations,'num_propagations':binary_propagations+integer_propagations,'response_stats':_solver_metric(metric_solver.ResponseStats,''),'infeasible_core_count':0,'timings':timings,'hybrid_model':hybrid_candidate_metrics,'room_pool_count':len(room_group_sizes),'room_eligibility_cache_entries':len(allocation_eligibility_cache)}
 run.statistics.update({'phase1_requested_max_time_seconds':max_solve_seconds,'phase1_actual_max_time_seconds':phase1_solver.parameters.max_time_in_seconds,'phase1_start_monotonic':phase1_started,'phase1_end_monotonic':phase1_ended,'phase1_elapsed_seconds':phase1_elapsed,'phase1_solutions_found':phase1_telemetry.solutions_found,'phase1_time_to_first_solution':phase1_telemetry.time_to_first_solution,'phase1_objective_at_first_solution':phase1_telemetry.objective_at_first_solution,'phase1_best_objective':phase1_telemetry.best_objective,'phase1_best_bound':phase1_best_bound,'phase1_stop_search_calls':0,'phase1_callback_stop_conditions':[],'phase1_objective_term_count':0});run.objective_score=objective_score
 run.statistics.update({'constructive_seed_status':seed_result.get('status','INVALID'),'seed_elapsed_seconds':seed_elapsed,'seed_run_id':seed_result.get('seed_run_id')})
 if application==GenerationRun.Status.SUCCEEDED:run.diagnostics={'errors':[],'warnings':candidate_warnings,'final_validation_passed':True,'blocking_errors':[]}
 elif solver_status=='POST_VALIDATION_FAILED':run.diagnostics={'errors':allocation_errors,'warnings':candidate_warnings,'final_validation_passed':False,'blocking_errors':allocation_errors}
 elif solver_status=='UNKNOWN':run.diagnostics={'errors':[{'code':'TIME_LIMIT','message':'No feasible timetable was found within the solver time limit.'}],'warnings':candidate_warnings}
 elif solver_status=='INFEASIBLE':run.diagnostics={'errors':[{'code':'INFEASIBLE_CORE','message':'CP-SAT proved the model infeasible. The following assumed requirements/constraint groups form a sufficient infeasibility core.','core':infeasible_core}],'warnings':[]}
 else:run.diagnostics={'errors':[{'code':solver_status,'message':'No feasible timetable was found.'}],'warnings':candidate_warnings}
 run.completed_at=timezone.now();run.save();record('GENERATION_SUCCEEDED' if application==GenerationRun.Status.SUCCEEDED else 'GENERATION_INFEASIBLE',run.created_by,timetable=run.timetable,version=run.source_version,entity_type='GenerationRun',entity_id=run.id,metadata={'solver_status':solver_status,'final_validation_passed':final_validation_passed,'blocking_error_count':len(allocation_errors)})
 timings['result_persistence_seconds']=time.perf_counter()-persistence_started
 timings['total_service_seconds']=time.perf_counter()-started
 run.statistics['timings']=timings;run.save(update_fields=['statistics'])
 _gen_log(f'persistence end elapsed={time.perf_counter()-persistence_started:.3f}s')
 _gen_log(f'total elapsed={time.perf_counter()-started:.3f}s solver_status={solver_status} candidates={candidate_count} vars={len(proto.variables)} constraints={len(proto.constraints)}')
 return run

def _finish(run,status,solver_status,diagnostics,started=None,statistics=None):
 persistence_started=time.perf_counter();_gen_log('persistence start (terminal diagnostic)')
 errors=_validation_errors(diagnostics.get('errors',[]));run.status=status;run.solver_status=solver_status;run.diagnostics={**diagnostics,'stage':'PRE_SOLVER','solver_invoked':False};run.generation_validation={'valid':False,'blocker_count':len(errors),'validated_at':timezone.now().isoformat(),'errors':errors};run.statistics={**(statistics or {}),'stage':'PRE_SOLVER','solver_invoked':False,'wall_time':None,'generation_wall_time':time.perf_counter()-started if started is not None else None};run.completed_at=timezone.now();run.save();record('GENERATION_INFEASIBLE',run.created_by,timetable=run.timetable,version=run.source_version,entity_type='GenerationRun',entity_id=run.id,metadata={'solver_status':solver_status,'stage':'PRE_SOLVER'})
 _gen_log(f'persistence end elapsed={time.perf_counter()-persistence_started:.3f}s')
 if started is not None:_gen_log(f'total elapsed={time.perf_counter()-started:.3f}s status={solver_status} stage=pre-solver')
 return run
@transaction.atomic
def apply_generation(run):
 run=GenerationRun.objects.select_for_update().select_related('source_version','timetable').get(pk=run.pk)
 prevalidation=validate_generation_apply(run)
 if not prevalidation['valid']:
  first=prevalidation['errors'][0]
  direct_codes={'GENERATION_ALREADY_APPLIED','INVALID_GENERATION_STATUS','SOURCE_VERSION_CHANGED',WEEKEND_ERROR_CODE}
  code=first['code'] if first['code'] in direct_codes else 'GENERATED_TIMETABLE_VALIDATION_FAILED'
  message=first['message'] if code in direct_codes else 'Generated timetable failed final hard-rule validation.'
  raise GenerationApplyError(code,message,prevalidation['errors'])
 generated_entries=run.result.get('entries',[]) if isinstance(run.result,dict) else []
 source_entries=run.source_version.entries.all()
 scope=set(run.input_config.get('section_ids') or [])
 if run.mode=='REBUILD_UNLOCKED':
  source_entries=source_entries.filter(Q(locked=True)|~Q(section_id__in=scope)) if scope else source_entries.filter(locked=True)
 latest=TimetableVersion.objects.select_for_update().filter(timetable=run.timetable).order_by('-version_no').first();new=TimetableVersion.objects.create(timetable=run.timetable,version_no=latest.version_no+1,previous_version=run.source_version,created_by=run.created_by,notes=f'Generated from version {run.source_version.version_no}')
 scope=set(run.input_config.get('section_ids') or []);mode=run.mode
 for entry in run.source_version.entries.prefetch_related('faculty_assignments').all():
  if mode=='REBUILD_UNLOCKED' and (not scope or str(entry.section_id) in scope) and not entry.locked:continue
  clone=ScheduleEntry.objects.create(version=new,section=entry.section,course_offering=entry.course_offering,weekday=entry.weekday,start_slot=entry.start_slot,block_length=entry.block_length,room=entry.room,entry_type=entry.entry_type,delivery_mode=entry.delivery_mode,locked=entry.locked,note=entry.note)
  ScheduleEntryFaculty.objects.bulk_create([ScheduleEntryFaculty(schedule_entry=clone,faculty_id=assignment.faculty_id,role=assignment.role) for assignment in entry.faculty_assignments.all()])
 for item in generated_entries:
  delivery_mode=item.get('delivery_mode','OFFLINE')
  room_id=None if delivery_mode=='ONLINE' else item.get('room_id')
  entry=ScheduleEntry.objects.create(version=new,section_id=item['section_id'],course_offering_id=item['course_offering_id'],weekday=item['weekday'],start_slot_id=item['start_slot_id'],block_length=item['block_length'],room_id=room_id,entry_type=item['entry_type'],delivery_mode=delivery_mode)
  ScheduleEntryFaculty.objects.bulk_create([ScheduleEntryFaculty(schedule_entry=entry,faculty_id=a['faculty_id'],role=a['role']) for a in item.get('faculty',[])])
 conflicts=validate_version(new)
 if conflicts.get('conflicts'):
  raise GenerationApplyError('GENERATED_TIMETABLE_VALIDATION_FAILED','Generated timetable failed final validation.',conflicts.get('conflicts',[]))
 run.status=GenerationRun.Status.APPLIED;run.applied_version=new;run.applied_at=timezone.now();run.apply_validation={**prevalidation,'valid':True,'blocker_count':prevalidation['blocking_error_count'],'validated_at':timezone.now().isoformat()};run.save(update_fields=['status','applied_version','applied_at','apply_validation']);record('GENERATION_APPLIED',run.created_by,timetable=new,version=new,entity_type='GenerationRun',entity_id=run.id,metadata={'generation_run_id':str(run.id),'source_version':str(run.source_version_id)});return new
