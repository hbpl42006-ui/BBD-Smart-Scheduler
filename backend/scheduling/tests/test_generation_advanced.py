import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from scheduling.models import GenerationRun, ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
from academics.models import Section, CourseOffering, Course
from faculty.models import Faculty
from common.models import TimeSlot
from rooms.models import Room, RoomAvailability, RoomEligibilityException
from accounts.models import User, Role
from scheduling.solver.engine import Candidate, FacultyAssignment, Requirement, build_candidates, build_requirements
from scheduling.solver.objective import build_objective_context, candidate_score
from scheduling.solver.service import _build_occurrence_model, _constructive_seed, _group_occurrences, preflight_generation
from ortools.sat.python import cp_model

pytestmark = pytest.mark.django_db

@pytest.fixture
def api_client():
    return APIClient()

@pytest.fixture
def admin_user():
    return User.objects.create_user('admin@test.local', 'Pass123', role=Role.TIMETABLE_COORDINATOR)

@pytest.fixture
def data(db, admin_user):
    from institutions.models import Institution, Department, Program
    from academics.models import AcademicSession, Semester
    from common.models import TimeSlotTemplate
    from scheduling.models import Timetable

    inst = Institution.objects.create(name='BBD', code='BBD')
    dept = Department.objects.create(institution=inst, name='CSE', code='CSE')
    prog = Program.objects.create(department=dept, name='BTech', code='BT', duration_years=4)
    sess = AcademicSession.objects.create(institution=inst, name='2026', start_date='2026-01-01', end_date='2026-12-31')
    sem = Semester.objects.create(session=sess, name='Sem 1', number=1, type='ODD', start_date='2026-01-01', end_date='2026-06-30')
    sec = Section.objects.create(program=prog, semester=sem, year=1, name='A', student_strength=60)

    course = Course.objects.create(code='CS101', name='Intro', credit=3)
    offering = CourseOffering.objects.create(semester=sem, section=sec, course=course, weekly_periods=2, required_block_size=1)

    fac = Faculty.objects.create(user=User.objects.create_user('f1@t.l','P'), employee_code='F1', initials='F1', department=dept)
    room = Room.objects.create(code='R1', building='B1', floor='1', capacity=60)

    tpl = TimeSlotTemplate.objects.create(name='T1', institution=inst)
    slots = [TimeSlot.objects.create(template=tpl, label=f'S{i}', start_time=f'{i+9:02d}:00', end_time=f'{i+10:02d}:00', order=i) for i in range(5)]

    tt = Timetable.objects.create(institution=inst, academic_session=sess, semester=sem, department=dept, title='TT', created_by=admin_user)
    v = TimetableVersion.objects.create(timetable=tt, version_no=1, created_by=admin_user)

    return {'version': v, 'section': sec, 'offering': offering, 'faculty': fac, 'room': room, 'slots': slots, 'timetable': tt, 'admin': admin_user}

def run_gen(client, data, payload):
    client.force_authenticate(data['admin'])
    res = client.post(f"/api/versions/{data['version'].id}/generation-runs/", payload, format='json')
    assert res.status_code == 201, res.data
    return res.data

def test_section_gap_objective(api_client, data):
    ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1, locked=True)
    data['offering'].weekly_periods = 2
    data['offering'].save()
    RoomAvailability.objects.create(room=data['room'], weekday=0, time_slot=data['slots'][3], status='BLOCKED')
    RoomAvailability.objects.create(room=data['room'], weekday=0, time_slot=data['slots'][4], status='BLOCKED')
    payload = {
        'mode': 'FILL_GAPS',
        'soft_constraints': {'spread_course_days': 0, 'balance_section_load': 0, 'minimize_section_gaps': 100, 'minimize_faculty_gaps': 0, 'preserve_existing': 0},
        'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}],
        'random_seed': 42
    }
    res = run_gen(api_client, data, payload)
    assert res['status'] == 'SUCCEEDED'
    assert res['result']['entries'][0]['start_slot_id'] == str(data['slots'][1].id)

def test_bulk_objective_context_preserves_candidate_score(data):
    entry = ScheduleEntry.objects.create(
        version=data['version'], section=data['section'], course_offering=data['offering'],
        weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1,
    )
    ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=data['faculty'], role='PRIMARY')
    candidate = Candidate(
        requirement_id='test', course_offering_id=str(data['offering'].id),
        section_id=str(data['section'].id), weekday=0,
        start_slot_id=str(data['slots'][2].id), occupied_slot_ids=(str(data['slots'][2].id),),
        block_length=1, room_id=str(data['room'].id),
        faculty=(FacultyAssignment(str(data['faculty'].id), 'PRIMARY'),), entry_type='LECTURE',
    )
    config = {
        'mode': 'REBUILD_UNLOCKED',
        'soft_constraints': {
            'spread_course_days': 10, 'balance_section_load': 5,
            'minimize_section_gaps': 4, 'minimize_faculty_gaps': 2,
            'preserve_existing': 8,
        },
    }
    context = build_objective_context(data['version'])
    assert candidate_score(candidate, data['version'], config, context) == candidate_score(candidate, data['version'], config)

def test_validate_apply_is_read_only_and_returns_shared_apply_preflight(api_client,data):
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    from scheduling.solver.service import fingerprint,validate_generation_apply
    from scheduling.provenance import build_input_snapshot,SNAPSHOT_VERSION
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1,1]}]}
    source_fp=fingerprint(data['version']);snapshot,input_fp=build_input_snapshot(data['version'],config,source_fp)
    run=GenerationRun.objects.create(timetable=data['timetable'],source_version=data['version'],created_by=data['admin'],mode='FILL_GAPS',status='SUCCEEDED',solver_status='FEASIBLE',input_config=config,input_snapshot=snapshot,input_snapshot_version=SNAPSHOT_VERSION,input_fingerprint=input_fp,result={'entries':[]},source_fingerprint=source_fp,statistics={'final_validation_passed':False},generation_validation={'valid':False,'blocker_count':3,'validated_at':'generation-time'})
    before=(TimetableVersion.objects.count(),ScheduleEntry.objects.count(),run.applied_at,run.applied_version_id,dict(run.generation_validation),dict(run.apply_validation))
    api_client.force_authenticate(data['admin'])
    with CaptureQueriesContext(connection) as queries:
        response=api_client.post(f'/api/generation-runs/{run.pk}/validate-apply/')
    after_run=GenerationRun.objects.get(pk=run.pk)
    after=(TimetableVersion.objects.count(),ScheduleEntry.objects.count(),after_run.applied_at,after_run.applied_version_id,dict(after_run.generation_validation),dict(after_run.apply_validation))
    assert response.status_code==200,response.data
    assert response.data==validate_generation_apply(run)
    assert response.data['valid'] is False and response.data['blocking_error_count']>0
    assert before==after
    assert not any(query['sql'].lstrip().upper().startswith(('INSERT','UPDATE','DELETE','REPLACE')) for query in queries)

def test_generation_input_snapshot_fingerprint_is_deterministic_and_semantic(data):
    from scheduling.provenance import build_input_snapshot
    from scheduling.solver.service import fingerprint
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]}
    source_fp=fingerprint(data['version'])
    snapshot_a,fp_a=build_input_snapshot(data['version'],config,source_fp)
    snapshot_b,fp_b=build_input_snapshot(data['version'],config,source_fp)
    assert snapshot_a==snapshot_b and fp_a==fp_b
    data['room'].building='Display-only building';data['room'].save(update_fields=['building'])
    assert build_input_snapshot(data['version'],config,source_fp)[1]==fp_a
    data['room'].allowed_year=2;data['room'].save(update_fields=['allowed_year'])
    assert build_input_snapshot(data['version'],config,source_fp)[1]!=fp_a

def test_generation_input_fingerprint_tracks_faculty_availability(data):
    from scheduling.provenance import build_input_snapshot
    from scheduling.solver.service import fingerprint
    from faculty.models import FacultyAvailability
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]}
    source_fp=fingerprint(data['version']);initial=build_input_snapshot(data['version'],config,source_fp)[1]
    FacultyAvailability.objects.create(faculty=data['faculty'],weekday=0,time_slot=data['slots'][0],is_available=False)
    assert build_input_snapshot(data['version'],config,source_fp)[1]!=initial

def test_empty_snapshot_is_explicitly_legacy_and_does_not_claim_reproducibility(data):
    from scheduling.provenance import provenance_for_run
    from scheduling.solver.service import fingerprint
    run=GenerationRun.objects.create(timetable=data['timetable'],source_version=data['version'],created_by=data['admin'],source_fingerprint=fingerprint(data['version']))
    assert provenance_for_run(run)['provenance_status']=='LEGACY_NO_SNAPSHOT'
    assert provenance_for_run(run)['generation_input_fingerprint'] is None

def test_explicit_current_revalidation_preserves_generation_validation(api_client,data):
    from scheduling.solver.service import fingerprint
    generation={'valid':True,'blocker_count':0,'validated_at':'historical-generation'}
    run=GenerationRun.objects.create(timetable=data['timetable'],source_version=data['version'],created_by=data['admin'],status='SUCCEEDED',solver_status='FEASIBLE',input_config={},result={'entries':[]},source_fingerprint=fingerprint(data['version']),generation_validation=generation)
    api_client.force_authenticate(data['admin'])
    response=api_client.post(f'/api/generation-runs/{run.pk}/revalidate/')
    run.refresh_from_db()
    assert response.status_code==200
    assert run.generation_validation==generation
    assert run.current_validation['validated_at']
    assert run.current_validation['valid']==response.data['valid']


def test_preferred_room_is_a_soft_score_and_ineligible_preference_is_only_advisory(data):
    alternative=Room.objects.create(code='R2',building='B1',floor='1',capacity=61)
    data['offering'].weekly_periods=1;data['offering'].preferred_room=data['room'];data['offering'].save(update_fields=['weekly_periods','preferred_room'])
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile)
    assert not diagnostics
    preference=profile['requirements'][0]
    assert preference['preferred_room_candidate_count']>0
    preferred=[item for item in candidates[requirements[0].requirement_id] if item.room_id==preference['preferred_room_group_id']][0]
    other=[item for item in candidates[requirements[0].requirement_id] if item.weekday==preferred.weekday and item.start_slot_id==preferred.start_slot_id and item.room_id!=preferred.room_id][0]
    context=build_objective_context(data['version'],profile['_room_group_by_room'],profile['_room_slot_orders'],preferred_room_groups={str(data['offering'].pk):preference['preferred_room_group_id']})
    assert candidate_score(preferred,data['version'],config,context)>candidate_score(other,data['version'],config,context)
    data['room'].allowed_year=2;data['room'].save(update_fields=['allowed_year'])
    profile={};fallback_candidates,warnings=build_candidates(data['version'],requirements,config,profile)
    assert fallback_candidates[requirements[0].requirement_id]
    assert any(item['code']=='PREFERRED_ROOM_UNAVAILABLE' and item['severity']=='WARNING' for item in warnings)

def test_generation_candidates_exclude_other_institution_time_slots(data):
    from institutions.models import Institution
    from common.models import TimeSlotTemplate

    other_institution = Institution.objects.create(name='Other University', code='OTHER')
    other_template = TimeSlotTemplate.objects.create(name='Other schedule', institution=other_institution)
    from common.models import TimeSlot
    other_slot = TimeSlot.objects.create(template=other_template, label='09:00-10:00', start_time='09:00', end_time='10:00', order=0)
    config = {
        'mode': 'REBUILD_UNLOCKED',
        'section_ids': [str(data['section'].id)],
        'offering_rules': [{
            'course_offering_id': str(data['offering'].id),
            'faculty': [{'faculty_id': str(data['faculty'].id), 'role': 'PRIMARY'}],
            'session_lengths': [1, 1],
        }],
    }
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    profile = {}
    candidates, diagnostics = build_candidates(data['version'], requirements, config, profile)
    assert not diagnostics
    assert all(candidate.start_slot_id != str(other_slot.id) for options in candidates.values() for candidate in options)
    assert profile['average_candidates_per_requirement'] > 0
    assert profile['top_30'][0]['candidate_count'] == max(row['candidate_count'] for row in profile['requirements'])

def test_identical_occurrence_requirements_reuse_candidate_eligibility_work(data):
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'}],'session_lengths':[1,1]}]}
    requirements,errors=build_requirements(data['version'],config)
    assert not errors and len(requirements)==2
    profile={}
    candidates,diagnostics=build_candidates(data['version'],requirements,config,profile)
    assert not diagnostics
    assert profile['equivalent_requirement_cache_hits']==1
    first,second=requirements
    assert candidates[first.requirement_id] and candidates[second.requirement_id]
    assert all(item.requirement_id==first.requirement_id for item in candidates[first.requirement_id])
    assert all(item.requirement_id==second.requirement_id for item in candidates[second.requirement_id])
    assert [(x.weekday,x.start_slot_id,x.room_id) for x in candidates[first.requirement_id]]==[(x.weekday,x.start_slot_id,x.room_id) for x in candidates[second.requirement_id]]

def test_equivalent_physical_rooms_are_grouped_and_resolved_on_generation(api_client, data):
    second_room = Room.objects.create(code='R2', building='B1', floor='1', capacity=60)
    data['offering'].weekly_periods = 1
    data['offering'].save()
    config = {
        'mode': 'FILL_GAPS',
        'offering_rules': [{
            'course_offering_id': str(data['offering'].id),
            'faculty': [{'faculty_id': str(data['faculty'].id), 'role': 'PRIMARY'}],
            'session_lengths': [1],
        }],
    }
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    profile = {}
    candidates, diagnostics = build_candidates(data['version'], requirements, config, profile)
    assert not diagnostics
    row = profile['requirements'][0]
    assert row['eligible_room_count'] == 2
    assert row['eligible_room_group_count'] == 1
    run_data = run_gen(api_client, data, config)
    entry = run_data['result']['entries'][0]
    assert entry['room_id'] in {str(data['room'].id), str(second_room.id)}
    assert run_data['statistics']['variable_count']==run_data['statistics']['candidate_count']
    assert run_data['statistics']['placement_variable_count']==0
    assert run_data['statistics']['assumption_variable_count']==0
    assert run_data['statistics']['unique_time_placement_count']==25

def test_identical_weekly_occurrences_use_exact_group_cardinality(data):
    faculty=(FacultyAssignment(str(data['faculty'].pk),'PRIMARY'),)
    requirements=[Requirement(f'r{i}',str(data['offering'].pk),str(data['section'].pk),'LECTURE',1,faculty,'',60) for i in range(3)]
    candidates={}
    for requirement in requirements:
        candidates[requirement.requirement_id]=[
            Candidate(requirement.requirement_id,str(data['offering'].pk),str(data['section'].pk),0,str(slot.pk),(str(slot.pk),),1,'POOL:R1',faculty,'LECTURE')
            for slot in data['slots']
        ]
    groups=_group_occurrences(requirements,candidates)
    assert len(groups)==1
    assert groups[0]['occurrence_count']==3
    model,variables,assumptions,_=_build_occurrence_model(groups,{'POOL:R1':1})
    assert len(variables)==len(data['slots'])
    assert len(model.Proto().variables)==len(data['slots'])
    assert not assumptions
    assert len(model.Proto().constraints)==1+len(data['slots'])+len(data['slots'])+len(data['slots'])
    solver=cp_model.CpSolver();assert solver.Solve(model)==cp_model.OPTIMAL
    assert sum(solver.Value(variable) for variable,_candidate,_group_id in variables)==3

def test_constructive_seed_respects_group_counts_and_cp_sat_accepts_hints(api_client,data):
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1,1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile);assert not diagnostics
    groups=_group_occurrences(requirements,candidates)
    seed=_constructive_seed(groups,profile['_room_group_sizes'],seed=42)
    assert seed['valid'] is True
    assert len(seed['assignments'])==2
    model,variables,_,_=_build_occurrence_model(groups,profile['_room_group_sizes'])
    selected={(group_id,candidate.weekday,candidate.start_slot_id) for group_id,candidate in seed['assignments']}
    for variable,candidate,group_id in variables:
        model.AddHint(variable,int((group_id,candidate.weekday,candidate.start_slot_id) in selected))
    solver=cp_model.CpSolver();solver.parameters.max_time_in_seconds=5
    assert solver.Solve(model) in (cp_model.FEASIBLE,cp_model.OPTIMAL)
    result=run_gen(api_client,data,config)
    assert result['solver_status'] in {'FEASIBLE','OPTIMAL'}
    assert result['statistics']['constructive_seed_validated'] is True
    assert result['statistics']['cp_sat_hint_count']==result['statistics']['candidate_decision_variable_count']
    assert result['statistics']['occurrence_group_count']==1
    assert len(result['result']['entries'])==2

def test_constructive_seed_zero_budget_returns_without_assignments():
    from scheduling.solver.service import _constructive_seed
    started=__import__('time').perf_counter()
    result=_constructive_seed([],{},time_budget_seconds=0)
    assert result['status']=='TIMEOUT'
    assert result['reason']=='SEED_TIME_BUDGET_EXCEEDED'
    assert result['assignments']==[]
    assert __import__('time').perf_counter()-started<0.5

def test_same_source_validated_feasible_run_is_reused_as_grouped_seed(data):
    from scheduling.solver.engine import candidate_dict
    from scheduling.solver.service import _reusable_generation_seed, fingerprint
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1,1]}],'max_solve_seconds':120}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile);assert not diagnostics
    groups=_group_occurrences(requirements,candidates)
    constructive=_constructive_seed(groups,profile['_room_group_sizes'])
    assert constructive['valid']
    entries=[];indexes={group['requirement_id']:0 for group in groups}
    group_map={group['requirement_id']:group for group in groups}
    for group_id,candidate in constructive['assignments']:
        group=group_map[group_id];index=indexes[group_id];indexes[group_id]+=1
        item=candidate_dict(candidate);item['requirement_id']=group['requirements'][index].requirement_id
        item['room_pool_id']=candidate.room_id;item['room_id']=profile['_room_group_members'][candidate.room_id][0]
        item['delivery_mode']='OFFLINE';entries.append(item)
    source_fingerprint=fingerprint(data['version'])
    old=GenerationRun.objects.create(timetable=data['version'].timetable,source_version=data['version'],created_by=data['version'].created_by,mode='FILL_GAPS',status='SUCCEEDED',solver_status='FEASIBLE',input_config=config,result={'entries':entries},source_fingerprint=source_fingerprint)
    current_config={**config,'max_solve_seconds':30}
    current=GenerationRun.objects.create(timetable=data['version'].timetable,source_version=data['version'],created_by=data['version'].created_by,mode='FILL_GAPS',input_config=current_config,source_fingerprint=source_fingerprint)
    seed=_reusable_generation_seed(current,groups,requirements)
    assert seed and seed['valid'] and seed['strategy']=='VALIDATED_PRIOR_RUN'
    assert seed['seed_run_id']==str(old.pk)
    assert len(seed['assignments'])==2

def test_constraint_family_relaxation_is_diagnostic_only_and_selective(data):
    faculty_a=(FacultyAssignment('faculty-A','PRIMARY'),)
    faculty_b=(FacultyAssignment('faculty-B','PRIMARY'),)
    groups=[]
    for index,faculty in enumerate((faculty_a,faculty_b)):
        requirement=Requirement(f'req-{index}',f'offering-{index}','same-section','LECTURE',1,faculty,'',60)
        candidate=Candidate(requirement.requirement_id,requirement.course_offering_id,requirement.section_id,0,'slot-1',('slot-1',),1,'POOL:R1',faculty,'LECTURE')
        groups.extend(_group_occurrences([requirement],{requirement.requirement_id:[candidate]}))
    strict,_,_,_=_build_occurrence_model(groups,{'POOL:R1':2})
    strict_solver=cp_model.CpSolver();assert strict_solver.Solve(strict)==cp_model.INFEASIBLE
    relaxed,_,_,_=_build_occurrence_model(groups,{'POOL:R1':2},disabled_families={'SECTION_NO_OVERLAP'})
    relaxed_solver=cp_model.CpSolver();assert relaxed_solver.Solve(relaxed)==cp_model.OPTIMAL
    diagnostic,_,descriptions,_=_build_occurrence_model(groups,{'POOL:R1':2},with_assumptions=True)
    assert len(descriptions)>0
    assert len(strict.Proto().variables)==2
    assert len(diagnostic.Proto().variables)>len(strict.Proto().variables)

def test_room_equivalence_never_transfers_a_scoped_exception(api_client,data):
    from rooms.models import RoomEligibilityException
    section=data['section'];section.year=2;section.save(update_fields=['year'])
    course=Course.objects.get(code='CS101')
    allowed=Room.objects.create(code='LGF001',building='B1',floor='0',capacity=60,room_type='ELECTRICAL_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    denied=Room.objects.create(code='LGF-002',building='B1',floor='0',capacity=60,room_type='ELECTRICAL_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    RoomEligibilityException.objects.create(room=allowed,program=section.program,year=2,course=course,allow=True,active=True,reason='exact scoped test')
    data['offering'].course=course;data['offering'].section=section;data['offering'].preferred_room=allowed;data['offering'].room_type_requirement='LAB';data['offering'].default_class_type='PRACTICAL';data['offering'].weekly_periods=1;data['offering'].save()
    config={'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'entry_type':'PRACTICAL','session_lengths':[1],'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]}
    reqs,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],reqs,config,profile)
    assert not [item for item in diagnostics if item.get('severity')=='ERROR']
    assert profile['requirements'][0]['eligible_room_count']==1
    assert profile['requirements'][0]['eligible_room_group_count']==1
    assert {member for candidate in candidates[reqs[0].requirement_id] for member in profile['_room_group_members'][candidate.room_id]}=={str(allowed.pk)}
    from faculty.models import CourseOfferingFaculty
    CourseOfferingFaculty.objects.get_or_create(course_offering=data['offering'],faculty=data['faculty'],defaults={'role':'PRIMARY'})
    result=run_gen(api_client,data,config)
    assert result['solver_status'] in {'FEASIBLE','OPTIMAL'}
    assert {entry['room_id'] for entry in result['result']['entries']}=={str(allowed.pk)}

def test_validated_seed_is_used_when_solver_returns_unknown(api_client, data, monkeypatch):
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model.CpSolver, 'Solve', lambda _solver, _model, *_args: cp_model.UNKNOWN)
    monkeypatch.setattr(cp_model.CpSolver, 'WallTime', lambda _solver: 0.0)
    monkeypatch.setattr(cp_model.CpSolver, 'ObjectiveValue', lambda _solver: 0.0)
    monkeypatch.setattr(cp_model.CpSolver, 'NumConflicts', lambda _solver: 0)
    monkeypatch.setattr(cp_model.CpSolver, 'NumBranches', lambda _solver: 0)
    run_data = run_gen(api_client, data, {
        'mode': 'FILL_GAPS',
        'offering_rules': [{
            'course_offering_id': str(data['offering'].id),
            'faculty': [{'faculty_id': str(data['faculty'].id), 'role': 'PRIMARY'}],
            'session_lengths': [1, 1],
        }],
    })
    assert run_data['solver_status'] == 'FEASIBLE'
    assert run_data['status'] == 'SUCCEEDED'
    assert len(run_data['result']['entries']) == 2
    assert run_data['statistics']['phase1_status'] == 'UNKNOWN'
    assert run_data['statistics']['feasible_solution_source'] == 'CONSTRUCTIVE_SEED'

@pytest.mark.parametrize(('requested','expected_budget'),[(30,30),(120,120),(300,300)])
def test_generation_request_persists_and_allocates_requested_solve_budget(api_client,data,monkeypatch,requested,expected_budget):
    from ortools.sat.python import cp_model
    from scheduling.solver_serializers import GenerationConfigSerializer
    default_serializer=GenerationConfigSerializer(data={})
    assert default_serializer.is_valid()
    assert default_serializer.validated_data['max_solve_seconds']==120
    observed=[]
    def unknown(_solver,_model,*_args):
        return cp_model.UNKNOWN
    monkeypatch.setattr(cp_model.CpSolver,'Solve',unknown)
    monkeypatch.setattr(cp_model.CpSolver,'WallTime',lambda _solver:0.0)
    monkeypatch.setattr(cp_model.CpSolver,'NumConflicts',lambda _solver:0)
    monkeypatch.setattr(cp_model.CpSolver,'NumBranches',lambda _solver:0)
    original_init=cp_model.CpSolver.__init__
    def capture_init(solver,*args,**kwargs):
        original_init(solver,*args,**kwargs)
        observed.append(solver)
    monkeypatch.setattr(cp_model.CpSolver,'__init__',capture_init)
    config={'mode':'FILL_GAPS','max_solve_seconds':requested,'section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1,1]}]}
    response=run_gen(api_client,data,config)
    assert response['input_config']['max_solve_seconds']==requested
    assert response['statistics']['requested_solve_seconds']==requested
    assert response['statistics']['phase1_status']=='UNKNOWN'
    assert response['statistics']['phase1_actual_max_time_seconds']==expected_budget
    assert response['statistics']['phase1_solutions_found']==0
    assert response['statistics']['phase1_time_to_first_solution'] is None
    assert response['solver_status']=='FEASIBLE'
    assert response['statistics']['constructive_seed_validated'] is True
    assert observed[0].parameters.max_time_in_seconds==expected_budget
    assert expected_budget==requested

@pytest.mark.parametrize('requested',[29,601])
def test_generation_solve_time_rejects_values_outside_limits(api_client,data,requested):
    api_client.force_authenticate(data['admin'])
    response=api_client.post(f'/api/versions/{data["version"].pk}/generation-runs/',{'max_solve_seconds':requested},format='json')
    assert response.status_code==400
    assert 'max_solve_seconds' in response.data

def test_phase1_feasible_assignment_survives_phase2_timeout(api_client,data,monkeypatch):
    from ortools.sat.python import cp_model
    original_solve=cp_model.CpSolver.Solve
    calls={'count':0}
    def phase2_timeout(solver,model,*args):
        calls['count']+=1
        if calls['count']==1:return original_solve(solver,model,*args)
        return cp_model.UNKNOWN
    monkeypatch.setattr(cp_model.CpSolver,'Solve',phase2_timeout)
    monkeypatch.setattr(cp_model.CpSolver,'WallTime',lambda solver:0.001 if calls['count']==1 else 0.0)
    response=run_gen(api_client,data,{'mode':'FILL_GAPS','max_solve_seconds':30,'section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1,1]}]})
    assert calls['count']==2
    assert response['solver_status']=='FEASIBLE'
    assert response['status']=='SUCCEEDED'
    assert len(response['result']['entries'])==2
    assert response['statistics']['phase1_status'] in {'FEASIBLE','OPTIMAL'}
    assert response['statistics']['phase1_solutions_found']>=1
    assert response['statistics']['phase1_time_to_first_solution'] is not None
    assert response['statistics']['phase2_status']=='UNKNOWN'
    assert response['statistics']['generated_entry_count']==2

def test_preflight_reports_hard_room_period_capacity_shortfall(data):
    data['offering'].weekly_periods = 26
    data['offering'].save()
    result = preflight_generation(data['version'], {
        'mode': 'FILL_GAPS',
        'section_ids': [str(data['section'].id)],
        'offering_rules': [{
            'course_offering_id': str(data['offering'].id),
            'faculty': [{'faculty_id': str(data['faculty'].id), 'role': 'PRIMARY'}],
            'session_lengths': [1] * 26,
        }],
    })
    capacity_error = next(error for error in result['errors'] if error['code'] == 'INSUFFICIENT_ROOM_CAPACITY')
    assert result['valid'] is False
    assert capacity_error['required_periods'] == 26
    assert capacity_error['available_periods'] == 25
    assert capacity_error['shortfall_periods'] == 1

def test_generation_candidates_and_result_never_use_weekends(api_client, data):
    data['offering'].weekly_periods = 1; data['offering'].save()
    config = {'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    profile = {}
    candidates, diagnostics = build_candidates(data['version'], requirements, config, profile)
    placements = [item for group in candidates.values() for item in group]
    assert not diagnostics and placements
    assert all(item.weekday in (0, 1, 2, 3, 4) for item in placements)
    result = run_gen(api_client, data, config)
    assert all(item['weekday'] in (0, 1, 2, 3, 4) for item in result['result']['entries'])

@pytest.mark.parametrize('offline_day', [5, 6])
def test_hybrid_weekend_offline_day_is_a_preflight_error(data, offline_day):
    from scheduling.solver.service import preflight_generation
    data['section'].delivery_policy = Section.DeliveryPolicy.HYBRID
    data['section'].offline_weekday = offline_day
    data['section'].save(update_fields=['delivery_policy', 'offline_weekday'])
    result = preflight_generation(data['version'], {'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)]})
    invalid = [error for error in result['errors'] if error['code'] == 'INVALID_HYBRID_OFFLINE_DAY']
    assert not result['valid'] and invalid and invalid[0]['offline_weekday'] == offline_day

@pytest.mark.parametrize('weekday', [5, 6])
def test_manual_entry_creation_rejects_weekends(api_client, data, weekday):
    api_client.force_authenticate(data['admin'])
    response = api_client.post(f"/api/versions/{data['version'].pk}/entries/", {
        'section':str(data['section'].pk), 'course_offering':str(data['offering'].pk),
        'weekday':weekday, 'start_slot':str(data['slots'][0].pk), 'block_length':1,
        'room':str(data['room'].pk),
    }, format='json')
    assert response.status_code == 400
    assert response.data['conflicts'][0]['code'] == 'WEEKEND_SCHEDULING_NOT_ALLOWED'
    assert not data['version'].entries.exists()

@pytest.mark.parametrize('weekday', [5, 6])
def test_room_lookup_rejects_weekend_placement_days(api_client, data, weekday):
    api_client.force_authenticate(data['admin'])
    response = api_client.get(f"/api/versions/{data['version'].pk}/available-rooms/?weekday={weekday}")
    assert response.status_code == 400
    assert response.data['code'] == 'WEEKEND_SCHEDULING_NOT_ALLOWED'

@pytest.mark.parametrize('weekday', [5, 6])
def test_version_validation_reports_legacy_weekend_entries(data, weekday):
    from scheduling.services.validation import validate_version
    entry = ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'])
    ScheduleEntry.objects.filter(pk=entry.pk).update(weekday=weekday)
    entry.refresh_from_db()
    result = validate_version(data['version'])
    assert any(item.get('entry_id') == str(entry.pk) and item.get('code') == 'WEEKEND_SCHEDULING_NOT_ALLOWED' for item in result['conflicts'])

@pytest.mark.parametrize('weekday', [5, 6])
def test_schedule_entry_model_rejects_new_weekend_records(data, weekday):
    from django.core.exceptions import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=weekday, start_slot=data['slots'][0], room=data['room'])
    assert exc_info.value.error_dict['weekday'][0].code == 'WEEKEND_SCHEDULING_NOT_ALLOWED'

@pytest.mark.parametrize('weekday', [5, 6])
def test_generation_apply_rejects_weekend_entries_before_draft_creation(api_client, data, weekday):
    from scheduling.solver.service import fingerprint
    run = GenerationRun.objects.create(
        timetable=data['timetable'], source_version=data['version'], created_by=data['admin'],
        mode='FILL_GAPS', status='SUCCEEDED', solver_status='OPTIMAL',
        source_fingerprint=fingerprint(data['version']),
        result={'entries':[{'section_id':str(data['section'].pk),'course_offering_id':str(data['offering'].pk),'weekday':weekday,'start_slot_id':str(data['slots'][0].pk),'block_length':1,'entry_type':'LECTURE','faculty':[]}]},
    )
    api_client.force_authenticate(data['admin'])
    response = api_client.post(f'/api/generation-runs/{run.pk}/apply/')
    assert response.status_code == 409
    assert response.data['code'] == 'WEEKEND_SCHEDULING_NOT_ALLOWED'
    assert data['timetable'].versions.count() == 1

def _hybrid_rule(data, entry_type='PRACTICAL', block=2, periods=2):
    data['section'].delivery_policy = Section.DeliveryPolicy.HYBRID
    data['section'].offline_weekday = 0
    data['section'].save()
    data['offering'].default_class_type = entry_type
    data['offering'].required_block_size = block
    data['offering'].weekly_periods = periods
    data['offering'].room_type_requirement = 'LAB' if entry_type == 'PRACTICAL' else 'CLASSROOM'
    data['offering'].save()
    return {'mode':'REBUILD_UNLOCKED','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'}],'session_lengths':[block] * (periods // block)}]}

def test_hybrid_online_practical_has_no_lab_demand_or_room_choice(data):
    from scheduling.solver.engine import resource_capacity_diagnostics
    config = _hybrid_rule(data)
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    assert resource_capacity_diagnostics(data['version'], requirements, config) == []
    candidates, diagnostics = build_candidates(data['version'], requirements, config)
    assert not diagnostics
    online = [candidate for values in candidates.values() for candidate in values if candidate.weekday == 1]
    assert online and all(candidate.delivery_mode == 'ONLINE' and candidate.room_id == '' for candidate in online)
    assert all(candidate.block_length == 2 for candidate in online)

def test_standard_offline_practical_still_consumes_lab_capacity(data):
    from scheduling.solver.engine import resource_capacity_diagnostics
    lab = Room.objects.create(code='LAB1', building='B1', floor='1', capacity=60, room_type='LAB')
    data['offering'].default_class_type='PRACTICAL';data['offering'].required_block_size=1;data['offering'].room_type_requirement='LAB';data['offering'].weekly_periods=31;data['offering'].save()
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]*31}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    shortage=next(error for error in resource_capacity_diagnostics(data['version'],requirements,config) if error['code']=='INSUFFICIENT_ROOM_CAPACITY')
    assert shortage['required_periods']==31 and shortage['available_periods']==25
    candidates,diagnostics=build_candidates(data['version'],requirements[:1],config)
    assert not diagnostics and all(item.room_id.startswith('ROOM_GROUP:') and item.delivery_mode=='OFFLINE' for items in candidates.values() for item in items)
    assert lab.active

@pytest.mark.parametrize(('source_value','expected'), [
    ('MECHANICS LAB', 'MECHANICS_LAB'),
    ('Mechanics Lab', 'MECHANICS_LAB'),
    ('MECHANICS_LAB', 'MECHANICS_LAB'),
    ('Engineering Graphics Lab', 'ENGINEERING_GRAPHICS_LAB'),
    ('ELECTRICAL LAB', 'ELECTRICAL_LAB'),
    ('PRACTICAL', ''),
])
def test_room_type_requirement_normalizes_labels_without_changing_practical_activity(source_value, expected):
    from rooms.services.eligibility import normalize_room_type_requirement
    assert normalize_room_type_requirement(source_value) == expected

def test_solver_matches_display_label_room_requirement_and_keeps_room_restrictions(data):
    section=data['section']
    mechanics=Room.objects.create(code='MECH1',building='B1',floor='1',capacity=60,room_type='MECHANICS_LAB',allowed_year=1)
    Room.objects.create(code='OTHER1',building='B1',floor='1',capacity=60,room_type='WORKSHOP',allowed_year=1)
    data['offering'].default_class_type='PRACTICAL';data['offering'].room_type_requirement='Mechanics Lab';data['offering'].weekly_periods=1;data['offering'].save()
    config={'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config)
    assert not errors and requirements[0].room_type_requirement=='MECHANICS_LAB'
    # Solver candidates refer to equivalence groups; inspect their members.
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile)
    assert not diagnostics
    chosen_groups={candidate.room_id for row in candidates.values() for candidate in row}
    assert chosen_groups
    assert all(profile['_room_group_members'][group]==[str(mechanics.pk)] for group in chosen_groups)

def test_preflight_explains_year_and_reservation_rejections_for_electrical_lab(data):
    from scheduling.solver.engine import resource_capacity_diagnostics
    section=data['section'];section.year=2;section.save(update_fields=['year'])
    data['offering'].room_type_requirement='ELECTRICAL LAB';data['offering'].weekly_periods=1;data['offering'].save()
    electrical=Room.objects.create(code='EL-Y1',building='B1',floor='1',capacity=60,room_type='ELECTRICAL_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    config={'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config)
    assert not errors
    diagnostic=next(error for error in resource_capacity_diagnostics(data['version'],requirements,config) if error['code']=='NO_ELIGIBLE_ROOM')
    assert diagnostic['candidate_rooms_by_type']==[electrical.code]
    assert diagnostic['rejected_by_year']==[{'code':electrical.code,'allowed_year':1,'section_year':2}]
    assert diagnostic['rejected_by_reservation']==[{'code':electrical.code,'reserved_program':section.program.code,'reserved_year':1}]
    assert diagnostic['example_course']=='CS101' and diagnostic['example_section']==section.name

def _configure_exception_case(data, *, year=2, room_code='LGF001', course_code='CS101', active=True, capacity=60, room_type='ELECTRICAL_LAB'):
    from rooms.models import RoomEligibilityException
    from academics.models import Course
    section=data['section'];section.year=year;section.save(update_fields=['year'])
    room=Room.objects.create(code=room_code,building='B1',floor='0',capacity=capacity,room_type=room_type,allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True,active=True)
    course=Course.objects.get(code=course_code)
    exception=RoomEligibilityException.objects.create(room=room,program=section.program,year=2,course=course,allow=True,active=active,reason='test exception')
    offering=data['offering'];offering.course=course;offering.section=section;offering.weekly_periods=1;offering.default_class_type='PRACTICAL';offering.required_block_size=1;offering.room_type_requirement='LAB';offering.preferred_room=room;offering.save()
    rule={'course_offering_id':str(offering.pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}
    return room,exception,{'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[rule]}

def test_exact_active_room_exception_allows_scoped_solver_candidate(data):
    room,exception,config=_configure_exception_case(data,course_code='CS101')
    requirements,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile)
    assert any(candidate.room_id in profile['_room_group_members'] and str(room.pk) in profile['_room_group_members'][candidate.room_id] for rows in candidates.values() for candidate in rows)
    assert any(item['code']=='ROOM_ELIGIBILITY_EXCEPTION_APPLIED' and item['exception_id']==str(exception.pk) for item in diagnostics)

def test_applied_room_exception_is_a_warning_not_a_generation_error(api_client,data):
    room,exception,config=_configure_exception_case(data,course_code='CS101')
    result=run_gen(api_client,data,config)
    assert result['solver_status'] in {'OPTIMAL','FEASIBLE'}
    assert result['statistics']['final_validation_passed'] is True
    assert result['statistics']['blocking_error_count']==0
    assert not result['diagnostics']['errors']
    assert not result['diagnostics']['blocking_errors']
    assert result['statistics']['blocking_error_count']==len(result['diagnostics']['blocking_errors'])
    assert any(item['code']=='ROOM_ELIGIBILITY_EXCEPTION_APPLIED' and item['exception_id']==str(exception.pk) for item in result['diagnostics']['warnings'])

def test_no_candidate_result_is_marked_pre_solver_and_has_no_solver_wall_time(api_client,data):
    data['offering'].weekly_periods=6;data['offering'].required_block_size=6;data['offering'].default_class_type='PRACTICAL';data['offering'].save()
    result=run_gen(api_client,data,{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'entry_type':'PRACTICAL','block_size':6,'session_lengths':[6],'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]})
    assert result['solver_status']=='PRECHECK_FAILED'
    assert result['diagnostics']['solver_invoked'] is False
    assert result['statistics']['stage']=='PRE_SOLVER'
    assert result['statistics']['wall_time'] is None
    assert result['diagnostics']['errors'][0]['code']=='NO_VALID_PLACEMENT'

def test_preflight_accepts_ncs4353_year2_using_only_scoped_lgf001_exception(data):
    from rooms.models import RoomEligibilityException
    from academics.models import Course
    section=data['section'];section.year=2;section.student_strength=55;section.save(update_fields=['year','student_strength'])
    course=Course.objects.create(code='NCS4353',name='Digital Logic Design Lab',credit=1)
    room=Room.objects.create(code='LGF001',building='Engineering Block',floor='0',capacity=60,room_type='ELECTRICAL_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    RoomEligibilityException.objects.create(room=room,program=section.program,year=2,course=course,allow=True,active=True,reason='Historical source timetable')
    data['offering'].course=course;data['offering'].section=section;data['offering'].preferred_room=room;data['offering'].room_type_requirement='LAB';data['offering'].default_class_type='PRACTICAL';data['offering'].weekly_periods=2;data['offering'].required_block_size=2;data['offering'].save()
    from faculty.models import CourseOfferingFaculty
    CourseOfferingFaculty.objects.get_or_create(course_offering=data['offering'],faculty=data['faculty'],defaults={'role':'PRIMARY'})
    config={'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'}],'session_lengths':[2]}]}
    from scheduling.solver.service import preflight_generation
    result=preflight_generation(data['version'],config)
    assert not any(error['code']=='NO_ELIGIBLE_ROOM' for error in result['errors'])
    assert result['statistics'].get('room_eligibility_exceptions_applied')
    assert result['statistics']['room_eligibility_exceptions_applied'][0]['course']=='NCS4353'

@pytest.mark.parametrize(('year','active'),[(2,False),(3,True)])
def test_room_exception_does_not_apply_outside_exact_scope(data,year,active):
    from rooms.models import RoomEligibilityException
    _room,exception,config=_configure_exception_case(data,year=year,room_code='LGF001',active=active,course_code='CS101')
    requirements,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile)
    assert not [candidate for rows in candidates.values() for candidate in rows]
    assert not any(item['code']=='ROOM_ELIGIBILITY_EXCEPTION_APPLIED' for item in diagnostics)

def test_lgf002_remains_ineligible_without_its_own_exception(data):
    from rooms.models import RoomEligibilityException
    section=data['section'];section.year=2;section.save(update_fields=['year'])
    other=Course.objects.get(code='CS101')
    lab=Room.objects.create(code='LGF-002',building='B1',floor='0',capacity=60,room_type='ELECTRICAL_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    data['offering'].course=other;data['offering'].weekly_periods=1;data['offering'].default_class_type='PRACTICAL';data['offering'].room_type_requirement='LAB';data['offering'].preferred_room=lab;data['offering'].save()
    from faculty.models import CourseOfferingFaculty
    CourseOfferingFaculty.objects.get_or_create(course_offering=data['offering'],faculty=data['faculty'],defaults={'role':'PRIMARY'})
    config={'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config)
    assert not [candidate for rows in candidates.values() for candidate in rows]
    assert not RoomEligibilityException.objects.filter(room=lab).exists()

def test_room_exception_is_course_scoped(data):
    _room,_exception,config=_configure_exception_case(data,course_code='CS101')
    other=Course.objects.create(code='CS102',name='Other course',credit=3)
    from faculty.models import CourseOfferingFaculty
    data['offering'].course=other;data['offering'].save(update_fields=['course'])
    CourseOfferingFaculty.objects.get_or_create(course_offering=data['offering'],faculty=data['faculty'],defaults={'role':'PRIMARY'})
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config)
    assert not [candidate for rows in candidates.values() for candidate in rows]

@pytest.mark.parametrize(('room_type','expected'), [('ELECTRICAL_LAB',True),('MECHANICS_LAB',True),('CLASSROOM',False),('WORKSHOP',False),('ENGINEERING_GRAPHICS_LAB',True),('PHYSICS_LAB',True),('COMPUTER_LAB',True),('SUSTAINABLE_CHEMICAL_SCIENCES_LAB',True),('QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB',True)])
def test_generic_lab_requirement_matches_only_registered_lab_types(data,room_type,expected):
    from rooms.services.eligibility import room_satisfies_requirement
    room=Room(code='TEST-LAB',building='B1',floor='1',capacity=30,room_type=room_type)
    assert room_satisfies_requirement(room,'LAB') is expected

def test_specific_room_type_requirement_remains_exact(data):
    from rooms.services.eligibility import room_satisfies_requirement
    room=Room(code='TEST-MECH',building='B1',floor='1',capacity=30,room_type='MECHANICS_LAB')
    assert room_satisfies_requirement(room,'ELECTRICAL_LAB') is False
    assert room_satisfies_requirement(room,'MECHANICS_LAB') is True

def test_matching_exclusive_room_remains_eligible_for_its_reserved_section(data):
    section=data['section']
    room=Room.objects.create(code='Y1-LAB',building='B1',floor='1',capacity=60,room_type='MECHANICS_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    data['offering'].weekly_periods=2;data['offering'].required_block_size=2;data['offering'].default_class_type='PRACTICAL';data['offering'].room_type_requirement='MECHANICS_LAB';data['offering'].save()
    config={'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'entry_type':'PRACTICAL','block_size':2,'session_lengths':[2],'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config)
    assert not diagnostics
    assert candidates[requirements[0].requirement_id]
    assert {room_id for rows in candidates.values() for room_id in [rows[0].room_id]} == {f'ROOM_GROUP:{room.pk}'}

def test_fixed_course_room_overrides_conflicting_generic_room_type(data):
    from academics.models import Course
    from rooms.models import Room
    course=Course.objects.create(code='RBS5151',name='Quantum Physics and Advanced Functional Materials Lab',credit=1)
    room=Room.objects.create(code='105',building='Engineering Block',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB',allowed_year=1,reserved_program=data['section'].program,reserved_year=1,exclusive_reservation=True)
    data['section'].program.code='BTECH-CSE';data['section'].program.save(update_fields=['code'])
    data['offering'].course=course;data['offering'].weekly_periods=2;data['offering'].required_block_size=2;data['offering'].default_class_type='PRACTICAL';data['offering'].room_type_requirement='CLASSROOM';data['offering'].preferred_room=room;data['offering'].save()
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'entry_type':'PRACTICAL','block_size':2,'session_lengths':[2],'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config)
    assert not diagnostics
    assert candidates[requirements[0].requirement_id]
    assert {candidate.room_id for rows in candidates.values() for candidate in rows} == {f'ROOM_GROUP:{room.pk}'}

def test_canonical_room_eligibility_preserves_matching_reservations_and_rejects_other_years(data):
    from rooms.services.eligibility import room_eligibility_error
    section=data['section'];room=Room.objects.create(code='ONLY-Y1',building='B1',floor='1',capacity=60,room_type='CLASSROOM',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    assert room_eligibility_error(room,data['offering'],section) is None
    section.year=3;section.save(update_fields=['year'])
    error=room_eligibility_error(room,data['offering'],section)
    assert error and error['code']=='ROOM_YEAR_RESTRICTION'

def test_canonical_room_eligibility_checks_entire_contiguous_block(data):
    from rooms.services.eligibility import room_eligibility_error
    room=data['room'];slot_ids=[str(data['slots'][1].pk),str(data['slots'][2].pk)]
    blocked={(str(room.pk),0,slot_ids[1])}
    error=room_eligibility_error(room,data['offering'],data['section'],activity_type='PRACTICAL',weekday=0,slot_ids=slot_ids,blocked_slots=blocked)
    assert error and error['code']=='ROOM_UNAVAILABLE'

def test_pool_signature_separates_fixed_rooms_and_exception_scopes(data):
    from rooms.services.eligibility import room_pool_signature
    from institutions.models import Program
    section=data['section']
    room105=Room.objects.create(code='105',building='B1',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB')
    room110=Room.objects.create(code='110',building='B1',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB')
    assert room_pool_signature(room105)!=room_pool_signature(room110)
    lgf001=Room.objects.create(code='LGF001',building='B1',floor='0',capacity=60,room_type='ELECTRICAL_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    lgf002=Room.objects.create(code='LGF-002',building='B1',floor='0',capacity=60,room_type='ELECTRICAL_LAB',allowed_year=1,reserved_program=section.program,reserved_year=1,exclusive_reservation=True)
    exception=RoomEligibilityException.objects.create(room=lgf001,program=section.program,year=2,course=data['offering'].course,allow=True)
    assert room_pool_signature(lgf001,[exception])!=room_pool_signature(lgf002,[])

def test_fixed_room_105_is_course_scoped_and_overrides_stale_type(data):
    from rooms.services.eligibility import room_eligibility_error
    room=Room.objects.create(code='105',building='B1',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB',allowed_year=1,reserved_program=data['section'].program,reserved_year=1,exclusive_reservation=True)
    data['section'].program.code='BTECH-CSE';data['section'].program.save(update_fields=['code'])
    fixed=Course.objects.create(code='RBS5151',name='Quantum Physics and Advanced Functional Materials Lab',credit=1)
    data['offering'].course=fixed;data['offering'].default_class_type='PRACTICAL';data['offering'].room_type_requirement='CLASSROOM';data['offering'].save(update_fields=['course','default_class_type','room_type_requirement'])
    assert room_eligibility_error(room,data['offering'],data['section']) is None
    data['offering'].course=Course.objects.create(code='CS103',name='Any course',credit=3);data['offering'].room_type_requirement='';data['offering'].save(update_fields=['course','room_type_requirement'])
    error=room_eligibility_error(room,data['offering'],data['section'])
    assert error and error['code']=='FIXED_ROOM_RESERVED'
    assert error['message']=="Room 105 is reserved for the Year-1 course 'Quantum Physics and Advanced Functional Materials Lab'."

@pytest.mark.parametrize('room_requirement',['','Any','CLASSROOM','LAB'])
def test_room_105_rejects_every_non_rbs5151_offering_even_in_year1(data,room_requirement):
    from academics.models import Course
    from rooms.services.eligibility import room_eligibility_error
    room=Room.objects.create(code='105',building='B1',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB',allowed_year=1,reserved_program=data['section'].program,reserved_year=1,exclusive_reservation=True)
    data['section'].program.code='BTECH-CSE';data['section'].program.save(update_fields=['code'])
    data['offering'].course=Course.objects.create(code='CS103',name='Programming in C Lab',credit=1)
    data['offering'].room_type_requirement=room_requirement;data['offering'].save(update_fields=['course','room_type_requirement'])
    error=room_eligibility_error(room,data['offering'],data['section'],activity_type='PRACTICAL')
    assert error and error['code']=='FIXED_ROOM_RESERVED'
    assert error['message']=="Room 105 is reserved for the Year-1 course 'Quantum Physics and Advanced Functional Materials Lab'."

@pytest.mark.parametrize(('program_code','year'),[('BTECH-CSE',2),('BTECH-CSE',3),('BTECH-CSE',4),('BTECH-IT',1)])
def test_room_105_rejects_rbs5151_outside_btech_cse_year1(data,program_code,year):
    from academics.models import Course
    from rooms.services.eligibility import room_eligibility_error
    room=Room.objects.create(code='105',building='B1',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB')
    data['section'].program.code=program_code;data['section'].program.save(update_fields=['code'])
    data['section'].year=year;data['section'].save(update_fields=['year'])
    data['offering'].course=Course.objects.create(code='RBS5151',name='Quantum Physics and Advanced Functional Materials Lab',credit=1)
    data['offering'].save(update_fields=['course'])
    error=room_eligibility_error(room,data['offering'],data['section'],activity_type='PRACTICAL')
    assert error and error['code']=='FIXED_ROOM_RESERVED'

def test_room_105_never_enters_unrelated_offering_candidate_pool(api_client,data):
    from academics.models import Course
    from scheduling.solver.engine import build_candidates,build_requirements
    room105=Room.objects.create(code='105',building='B1',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB')
    Room.objects.create(code='GENERIC',building='B1',floor='1',capacity=60,room_type='CLASSROOM')
    data['section'].program.code='BTECH-CSE';data['section'].program.save(update_fields=['code'])
    data['offering'].course=Course.objects.create(code='CS103',name='Programming in C Lab',credit=1)
    data['offering'].weekly_periods=1;data['offering'].room_type_requirement='';data['offering'].save(update_fields=['course','weekly_periods','room_type_requirement'])
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'entry_type':'PRACTICAL','session_lengths':[1],'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile)
    assert not diagnostics
    assert candidates[requirements[0].requirement_id]
    assert all(candidate.room_id != f'ROOM_GROUP:{room105.pk}' for candidate in candidates[requirements[0].requirement_id])
    groups=_group_occurrences(requirements,candidates)
    seed=_constructive_seed(groups,profile.get('_room_group_sizes',{}))
    assert seed['valid']
    assert all(candidate.room_id != f'ROOM_GROUP:{room105.pk}' for _group_id,candidate in seed['assignments'])
    generated=run_gen(api_client,data,config)
    assert generated['solver_status'] in ('FEASIBLE','OPTIMAL')
    assert generated['statistics']['final_validation_passed'] is True
    assert all(entry.get('room_id') != str(room105.pk) for entry in generated['result']['entries'])
    from scheduling.services.validation import validate_generated_entries
    from scheduling.solver.service import fingerprint
    candidate=candidates[requirements[0].requirement_id][0]
    payload={'requirement_id':candidate.requirement_id,'course_offering_id':str(data['offering'].pk),'section_id':str(data['section'].pk),'weekday':candidate.weekday,'start_slot_id':candidate.start_slot_id,'occupied_slot_ids':list(candidate.occupied_slot_ids),'block_length':candidate.block_length,'room_id':str(room105.pk),'delivery_mode':'OFFLINE','entry_type':'PRACTICAL','faculty':[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'}]}
    checked=validate_generated_entries(data['version'],[payload],requirements,'FILL_GAPS',[str(data['section'].pk)])
    assert not checked['valid']
    assert any(error['code']=='FIXED_ROOM_RESERVED' for error in checked['conflicts'])
    leaked=GenerationRun.objects.create(timetable=data['timetable'],source_version=data['version'],created_by=data['admin'],mode='FILL_GAPS',status='SUCCEEDED',solver_status='FEASIBLE',input_config=config,result={'entries':[payload]},source_fingerprint=fingerprint(data['version']))
    api_client.force_authenticate(data['admin'])
    applied=api_client.post(f'/api/generation-runs/{leaked.pk}/apply/')
    assert applied.status_code==409
    assert applied.data['code']=='GENERATED_TIMETABLE_VALIDATION_FAILED'
    assert applied.data['blocking_error_count']==applied.data['error_count']==len(applied.data['conflicts'])
    assert applied.data['errors']==applied.data['conflicts']
    assert all(item.get('message') for item in applied.data['errors'])

def test_generated_payload_validation_and_apply_reject_room_policy_violation(api_client,data):
    from rooms.services.eligibility import room_eligibility_error
    from scheduling.services.validation import validate_generated_entries
    from scheduling.solver.service import fingerprint
    from rooms.models import Room
    room=Room.objects.create(code='ONLY-Y1',building='B1',floor='1',capacity=60,room_type='CLASSROOM',allowed_year=1,reserved_program=data['section'].program,reserved_year=1,exclusive_reservation=True)
    data['section'].year=3;data['section'].save(update_fields=['year'])
    config={'mode':'REBUILD_UNLOCKED','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'}],'entry_type':'LECTURE','session_lengths':[1,1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    entries=[]
    for index,requirement in enumerate(requirements):
        entries.append({'requirement_id':requirement.requirement_id,'course_offering_id':str(data['offering'].pk),'section_id':str(data['section'].pk),'weekday':index,'start_slot_id':str(data['slots'][index].pk),'occupied_slot_ids':[str(data['slots'][index].pk)],'block_length':1,'room_id':str(room.pk),'delivery_mode':'OFFLINE','entry_type':'LECTURE','faculty':[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'}]})
    checked=validate_generated_entries(data['version'],entries,requirements,'REBUILD_UNLOCKED',[str(data['section'].pk)])
    assert not checked['valid']
    assert any(error['code']=='ROOM_YEAR_RESTRICTION' for error in checked['conflicts'])
    run=GenerationRun.objects.create(timetable=data['timetable'],source_version=data['version'],created_by=data['admin'],mode='REBUILD_UNLOCKED',status='SUCCEEDED',solver_status='FEASIBLE',input_config=config,result={'entries':entries},source_fingerprint=fingerprint(data['version']))
    api_client.force_authenticate(data['admin'])
    response=api_client.post(f'/api/generation-runs/{run.pk}/apply/')
    assert response.status_code==409
    assert response.data['code']=='GENERATED_TIMETABLE_VALIDATION_FAILED'
    assert response.data['blocking_error_count']==response.data['error_count']==len(response.data['conflicts'])
    assert response.data['errors']==response.data['conflicts']
    assert not TimetableVersion.objects.exclude(pk=data['version'].pk).exists()

def test_preflight_passes_but_section_capacity_infeasibility_has_assumption_core(api_client,data):
    from academics.models import Course,CourseOffering
    from faculty.models import FacultyAvailability
    from accounts.models import User
    Room.objects.create(code='R2',building='B1',floor='1',capacity=60)
    other_faculty=Faculty.objects.create(user=User.objects.create_user('f2@t.l','P'),employee_code='F2',initials='F2',department=data['faculty'].department)
    other_course=Course.objects.create(code='CS102',name='Second course',credit=3)
    other_offering=CourseOffering.objects.create(semester=data['offering'].semester,section=data['section'],course=other_course,weekly_periods=1,default_class_type='LECTURE')
    data['offering'].weekly_periods=1;data['offering'].save()
    from faculty.models import CourseOfferingFaculty
    CourseOfferingFaculty.objects.create(course_offering=other_offering,faculty=other_faculty,role='PRIMARY')
    for faculty in (data['faculty'],other_faculty):
        for weekday in range(5):
            for slot in data['slots']:
                if weekday!=0 or slot!=data['slots'][0]:FacultyAvailability.objects.create(faculty=faculty,weekday=weekday,time_slot=slot,is_available=False)
    rules=[{'course_offering_id':str(offering.pk),'entry_type':'LECTURE','session_lengths':[1],'faculty':[{'faculty_id':str(faculty.pk)}]} for offering,faculty in ((data['offering'],data['faculty']),(other_offering,other_faculty))]
    result=run_gen(api_client,data,{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':rules})
    assert result['solver_status']=='INFEASIBLE'
    assert result['statistics']['stage']=='CP_SAT'
    assert result['statistics']['constraint_count']>0
    assert any(item['code']=='SECTION_NO_OVERLAP' for item in result['diagnostics']['errors'][0]['core'])

def test_preflight_passes_but_faculty_availability_collision_has_assumption_core(api_client,data):
    from academics.models import Section,Course,CourseOffering
    from faculty.models import FacultyAvailability
    second_section=Section.objects.create(program=data['section'].program,semester=data['section'].semester,year=1,name='B',student_strength=60)
    second_course=Course.objects.create(code='CS102',name='Second course',credit=3)
    second_offering=CourseOffering.objects.create(semester=data['offering'].semester,section=second_section,course=second_course,weekly_periods=1,default_class_type='LECTURE')
    data['offering'].weekly_periods=1;data['offering'].save()
    from faculty.models import CourseOfferingFaculty
    CourseOfferingFaculty.objects.create(course_offering=second_offering,faculty=data['faculty'],role='PRIMARY')
    for weekday in range(5):
        for slot in data['slots']:
            if weekday!=0 or slot!=data['slots'][0]:FacultyAvailability.objects.create(faculty=data['faculty'],weekday=weekday,time_slot=slot,is_available=False)
    rules=[{'course_offering_id':str(offering.pk),'entry_type':'LECTURE','session_lengths':[1],'faculty':[{'faculty_id':str(data['faculty'].pk)}]} for offering in (data['offering'],second_offering)]
    result=run_gen(api_client,data,{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk),str(second_section.pk)],'offering_rules':rules})
    assert result['solver_status']=='INFEASIBLE'
    core=result['diagnostics']['errors'][0]['core']
    assert any(item['code']=='FACULTY_NO_OVERLAP' for item in core)
    assert all('assumption_' not in str(item) for item in core)

def test_preflight_passes_but_contiguous_blocks_can_make_model_infeasible(api_client,data):
    from faculty.models import FacultyAvailability
    data['offering'].weekly_periods=4;data['offering'].required_block_size=2;data['offering'].default_class_type='PRACTICAL';data['offering'].save()
    for weekday in range(5):
        for index,slot in enumerate(data['slots']):
            if weekday!=0 or index>2:FacultyAvailability.objects.create(faculty=data['faculty'],weekday=weekday,time_slot=slot,is_available=False)
    result=run_gen(api_client,data,{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'entry_type':'PRACTICAL','block_size':2,'session_lengths':[2,2],'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]})
    assert result['solver_status']=='INFEASIBLE'
    assert result['statistics']['infeasible_core_count']>0

def test_fixed_room_bottleneck_is_explained_by_room_no_overlap_core(api_client,data):
    from academics.models import Section,Course,CourseOffering
    from faculty.models import CourseOfferingFaculty
    course=Course.objects.create(code='RBS5151',name='Quantum Physics and Advanced Functional Materials Lab',credit=1)
    room=Room.objects.create(code='105',building='Engineering Block',floor='1',capacity=60,room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB',allowed_year=1,reserved_program=data['section'].program,reserved_year=1,exclusive_reservation=True)
    data['section'].program.code='BTECH-CSE';data['section'].program.save(update_fields=['code'])
    second_section=Section.objects.create(program=data['section'].program,semester=data['section'].semester,year=1,name='B',student_strength=60)
    offerings=[]
    for section in (data['section'],second_section):
        offering=CourseOffering.objects.create(semester=data['offering'].semester,section=section,course=course,weekly_periods=2,default_class_type='PRACTICAL',required_block_size=2,room_type_requirement='CLASSROOM',preferred_room=room)
        CourseOfferingFaculty.objects.create(course_offering=offering,faculty=data['faculty'],role='PRIMARY');offerings.append(offering)
    data['offering'].weekly_periods=1;data['offering'].default_class_type='LECTURE';data['offering'].room_type_requirement='CLASSROOM';data['offering'].save()
    CourseOfferingFaculty.objects.get_or_create(course_offering=data['offering'],faculty=data['faculty'],defaults={'role':'PRIMARY'})
    from rooms.models import RoomAvailability
    for weekday in range(5):
        for index,slot in enumerate(data['slots']):
            if weekday!=0 or index>1:RoomAvailability.objects.create(room=room,weekday=weekday,time_slot=slot,status='BLOCKED')
    rules=[{'course_offering_id':str(data['offering'].pk),'entry_type':'LECTURE','session_lengths':[1],'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]
    rules += [{'course_offering_id':str(offering.pk),'entry_type':'PRACTICAL','block_size':2,'session_lengths':[2],'faculty':[{'faculty_id':str(data['faculty'].pk)}]} for offering in offerings]
    result=run_gen(api_client,data,{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk),str(second_section.pk)],'offering_rules':rules})
    assert result['solver_status']=='INFEASIBLE'
    assert result['statistics']['infeasible_core_count']>0
    assert result['diagnostics']['errors'][0]['core']

@pytest.mark.parametrize(('capacity','active'),[(40,True),(60,False)])
def test_room_exception_never_bypasses_capacity_or_active_status(data,capacity,active):
    room,_exception,config=_configure_exception_case(data,course_code='CS101',capacity=capacity)
    room.active=active;room.save(update_fields=['active'])
    if capacity< data['section'].student_strength:
        data['section'].student_strength=60;data['section'].save(update_fields=['student_strength'])
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config)
    assert not [candidate for rows in candidates.values() for candidate in rows]

@pytest.mark.parametrize(('year','code'),[(1,'516'),(2,'604')])
def test_mtech_reserved_rooms_remain_exact_under_exception_policy(data,year,code):
    from institutions.models import Program
    mtech=Program.objects.create(department=data['section'].program.department,name='M.Tech Computer Science',code='MTECH-CSE',duration_years=2)
    section=data['section'];section.program=mtech;section.year=year;section.save(update_fields=['program','year'])
    reserved=Room.objects.create(code=code,building='B1',floor='1',capacity=60,room_type='CLASSROOM',exclusive_reservation=True,reserved_program=mtech,reserved_year=year)
    wrong=Room.objects.create(code='WRONG-MTECH',building='B1',floor='1',capacity=60,room_type='CLASSROOM',exclusive_reservation=False)
    data['offering'].weekly_periods=1;data['offering'].room_type_requirement='';data['offering'].save()
    from faculty.models import CourseOfferingFaculty
    CourseOfferingFaculty.objects.get_or_create(course_offering=data['offering'],faculty=data['faculty'],defaults={'role':'PRIMARY'})
    config={'mode':'FILL_GAPS','section_ids':[str(section.pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    profile={};candidates,diagnostics=build_candidates(data['version'],requirements,config,profile)
    assert not diagnostics
    groups={candidate.room_id for rows in candidates.values() for candidate in rows}
    assert groups and all(profile['_room_group_members'][group]==[str(reserved.pk)] for group in groups)
    assert str(wrong.pk) not in {room_id for group in groups for room_id in profile['_room_group_members'][group]}

def test_hybrid_online_lecture_can_be_generated_without_physical_room(api_client, data):
    config=_hybrid_rule(data,entry_type='LECTURE',block=1,periods=1)
    data['offering'].room_type_requirement='NON_EXISTENT_ROOM';data['offering'].save()
    run=run_gen(api_client,data,config)
    entry=run['result']['entries'][0]
    assert entry['delivery_mode']=='ONLINE' and entry['room_id'] is None
    assert run['statistics']['online_periods']==1 and run['statistics']['offline_periods']==0

def test_hybrid_delivery_mode_is_persisted_from_section_policy_on_apply(api_client, data):
    config = _hybrid_rule(data, entry_type='LECTURE', block=1, periods=1)
    data['offering'].room_type_requirement = 'NON_EXISTENT_ROOM'; data['offering'].save()
    run = run_gen(api_client, data, config)
    api_client.force_authenticate(data['admin'])
    response = api_client.post(f"/api/generation-runs/{run['id']}/apply/")
    assert response.status_code == 200, response.data
    applied = TimetableVersion.objects.get(id=response.data['applied_version'])
    entry = applied.entries.get(course_offering=data['offering'])
    assert entry.delivery_mode == 'ONLINE'
    assert entry.room_id is None

def test_standard_lecture_candidates_require_compatible_physical_room(data):
    data['offering'].weekly_periods=1;data['offering'].room_type_requirement='CLASSROOM';data['offering'].save()
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config)
    assert not diagnostics and all(item.delivery_mode=='OFFLINE' and item.room_id for items in candidates.values() for item in items)

def test_tutorial_uses_unit_periods_even_with_legacy_block_size_two(data):
    data['offering'].default_class_type = 'TUTORIAL'
    data['offering'].weekly_periods = 2
    data['offering'].required_block_size = 2
    data['offering'].save()
    requirements, errors = build_requirements(data['version'], {'mode': 'REBUILD_UNLOCKED', 'section_ids': [str(data['section'].id)], 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1, 1], 'entry_type': 'TUTORIAL'}]})
    assert not errors
    assert [item.block_length for item in requirements] == [1, 1]

def test_hybrid_scope_requires_configured_offline_weekday(data):
    from scheduling.solver.service import preflight_generation
    data['section'].delivery_policy=Section.DeliveryPolicy.HYBRID;data['section'].offline_weekday=None;data['section'].save()
    config=_hybrid_rule(data)
    data['section'].offline_weekday=None;data['section'].save()
    result=preflight_generation(data['version'],config)
    assert not result['valid']
    assert any(error['code']=='MISSING_OFFLINE_DAY' for error in result['errors'])

def test_hybrid_online_candidates_still_respect_fixed_faculty_and_section_occupancy(data):
    from academics.models import Section, Course, CourseOffering
    other=Section.objects.create(program=data['section'].program,semester=data['section'].semester,year=1,name='B')
    other_course=Course.objects.create(code='CS102',name='Other',credit=3,short_code='O')
    other_offering=CourseOffering.objects.create(semester=data['offering'].semester,section=other,course=other_course,weekly_periods=1)
    fixed=ScheduleEntry.objects.create(version=data['version'],section=other,course_offering=other_offering,weekday=1,start_slot=data['slots'][0],room=data['room'],locked=True)
    ScheduleEntryFaculty.objects.create(schedule_entry=fixed,faculty=data['faculty'])
    section_course=Course.objects.create(code='CS103',name='Fixed in target section',credit=3,short_code='F')
    section_offering=CourseOffering.objects.create(semester=data['offering'].semester,section=data['section'],course=section_course,weekly_periods=1)
    ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=section_offering,weekday=2,start_slot=data['slots'][1],room=data['room'],locked=True)
    rule=_hybrid_rule(data,entry_type='LECTURE',block=1,periods=1)
    rule['mode']='REBUILD_UNLOCKED'
    requirements,errors=build_requirements(data['version'],rule);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,rule)
    assert not diagnostics
    assert not any(item.weekday==1 and item.start_slot_id==str(data['slots'][0].pk) for items in candidates.values() for item in items)
    assert not any(item.weekday==2 and item.start_slot_id==str(data['slots'][1].pk) for items in candidates.values() for item in items)
    assert Course.objects.filter(pk=data['offering'].course_id).exists()

def test_section_break_test(api_client, data):
    data['slots'][1].is_break = True; data['slots'][1].save()
    ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1, locked=True)
    data['offering'].weekly_periods = 2; data['offering'].save()
    payload = {'mode': 'FILL_GAPS', 'soft_constraints': {'minimize_section_gaps': 100, 'spread_course_days':0, 'balance_section_load':0}, 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}], 'random_seed': 42}
    res = run_gen(api_client, data, payload)
    assert res['status'] == 'SUCCEEDED'
    assert res['result']['entries'][0]['start_slot_id'] == str(data['slots'][2].id)

def test_section_multi_period_test(api_client, data):
    data['offering'].weekly_periods = 2; data['offering'].required_block_size = 2; data['offering'].save()
    payload = {'mode': 'FILL_GAPS', 'soft_constraints': {'minimize_section_gaps': 10, 'spread_course_days':0}, 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [2], 'block_size': 2, 'entry_type': 'PRACTICAL'}], 'random_seed': 42}
    res = run_gen(api_client, data, payload)
    assert res['status'] == 'SUCCEEDED'
    assert res['objective_score'] == 0

def test_faculty_gap_test(api_client, data):
    ScheduleEntryFaculty.objects.create(schedule_entry=ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1, locked=True), faculty=data['faculty'], role='PRIMARY')
    data['offering'].weekly_periods = 2; data['offering'].save()
    RoomAvailability.objects.create(room=data['room'], weekday=0, time_slot=data['slots'][3], status='BLOCKED')
    payload = {'mode': 'FILL_GAPS', 'soft_constraints': {'minimize_faculty_gaps': 100, 'spread_course_days':0, 'balance_section_load':0}, 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}], 'random_seed': 42}
    res = run_gen(api_client, data, payload)
    assert res['status'] == 'SUCCEEDED'
    assert res['result']['entries'][0]['start_slot_id'] == str(data['slots'][1].id)

def test_fill_gaps_apply_integration(api_client, data):
    ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1, locked=True)
    data['offering'].weekly_periods = 2; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id), 'role': 'PRIMARY'}], 'session_lengths': [1]}]})
    api_client.force_authenticate(data['admin'])
    res = api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    assert res.status_code == 200
    v2 = TimetableVersion.objects.get(id=res.data['applied_version'])
    assert v2.entries.count() == 2
    assert v2.status == TimetableVersion.Status.DRAFT

def test_validation_failure_response(api_client, data):
    data['offering'].weekly_periods = 1; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}]})
    data['room'].capacity = 1; data['room'].save()
    api_client.force_authenticate(data['admin'])
    res = api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    assert res.status_code == 409
    assert res.data['code'] == 'GENERATED_TIMETABLE_VALIDATION_FAILED'
    assert 'conflicts' in res.data

def test_rebuild_unlocked_apply_integration(api_client, data):
    e1 = ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1, locked=True)
    e2 = ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][1], room=data['room'], block_length=1, locked=False)
    data['offering'].weekly_periods = 2; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'REBUILD_UNLOCKED', 'section_ids': [str(data['section'].id)], 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}]})
    api_client.force_authenticate(data['admin'])
    res = api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    assert res.status_code == 200, res.data
    v2 = TimetableVersion.objects.get(id=res.data['applied_version'])
    assert v2.entries.count() == 2
    assert v2.entries.filter(locked=True).count() == 1
    assert v2.entries.filter(locked=False).count() == 1


def test_locked_two_period_official_blocks_keep_exact_times_and_room_occupancy(data):
    from common.models import TimeSlot
    from scheduling.solver.service import preflight_generation

    data['slots'][1].label = '10:00-11:00'
    data['slots'][1].save(update_fields=['label'])
    data['slots'][2].label = '11:00-12:00'
    data['slots'][2].save(update_fields=['label'])
    afternoon = [
        TimeSlot.objects.create(
            template=data['slots'][0].template,
            label=f'{hour:02d}:00-{hour + 1:02d}:00',
            start_time=f'{hour:02d}:00', end_time=f'{hour + 1:02d}:00', order=hour - 8,
        )
        for hour in (14, 15)
    ]
    data['offering'].weekly_periods = 4
    data['offering'].required_block_size = 2
    data['offering'].save()
    seminar = ScheduleEntry.objects.create(
        version=data['version'], section=data['section'], course_offering=data['offering'],
        weekday=1, start_slot=afternoon[0], block_length=2, room=data['room'],
        entry_type='PRACTICAL', locked=True,
    )
    ai_lab = ScheduleEntry.objects.create(
        version=data['version'], section=data['section'], course_offering=data['offering'],
        weekday=3, start_slot=data['slots'][1], block_length=2, room=data['room'],
        entry_type='PRACTICAL', locked=True,
    )
    ScheduleEntryFaculty.objects.create(schedule_entry=seminar, faculty=data['faculty'])
    ScheduleEntryFaculty.objects.create(schedule_entry=ai_lab, faculty=data['faculty'])

    assert (seminar.weekday, seminar.start_slot.label, seminar.block_length) == (1, '14:00-15:00', 2)
    assert (ai_lab.weekday, ai_lab.start_slot.label, ai_lab.block_length) == (3, '10:00-11:00', 2)
    preflight = preflight_generation(
        data['version'], {'mode': 'REBUILD_UNLOCKED', 'section_ids': [str(data['section'].pk)]},
        include_internal=True,
    )
    assert preflight['valid']
    assert preflight['statistics']['preserved_entry_count'] == 2
    assert preflight['statistics']['preserved_period_count'] == 4
    assert preflight['_requirements'] == []


def test_mtcsai2_official_nine_locked_periods_and_three_cloud_occurrences(data):
    from faculty.models import CourseOfferingFaculty
    from scheduling.services.validation import validate_version
    from scheduling.solver.service import preflight_generation

    section = data['section']
    section.name = 'MTCSAI-2'
    section.year = 2
    section.program.code = 'MTECH-CSEAI'
    section.program.name = 'M.Tech CSE-AI'
    section.program.save(update_fields=['code', 'name'])
    section.save(update_fields=['name', 'year'])
    cloud = data['offering']
    cloud.course.code = 'GE14241'
    cloud.course.name = 'Cloud Computing'
    cloud.course.save(update_fields=['code', 'name'])
    cloud.weekly_periods = 4
    cloud.save(update_fields=['weekly_periods'])
    CourseOfferingFaculty.objects.create(course_offering=cloud, faculty=data['faculty'])

    other_section = Section.objects.create(
        program=section.program, semester=section.semester, year=1, name='MTCSAI-1',
    )
    other_cloud = CourseOffering.objects.create(
        semester=section.semester, section=other_section, course=cloud.course, weekly_periods=4,
    )
    room = data['room']
    room.code = '604'
    room.allowed_year = 2
    room.reserved_program = section.program
    room.reserved_year = 2
    room.exclusive_reservation = True
    room.save(update_fields=['code', 'allowed_year', 'reserved_program', 'reserved_year', 'exclusive_reservation'])

    from academics.models import Course
    from faculty.models import Faculty
    course_specs = [
        ('MAI1301', 'Research Methodology and IPR', 4),
        ('MAI1351', 'Seminar', 1),
        ('MAI1352', 'Dissertation-1', 1),
    ]
    offerings = {'GE14241': cloud}
    for code, name, periods in course_specs:
        course = Course.objects.create(code=code, name=name, credit=1)
        offerings[code] = CourseOffering.objects.create(
            semester=section.semester, section=section, course=course,
            weekly_periods=periods, default_class_type='LECTURE' if periods > 1 else 'PRACTICAL',
        )
        CourseOfferingFaculty.objects.create(course_offering=offerings[code], faculty=data['faculty'])

    slots = data['slots']
    for index, slot in enumerate(slots):
        hour = 9 + index
        slot.label = f'{hour:02d}:00-{hour + 1:02d}:00'
        slot.start_time = f'{hour:02d}:00'
        slot.end_time = f'{hour + 1:02d}:00'
        slot.is_break = hour == 13
        slot.save(update_fields=['label', 'start_time', 'end_time', 'is_break'])
    from common.models import TimeSlot
    for hour, order in ((14, 6), (15, 7), (16, 8)):
        TimeSlot.objects.create(
            template=slots[0].template, label=f'{hour:02d}:00-{hour + 1:02d}:00',
            start_time=f'{hour:02d}:00', end_time=f'{hour + 1:02d}:00', order=order,
        )
    slots_by_label = {slot.label: slot for slot in TimeSlot.objects.filter(template=slots[0].template)}
    official_rows = [
        (0, '14:00-15:00', 'GE14241'),
        (1, '11:00-12:00', 'MAI1301'),
        (2, '11:00-12:00', 'MAI1352'),
        (2, '12:00-13:00', 'MAI1301'),
        (3, '09:00-10:00', 'GE14241'),
        (3, '10:00-11:00', 'MAI1351'),
        (3, '11:00-12:00', 'MAI1301'),
        (4, '12:00-13:00', 'MAI1301'),
        (4, '16:00-17:00', 'GE14241'),
    ]
    entries = []
    for weekday, label, code in official_rows:
        entry = ScheduleEntry.objects.create(
            version=data['version'], section=section, course_offering=offerings[code],
            weekday=weekday, start_slot=slots_by_label[label], block_length=1,
            room=room, entry_type=offerings[code].default_class_type, locked=True,
        )
        ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=data['faculty'])
        entries.append(entry)

    cloud.weekly_periods = 3
    cloud.save(update_fields=['weekly_periods'])
    persisted = list(data['version'].entries.filter(section=section).select_related('course_offering__course', 'room', 'start_slot'))
    counts = {}
    for entry in persisted:
        counts[entry.course_offering.course.code] = counts.get(entry.course_offering.course.code, 0) + entry.block_length
    assert counts == {'GE14241': 3, 'MAI1301': 4, 'MAI1352': 1, 'MAI1351': 1}
    assert len(persisted) == 9 and all(entry.locked and entry.room.code == '604' for entry in persisted)
    assert other_cloud.weekly_periods == 4
    assert not CourseOffering.objects.filter(section=section, course__code__icontains='LIB').exists()
    assert validate_version(data['version'])['valid']
    preflight = preflight_generation(
        data['version'], {'mode': 'REBUILD_UNLOCKED', 'section_ids': [str(section.pk)]}, include_internal=True,
    )
    assert preflight['valid'] and not preflight['_requirements']
    assert preflight['statistics']['preserved_entry_count'] == 9
    assert preflight['statistics']['preserved_period_count'] == 9

def test_multiple_faculty_apply(api_client, data):
    f2 = Faculty.objects.create(user=User.objects.create_user('f2@t.l','P'), employee_code='F2', initials='F2', department=data['faculty'].department)
    data['offering'].weekly_periods = 1; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id), 'role': 'PRIMARY'}, {'faculty_id': str(f2.id), 'role': 'CO_FACULTY'}], 'session_lengths': [1]}]})
    api_client.force_authenticate(data['admin'])
    res = api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    assert res.status_code == 200
    v2 = TimetableVersion.objects.get(id=res.data['applied_version'])
    e = v2.entries.first()
    assert e.faculty_assignments.count() == 2
    roles = set(e.faculty_assignments.values_list('role', flat=True))
    assert 'PRIMARY' in roles and 'CO_FACULTY' in roles

def test_transaction_rollback(api_client, data):
    data['offering'].weekly_periods = 1; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}]})
    data['room'].capacity = 1; data['room'].save()
    api_client.force_authenticate(data['admin'])
    res = api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    assert res.status_code == 409
    assert TimetableVersion.objects.filter(timetable=data['timetable']).count() == 1
    run = GenerationRun.objects.get(id=run_data['id'])
    assert run.status == 'SUCCEEDED'
    assert run.applied_version is None

def test_duplicate_apply(api_client, data):
    data['offering'].weekly_periods = 1; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}]})
    api_client.force_authenticate(data['admin'])
    api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    res = api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    assert res.status_code == 409
    assert res.data['code'] == 'GENERATION_ALREADY_APPLIED'

def test_stale_fingerprint(api_client, data):
    data['offering'].weekly_periods = 1; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}]})
    ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1)
    api_client.force_authenticate(data['admin'])
    res = api_client.post(f"/api/generation-runs/{run_data['id']}/apply/")
    assert res.status_code == 409
    assert res.data['code'] == 'SOURCE_VERSION_CHANGED'

def test_foreign_section_scope(api_client, data):
    api_client.force_authenticate(data['admin'])
    import uuid
    res = api_client.post(f"/api/versions/{data['version'].id}/generation-runs/", {'section_ids': [str(uuid.uuid4())]}, format='json')
    assert res.status_code == 400
    assert 'INVALID_SECTION_SCOPE' in str(res.data)

def test_invalid_offering_scope(api_client, data):
    api_client.force_authenticate(data['admin'])
    import uuid
    res = api_client.post(f"/api/versions/{data['version'].id}/generation-runs/", {'offering_rules': [{'course_offering_id': str(uuid.uuid4())}]}, format='json')
    assert res.status_code == 400
    assert 'INVALID_OFFERING_SCOPE' in str(res.data)

def test_unknown_uuid(api_client, data):
    api_client.force_authenticate(data['admin'])
    res = api_client.post(f"/api/versions/00000000-0000-0000-0000-000000000000/generation-runs/", {}, format='json')
    assert res.status_code == 404

def test_generation_rbac(api_client, data):
    viewer = User.objects.create_user('v@v.v', 'P', role=Role.READ_ONLY_VIEWER)
    api_client.force_authenticate(viewer)
    res = api_client.post(f"/api/versions/{data['version'].id}/generation-runs/", {}, format='json')
    assert res.status_code == 403

def test_audit_generated(api_client, data):
    data['offering'].weekly_periods = 1; data['offering'].save()
    run_data = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}]})
    from audit.models import AuditEvent
    assert AuditEvent.objects.filter(event_type='GENERATION_STARTED').exists()
    assert AuditEvent.objects.filter(event_type='GENERATION_SUCCEEDED').exists()

def test_combined_objective(api_client, data):
    data['offering'].weekly_periods = 2; data['offering'].save()
    payload = {'mode': 'FILL_GAPS', 'soft_constraints': {'minimize_section_gaps': 10, 'spread_course_days':10, 'balance_section_load':10}, 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1, 1]}], 'random_seed': 42}
    res = run_gen(api_client, data, payload)
    assert res['status'] == 'SUCCEEDED'

def test_disabled_gap_objective(api_client, data):
    data['offering'].weekly_periods = 2; data['offering'].save()
    payload = {'mode': 'FILL_GAPS', 'soft_constraints': {'minimize_section_gaps': 0, 'minimize_faculty_gaps': 0}, 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1, 1]}], 'random_seed': 42}
    res = run_gen(api_client, data, payload)
    assert res['status'] == 'SUCCEEDED'


OFFICIAL_YEAR4_OFFLINE_DAYS = [
    ('CS-4A', 0), ('CS-4B', 1), ('CS-4C', 2), ('CS-4D', 3), ('CS-4E', 4),
    ('CS-4F', 0), ('CS-4G', 1), ('CS-4H', 2), ('CS-4I', 4),
    ('CS-4J', 0), ('CS-4K', 4), ('CS-4L', 1),
    ('CSE(AI)-4A', 3), ('CSE(AI)-4B', 0), ('CSE(AI)-4C', 1),
    ('CSE(AI)-4D', 1), ('CSE(AI)-4E', 4), ('CSE(AI)-4F', 0),
    ('CSE(CCML)-4A', 3), ('CSE(IOTBC)-4A', 1),
]


def _create_period_hybrid_policy(data, section_name, weekday):
    from academics.models import SectionDeliveryPolicy
    section = data['section']
    section.name = section_name
    section.year = 4
    section.save(update_fields=['name', 'year'])
    return SectionDeliveryPolicy.objects.create(
        section=section,
        academic_session=section.semester.session,
        semester=section.semester,
        mode='HYBRID',
        offline_weekday=weekday,
        source='OFFICIAL_TIMETABLE',
        active=True,
    )


@pytest.mark.parametrize(('section_name', 'offline_weekday'), OFFICIAL_YEAR4_OFFLINE_DAYS)
def test_official_year4_hybrid_mapping_is_a_hard_candidate_rule(data, section_name, offline_weekday):
    _create_period_hybrid_policy(data, section_name, offline_weekday)
    data['offering'].weekly_periods = 1
    data['offering'].save(update_fields=['weekly_periods'])
    config = {'mode': 'FILL_GAPS', 'section_ids': [str(data['section'].pk)], 'offering_rules': [{
        'course_offering_id': str(data['offering'].pk),
        'faculty': [{'faculty_id': str(data['faculty'].pk), 'role': 'PRIMARY'}],
        'session_lengths': [1],
    }]}
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    profile = {}
    candidates, diagnostics = build_candidates(data['version'], requirements, config, profile)
    placements = [candidate for options in candidates.values() for candidate in options]
    assert not diagnostics and placements
    assert {candidate.weekday for candidate in placements if candidate.delivery_mode == 'OFFLINE'} == {offline_weekday}
    assert all(candidate.room_id for candidate in placements if candidate.delivery_mode == 'OFFLINE')
    assert all(not candidate.room_id for candidate in placements if candidate.delivery_mode == 'ONLINE')
    assert all(candidate.delivery_mode == ('OFFLINE' if candidate.weekday == offline_weekday else 'ONLINE') for candidate in placements)
    assert profile['hybrid_profile']['offerings'] == 1
    assert profile['hybrid_profile']['online_candidates'] == sum(candidate.delivery_mode == 'ONLINE' for candidate in placements)
    assert profile['hybrid_profile']['offline_candidates'] == sum(candidate.delivery_mode == 'OFFLINE' for candidate in placements)
    from scheduling.solver.service import _build_occurrence_model, _constructive_seed, _group_occurrences
    groups = _group_occurrences(requirements, candidates)
    required_offline = {str(data['section'].pk)}
    seed = _constructive_seed(groups, profile['_room_group_sizes'], required_offline_section_ids=required_offline)
    assert seed['valid'] and any(candidate.delivery_mode == 'OFFLINE' for _, candidate in seed['assignments'])
    model, variables, _, _ = _build_occurrence_model(groups, profile['_room_group_sizes'], required_offline_section_ids=required_offline)
    assert len(variables) == len(placements)
    assert len(model.Proto().variables) == len(placements)
    solver = cp_model.CpSolver()
    assert solver.Solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert any(solver.Value(variable) and candidate.delivery_mode == 'OFFLINE' for variable, candidate, _ in variables)


def test_prefetched_delivery_policy_resolution_avoids_inner_loop_queries(data, django_assert_num_queries):
    from academics.delivery import get_section_delivery_policy
    _create_period_hybrid_policy(data, 'CSE(AI)-4A', 3)
    section = Section.objects.select_related('semester__session').prefetch_related('period_delivery_policies').get(pk=data['section'].pk)
    with django_assert_num_queries(0):
        assert get_section_delivery_policy(section, section.semester) == ('HYBRID', 3)
        assert get_section_delivery_policy(section, section.semester) == ('HYBRID', 3)


def test_fixed_occupancy_prefetches_template_slots_for_many_entries(data, django_assert_num_queries):
    from scheduling.solver.engine import fixed_occupancy
    ScheduleEntry.objects.bulk_create([
        ScheduleEntry(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=index%5,start_slot=data['slots'][0],room=data['room'],block_length=1,locked=True)
        for index in range(20)
    ])
    with django_assert_num_queries(3):
        section,_faculty,room=fixed_occupancy(data['version'],'REBUILD_UNLOCKED',[str(data['section'].pk)])
    assert section and room


def test_room_capacity_preflight_uses_batched_exception_lookup(data):
    from dataclasses import replace
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    from scheduling.solver.engine import resource_capacity_diagnostics
    from scheduling.solver.service import preflight_generation
    data['room'].allowed_year=2;data['room'].save(update_fields=['allowed_year'])
    config={'mode':'REBUILD_UNLOCKED','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1,1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    repeated=[replace(requirements[0],requirement_id=f'requirement-{index}') for index in range(40)]
    with CaptureQueriesContext(connection) as queries:
        diagnostics=resource_capacity_diagnostics(data['version'],repeated,config)
    assert diagnostics and len(queries)<=12


def test_period_policy_overrides_legacy_section_policy_only_in_its_semester(data):
    from academics.delivery import get_section_delivery_policy
    from academics.models import AcademicSession, SectionDeliveryPolicy, Semester
    _create_period_hybrid_policy(data, 'CSE(AI)-4A', 3)
    assert get_section_delivery_policy(data['section']) == ('HYBRID', 3)
    next_session = AcademicSession.objects.create(
        institution=data['section'].semester.session.institution,
        name='2027-28', start_date='2027-04-01', end_date='2028-03-31',
    )
    next_semester = Semester.objects.create(
        session=next_session, name='Odd Semester', number=1, type='ODD',
        start_date='2027-07-01', end_date='2027-12-31',
    )
    assert get_section_delivery_policy(data['section'], next_semester) == ('STANDARD', None)
    assert SectionDeliveryPolicy.objects.filter(section=data['section']).count() == 1


def test_period_policy_generation_validator_requires_room_for_offline_and_forbids_room_online(data):
    from academics.delivery import expected_delivery_mode
    from academics.models import SectionDeliveryPolicy
    from scheduling.solver.engine import candidate_dict
    from scheduling.services.validation import validate_generated_entries
    _create_period_hybrid_policy(data, 'CS-4F', 0)
    data['offering'].weekly_periods = 1
    data['offering'].save(update_fields=['weekly_periods'])
    config = {'mode': 'FILL_GAPS', 'section_ids': [str(data['section'].pk)], 'offering_rules': [{
        'course_offering_id': str(data['offering'].pk),
        'faculty': [{'faculty_id': str(data['faculty'].pk), 'role': 'PRIMARY'}],
        'session_lengths': [1],
    }]}
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    candidates, diagnostics = build_candidates(data['version'], requirements, config)
    assert not diagnostics
    placements = [candidate for values in candidates.values() for candidate in values]
    offline = next(item for item in placements if item.delivery_mode == 'OFFLINE')
    online = next(item for item in placements if item.delivery_mode == 'ONLINE')
    assert expected_delivery_mode(data['section'], 0) == 'OFFLINE'
    online_only = validate_generated_entries(data['version'], [candidate_dict(online)], requirements)
    assert any(item['code'] == 'OFFLINE_DAY_MISSING' for item in online_only['conflicts'])
    assert not any(item['code'] == 'ROOM_REQUIRED' for item in online_only['conflicts'])
    valid = candidate_dict(offline) | {'room_id': str(data['room'].pk)}
    assert validate_generated_entries(data['version'], [valid], requirements)['valid']
    online_with_room = candidate_dict(online) | {'room_id': str(data['room'].pk)}
    result = validate_generated_entries(data['version'], [online_with_room], requirements)
    assert any(item['code'] == 'ONLINE_ROOM_NOT_ALLOWED' for item in result['conflicts'])
    assert not any(item['code'] == 'ROOM_REQUIRED' for item in result['conflicts'])
    offline_without_room = candidate_dict(offline) | {'room_id': ''}
    result = validate_generated_entries(data['version'], [offline_without_room], requirements)
    assert any(item['code'] == 'ROOM_REQUIRED' for item in result['conflicts'])
    wrong_day_offline = candidate_dict(online) | {'delivery_mode': 'OFFLINE', 'room_id': str(data['room'].pk)}
    result = validate_generated_entries(data['version'], [wrong_day_offline], requirements)
    assert any(item['code'] == 'OFFLINE_DAY_MISMATCH' for item in result['conflicts'])


def test_period_hybrid_manual_validation_keeps_roomless_online_and_checks_faculty(data):
    from academics.models import SectionDeliveryPolicy
    from scheduling.services.validation import validate_entry, validate_version
    from scheduling.models import ScheduleEntry, ScheduleEntryFaculty
    _create_period_hybrid_policy(data, 'CS-4F', 0)
    common = {
        'section': str(data['section'].pk), 'course_offering': str(data['offering'].pk),
        'start_slot': str(data['slots'][0].pk), 'block_length': 1,
        'faculty_ids': [str(data['faculty'].pk)],
    }
    online = common | {'weekday': 1, 'room': None}
    assert not validate_entry(data['version'], online)
    physical_online_day = common | {'weekday': 1, 'room': str(data['room'].pk)}
    assert any(item['code'] == 'OFFLINE_DAY_MISMATCH' for item in validate_entry(data['version'], physical_online_day))
    assert any(item.get('code', item.get('type')) == 'ONLINE_ROOM_NOT_ALLOWED'
               for item in validate_entry(data['version'], physical_online_day))
    offline_without_room = common | {'weekday': 0, 'room': None}
    assert any(item['code'] == 'ROOM_REQUIRED' for item in validate_entry(data['version'], offline_without_room))
    entry = ScheduleEntry.objects.create(
        version=data['version'], section=data['section'], course_offering=data['offering'],
        weekday=1, start_slot=data['slots'][0], room=None, delivery_mode='ONLINE',
    )
    ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=data['faculty'])
    conflicts = validate_entry(data['version'], online)
    assert any(item.get('code', item.get('type')) == 'FACULTY_CLASH' for item in conflicts)
    assert any(item.get('code', item.get('type')) == 'SECTION_CLASH' for item in conflicts)
    assert any(item.get('code', item.get('type')) == 'OFFLINE_DAY_MISSING' for item in validate_version(data['version'])['conflicts'])


def test_standard_offline_manual_entry_still_requires_room(data):
    from scheduling.services.validation import validate_entry
    data['offering'].weekly_periods = 1
    data['offering'].save(update_fields=['weekly_periods'])
    conflicts = validate_entry(data['version'], {
        'section': str(data['section'].pk), 'course_offering': str(data['offering'].pk),
        'weekday': 0, 'start_slot': str(data['slots'][0].pk), 'block_length': 1,
        'room': None, 'faculty_ids': [str(data['faculty'].pk)],
    })
    assert any(item.get('code', item.get('type')) == 'ROOM_REQUIRED' for item in conflicts)


def test_version_validator_uses_each_entry_delivery_mode_for_room_checks(data):
    import uuid
    from scheduling.services.validation import validate_version
    _hybrid_rule(data, entry_type='LECTURE', block=1, periods=2)
    online = ScheduleEntry.objects.create(
        id=uuid.UUID(int=1), version=data['version'], section=data['section'],
        course_offering=data['offering'], weekday=1, start_slot=data['slots'][0],
        room=None, entry_type='LECTURE', delivery_mode='ONLINE',
    )
    ScheduleEntry.objects.create(
        id=uuid.UUID(int=2), version=data['version'], section=data['section'],
        course_offering=data['offering'], weekday=0, start_slot=data['slots'][1],
        room=data['room'], entry_type='LECTURE', delivery_mode='OFFLINE',
    )

    result = validate_version(data['version'])

    assert online.room_id is None
    assert not [item for item in result['conflicts']
                if item.get('code', item.get('type')) == 'ROOM_REQUIRED']
    assert result['valid']


def test_apply_preserves_hybrid_delivery_and_final_validation_accepts_online(data):
    from scheduling.provenance import build_input_snapshot, SNAPSHOT_VERSION
    from scheduling.solver.engine import candidate_dict
    from scheduling.solver.service import apply_generation, fingerprint
    from scheduling.services.validation import validate_version
    config = _hybrid_rule(data, entry_type='LECTURE', block=1, periods=2)
    preserved_section = Section.objects.create(
        program=data['section'].program, semester=data['section'].semester,
        year=1, name='LOCKED-COPY', student_strength=50,
    )
    preserved_offering = CourseOffering.objects.create(
        semester=data['section'].semester, section=preserved_section,
        course=data['offering'].course, weekly_periods=1,
    )
    source_locked = ScheduleEntry.objects.create(
        version=data['version'], section=preserved_section, course_offering=preserved_offering,
        weekday=2, start_slot=data['slots'][4], room=data['room'], locked=True,
    )
    ScheduleEntryFaculty.objects.create(
        schedule_entry=source_locked, faculty=data['faculty'], role='PRIMARY',
    )
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    candidates, diagnostics = build_candidates(data['version'], requirements, config)
    assert not diagnostics
    selected = [
        next(item for item in candidates[requirements[0].requirement_id]
             if item.weekday == 0 and item.delivery_mode == 'OFFLINE'),
        next(item for item in candidates[requirements[1].requirement_id]
             if item.weekday == 1 and item.delivery_mode == 'ONLINE'),
    ]
    payloads = [candidate_dict(item) for item in selected]
    payloads[0]['room_id'] = str(data['room'].pk)
    payloads[1]['room_id'] = None
    source_fp = fingerprint(data['version'])
    snapshot, input_fp = build_input_snapshot(data['version'], config, source_fp)
    run = GenerationRun.objects.create(
        timetable=data['timetable'], source_version=data['version'], created_by=data['admin'],
        mode=config['mode'], status='SUCCEEDED', solver_status='FEASIBLE',
        input_config=config, input_snapshot=snapshot, input_snapshot_version=SNAPSHOT_VERSION,
        input_fingerprint=input_fp, source_fingerprint=source_fp,
        result={'entries': payloads}, statistics={'final_validation_passed': True},
    )

    applied = apply_generation(run)

    online_entry = applied.entries.get(delivery_mode='ONLINE')
    assert online_entry.room_id is None
    assert applied.status == TimetableVersion.Status.DRAFT
    assert source_locked.faculty_assignments.filter(faculty=data['faculty'], role='PRIMARY').exists()
    cloned_locked = applied.entries.get(section=preserved_section, locked=True)
    assert cloned_locked.faculty_assignments.filter(faculty=data['faculty'], role='PRIMARY').exists()
    final_validation = validate_version(applied)
    assert final_validation['valid'], final_validation['conflicts']
    assert not [item for item in final_validation['conflicts']
                if item.get('code', item.get('type')) == 'ROOM_REQUIRED']


def test_room_515_is_preserved_by_import_and_not_replaced_with_516(data):
    from academics.models import SectionDeliveryPolicy
    from faculty.models import CourseOfferingFaculty
    from rooms.models import Room
    from scheduling.services.timetable_import import parse
    _create_period_hybrid_policy(data, 'CSE(IOTBC)-4A', 1)
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'], role='PRIMARY')
    room_515 = Room.objects.create(code='515', building='B1', floor='1', capacity=60)
    Room.objects.create(code='516', building='B1', floor='1', capacity=60)
    room_ugf011 = Room.objects.create(code='UGF011', building='B1', floor='1', capacity=60)
    result, grouped = parse(data['version'], [
        {'Section': 'CSE(IOTBC)-4A', 'Course Code': data['offering'].course.code,
         'Employee Code': data['faculty'].employee_code, 'Day': 'Tuesday',
         'Time Slot': data['slots'][0].label, 'Room No.': '515'},
        {'Section': 'CSE(IOTBC)-4A', 'Course Code': data['offering'].course.code,
         'Employee Code': data['faculty'].employee_code, 'Day': 'Tuesday',
         'Time Slot': data['slots'][1].label, 'Room No.': 'UGF-011'},
    ])
    assert result['valid'] == 2 and result['rows'][0]['room'] == '515'
    assert {identity[4] for identity in grouped} == {room_515.pk, room_ugf011.pk}


def test_period_hybrid_roomless_online_occurrence_does_not_count_as_room_demand(data):
    from academics.models import SectionDeliveryPolicy
    from scheduling.solver.engine import resource_capacity_diagnostics
    _create_period_hybrid_policy(data, 'CS-4F', 0)
    data['offering'].weekly_periods = 1
    data['offering'].save(update_fields=['weekly_periods'])
    config = {'mode': 'FILL_GAPS', 'section_ids': [str(data['section'].pk)], 'offering_rules': [{
        'course_offering_id': str(data['offering'].pk),
        'faculty': [{'faculty_id': str(data['faculty'].pk), 'role': 'PRIMARY'}],
        'session_lengths': [1],
    }]}
    requirements, errors = build_requirements(data['version'], config)
    statistics = {}
    assert not errors
    assert resource_capacity_diagnostics(data['version'], requirements, config, statistics) == []
    assert statistics['physical_required_periods'] == 0
    assert statistics['hybrid_flexible_periods'] == 1


OFFICIAL_WEEKLY_OFF_DAYS = {
    2: {'CS-2B': 3, 'CS-2C': 2, 'CS-2D': 4, 'CS-2E': 0, 'CS-2F': 3,
        'CSAI-2A': 2, 'CSAI-2B': 1, 'CSAI-2C': 4, 'CSAI-2D': 3,
        'CSAI-2E': 2, 'CSAI-2F': 0, 'CSAI-2G': 4, 'CCML-2A': 1, 'AIBC-2A': 3},
    3: {'CS-3A': 0, 'CS-3B': 1, 'CS-3C': 2, 'CS-3D': 4, 'CS-3E': 0,
        'CS-3F': 1, 'CS-3G': 2, 'CS-3H': 3, 'CSE(AI)-3A': 0,
        'CSE(AI)-3B': 1, 'CSE(AI)-3C': 4, 'CSE(AI)-3D': 0,
        'CSE(AI)-3E': 1, 'CSE(AI)-3F': 3, 'CSE(AI)-3G': 0,
        'CSE(AI)-3H': 1, 'CSE(CCML)-3A': 0, 'CSE(IOTBC)-3A': 4},
}


def _configure_weekly_off_fixture(data, name='CS-3A', year=3, weekday=0):
    from academics.models import SectionWeeklyOffPolicy
    section = data['section']
    section.name = name
    section.year = year
    section.program.code = 'BTECH-CSE'
    section.program.save(update_fields=['code'])
    section.semester.session.name = '2026-27'
    section.semester.session.save(update_fields=['name'])
    section.save(update_fields=['name', 'year'])
    return SectionWeeklyOffPolicy.objects.create(
        section=section, academic_session=section.semester.session,
        semester=section.semester, weekday=weekday,
        source='OFFICIAL_TIMETABLE', is_active=True,
    )


def _configure_full_week_fixture(data):
    from academics.models import SectionWeeklyOffPolicy
    section=data['section']
    section.name='CS-2A';section.year=2
    section.program.code='BTECH-CSE';section.program.save(update_fields=['code'])
    section.semester.session.name='2026-27';section.semester.session.save(update_fields=['name'])
    section.save(update_fields=['name','year'])
    return SectionWeeklyOffPolicy.objects.create(section=section,academic_session=section.semester.session,
        semester=section.semester,policy_type='NO_WEEKLY_OFF',weekday=None,
        source='OFFICIAL_TIMETABLE',is_active=True)


def test_official_year2_and_year3_weekly_off_maps_are_exact():
    from importlib import import_module
    migration = import_module('academics.migrations.0011_sectionweeklyoffpolicy')
    assert migration.OFFICIAL_OFF_DAYS == OFFICIAL_WEEKLY_OFF_DAYS


def test_weekly_off_day_removes_solver_candidates_but_keeps_other_days_and_seed_safe(data):
    from academics.weekly_off import is_section_available
    from scheduling.solver.service import _constructive_seed, _group_occurrences
    _configure_weekly_off_fixture(data)
    config = {'mode': 'FILL_GAPS', 'section_ids': [str(data['section'].pk)], 'offering_rules': [{
        'course_offering_id': str(data['offering'].pk),
        'faculty': [{'faculty_id': str(data['faculty'].pk), 'role': 'PRIMARY'}],
        'session_lengths': [1, 1],
    }]}
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    profile = {}
    candidates, diagnostics = build_candidates(data['version'], requirements, config, profile)
    placements = [candidate for options in candidates.values() for candidate in options]
    assert not diagnostics and placements
    assert {candidate.weekday for candidate in placements} == {1, 2, 3, 4}
    assert all(is_section_available(data['section'], day, data['version'].timetable.academic_session, data['version'].timetable.semester) for day in (1, 2, 3, 4))
    assert not is_section_available(data['section'], 0, data['version'].timetable.academic_session, data['version'].timetable.semester)
    assert profile['weekly_off_candidate_placements_eliminated'] > 0
    seed = _constructive_seed(_group_occurrences(requirements, candidates), profile['_room_group_sizes'])
    assert seed['valid']
    assert all(candidate.weekday != 0 for _, candidate in seed['assignments'])


def test_cs2a_no_weekly_off_is_authoritative_and_keeps_all_weekday_candidates(data):
    from academics.weekly_off import is_section_available
    from scheduling.solver.service import scope_errors,preflight_generation
    from scheduling.solver.engine import build_candidates
    policy=_configure_full_week_fixture(data)
    assert policy.policy_type=='NO_WEEKLY_OFF' and policy.weekday is None
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{
        'course_offering_id':str(data['offering'].pk),
        'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1,1]}]}
    errors=scope_errors(data['version'],config)
    assert not any(item['code']=='OFF_DAY_SOURCE_MISSING' for item in errors)
    preflight=preflight_generation(data['version'],config)
    assert not any(item['code']=='OFF_DAY_SOURCE_MISSING' for item in preflight['errors'])
    assert preflight['statistics']['no_weekly_off_policy_count']==1
    assert preflight['statistics']['source_confirmed_section_count']==1
    assert preflight['statistics']['missing_off_day_source_count']==0
    candidates,diagnostics=build_candidates(data['version'],build_requirements(data['version'],config)[0],config)
    weekdays={candidate.weekday for choices in candidates.values() for candidate in choices}
    assert not diagnostics and weekdays==set(range(5))
    assert all(is_section_available(data['section'],day,data['version'].timetable.academic_session,data['version'].timetable.semester) for day in range(5))


def test_weekly_off_still_blocks_cs2b_thursday_and_year3_mapped_day(data):
    from academics.weekly_off import is_section_available
    _configure_weekly_off_fixture(data,name='CS-2B',year=2,weekday=3)
    assert not is_section_available(data['section'],3,data['version'].timetable.academic_session,data['version'].timetable.semester)
    data['section'].name='CS-3A';data['section'].year=3;data['section'].save(update_fields=['name','year'])
    policy=__import__('academics.models',fromlist=['SectionWeeklyOffPolicy']).SectionWeeklyOffPolicy.objects.get(section=data['section'])
    policy.weekday=0;policy.save(update_fields=['weekday'])
    fresh_section=Section.objects.get(pk=data['section'].pk)
    assert not is_section_available(fresh_section,0,data['version'].timetable.academic_session,data['version'].timetable.semester)


@pytest.mark.parametrize('weekday',range(5))
def test_no_weekly_off_manual_and_generated_validation_accept_each_weekday(data,weekday):
    from scheduling.services.validation import validate_entry,validate_generated_entries
    from scheduling.solver.engine import candidate_dict
    from scheduling.weekdays import WEEKDAY_NAMES
    _configure_full_week_fixture(data)
    data['offering'].weekly_periods=1;data['offering'].save(update_fields=['weekly_periods'])
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{
        'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config);assert not diagnostics
    candidate=next(item for item in candidates[requirements[0].requirement_id] if item.weekday==weekday)
    payload=candidate_dict(candidate)
    payload['room_id']=str(data['room'].pk)
    manual=validate_entry(data['version'],{'section':str(data['section'].pk),'course_offering':str(data['offering'].pk),
        'weekday':weekday,'start_slot':candidate.start_slot_id,'block_length':1,'room':str(data['room'].pk),
        'faculty_ids':[str(data['faculty'].pk)]})
    assert not any(error.get('code')=='SECTION_WEEKLY_OFF_DAY_VIOLATION' for error in manual),WEEKDAY_NAMES[weekday]
    assert validate_generated_entries(data['version'],[payload],requirements,'FILL_GAPS',config['section_ids'])['valid']


def test_apply_preflight_accepts_cs2a_full_week_candidate(data):
    from scheduling.provenance import build_input_snapshot,SNAPSHOT_VERSION
    from scheduling.solver.service import fingerprint,validate_generation_apply
    _configure_full_week_fixture(data)
    data['offering'].weekly_periods=1;data['offering'].save(update_fields=['weekly_periods'])
    config={'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{
        'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[1]}]}
    requirements,errors=build_requirements(data['version'],config);assert not errors
    candidates,diagnostics=build_candidates(data['version'],requirements,config);assert not diagnostics
    candidate=next(item for item in candidates[requirements[0].requirement_id] if item.weekday==0)
    source_fp=fingerprint(data['version']);snapshot,input_fp=build_input_snapshot(data['version'],config,source_fp)
    run=GenerationRun.objects.create(timetable=data['timetable'],source_version=data['version'],created_by=data['admin'],
        status='SUCCEEDED',solver_status='FEASIBLE',input_config=config,input_snapshot=snapshot,
        input_snapshot_version=SNAPSHOT_VERSION,input_fingerprint=input_fp,source_fingerprint=source_fp,
        result={'entries':[__import__('scheduling.solver.engine',fromlist=['candidate_dict']).candidate_dict(candidate)|{'room_id':str(data['room'].pk)}]},
        statistics={'final_validation_passed':True})
    validation=validate_generation_apply(run)
    assert validation['valid'],validation['errors']


def test_weekly_off_policy_only_blocks_its_own_section_and_manual_validation(data):
    from dataclasses import replace
    from scheduling.services.validation import validate_generated_entries, validate_entry
    from scheduling.solver.engine import candidate_dict
    _configure_weekly_off_fixture(data)
    second = Section.objects.create(program=data['section'].program, semester=data['section'].semester,
                                    year=3, name='CS-3B', student_strength=60)
    other_offering = CourseOffering.objects.create(semester=data['section'].semester, section=second,
        course=data['offering'].course, weekly_periods=1)
    config = {'mode': 'FILL_GAPS', 'section_ids': [str(data['section'].pk), str(second.pk)], 'offering_rules': [
        {'course_offering_id': str(data['offering'].pk), 'faculty': [{'faculty_id': str(data['faculty'].pk)}], 'session_lengths': [1, 1]},
        {'course_offering_id': str(other_offering.pk), 'faculty': [{'faculty_id': str(data['faculty'].pk)}], 'session_lengths': [1]},
    ]}
    requirements, errors = build_requirements(data['version'], config)
    assert not errors
    candidates, diagnostics = build_candidates(data['version'], requirements, config)
    assert not diagnostics
    assert all(candidate.weekday != 0 for candidate in candidates[requirements[0].requirement_id])
    other_requirement = next(item for item in requirements if item.section_id == str(second.pk))
    assert any(candidate.weekday == 0 for candidate in candidates[other_requirement.requirement_id])
    candidate = candidates[requirements[0].requirement_id][0]
    payload = candidate_dict(replace(candidate, weekday=0))
    payload['room_id'] = str(data['room'].pk)
    result = validate_generated_entries(data['version'], [payload], requirements, 'FILL_GAPS', config['section_ids'])
    assert any(error['code'] == 'SECTION_WEEKLY_OFF_DAY_VIOLATION' for error in result['conflicts'])
    errors = validate_entry(data['version'], {
        'section': str(data['section'].pk), 'course_offering': str(data['offering'].pk),
        'weekday': 0, 'start_slot': str(data['slots'][0].pk), 'block_length': 1,
        'room': str(data['room'].pk), 'faculty_ids': [str(data['faculty'].pk)],
    })
    assert errors[0]['code'] == 'SECTION_WEEKLY_OFF_DAY_VIOLATION'


def test_weekly_off_locked_entry_and_missing_source_are_preflight_errors(data):
    from academics.models import SectionWeeklyOffPolicy
    from scheduling.solver.service import scope_errors
    from scheduling.services.validation import validate_version
    _configure_weekly_off_fixture(data)
    locked = ScheduleEntry.objects.create(version=data['version'], section=data['section'],
        course_offering=data['offering'], weekday=0, start_slot=data['slots'][0],
        room=data['room'], locked=True)
    errors = scope_errors(data['version'], {'section_ids': [str(data['section'].pk)]})
    conflict = next(item for item in errors if item['code'] == 'LOCKED_ENTRY_ON_SECTION_OFF_DAY')
    assert conflict['entry_id'] == str(locked.pk) and conflict['version_id'] == str(data['version'].pk)
    assert any(item['code'] == 'SECTION_WEEKLY_OFF_DAY_VIOLATION' for item in validate_version(data['version'])['conflicts'])
    SectionWeeklyOffPolicy.objects.filter(section=data['section']).delete()
    errors = scope_errors(data['version'], {'section_ids': [str(data['section'].pk)]})
    assert any(item['code'] == 'OFF_DAY_SOURCE_MISSING' for item in errors)

def test_preflight_rejects_out_of_scope_preserved_entries_on_weekly_off_day(data):
    from academics.models import Section
    from scheduling.models import ScheduleEntry
    from scheduling.solver.service import preflight_generation,scope_errors
    from scheduling.services.validation import validate_generated_entries
    _configure_weekly_off_fixture(data,name='CSE(IOTBC)-3A',year=3,weekday=4)
    outside_scope=Section.objects.create(program=data['section'].program,semester=data['section'].semester,
        year=1,name='CS-1B',student_strength=60)
    config={'mode':'REBUILD_UNLOCKED','section_ids':[str(outside_scope.pk)]}
    assert validate_generated_entries(data['version'],[],[],'REBUILD_UNLOCKED',config['section_ids'])['valid']
    preserved=[]
    for slot in data['slots'][:2]:
        preserved.append(ScheduleEntry.objects.create(version=data['version'],section=data['section'],
            course_offering=data['offering'],weekday=4,start_slot=slot,room=data['room'],locked=False))
    expected_ids={str(entry.pk) for entry in preserved}
    errors=scope_errors(data['version'],config)
    blockers=[item for item in errors if item['code']=='SECTION_WEEKLY_OFF_DAY_VIOLATION']
    assert {item['entry_id'] for item in blockers}==expected_ids
    preflight=preflight_generation(data['version'],config)
    preflight_blockers=[item for item in preflight['errors'] if item['code']=='SECTION_WEEKLY_OFF_DAY_VIOLATION']
    assert {item['entry_id'] for item in preflight_blockers}==expected_ids
    validation=validate_generated_entries(data['version'],[],[],'REBUILD_UNLOCKED',config['section_ids'])
    validation_blockers=[item for item in validation['conflicts'] if item['code']=='SECTION_WEEKLY_OFF_DAY_VIOLATION']
    assert {item['entry_id'] for item in validation_blockers}==expected_ids


def test_weekly_off_reduces_section_capacity_and_preflight_reports_overload(data):
    from scheduling.solver.service import scope_errors
    _configure_weekly_off_fixture(data)
    data['offering'].weekly_periods = 30
    data['offering'].save(update_fields=['weekly_periods'])
    errors = scope_errors(data['version'], {'section_ids': [str(data['section'].pk)]})
    capacity = next(item for item in errors if item['code'] == 'SECTION_WEEKLY_CAPACITY_EXCEEDED')
    assert capacity['required_periods'] == 30
    assert capacity['available_periods'] == 20


def test_apply_rejects_weekly_off_day_even_if_generation_result_is_tampered(api_client, data):
    _configure_weekly_off_fixture(data)
    run = run_gen(api_client, data, {'mode': 'FILL_GAPS', 'section_ids': [str(data['section'].pk)],
        'offering_rules': [{'course_offering_id': str(data['offering'].pk),
                            'faculty': [{'faculty_id': str(data['faculty'].pk)}],
                            'session_lengths': [1, 1]}], 'max_solve_seconds': 30})
    generation = GenerationRun.objects.get(pk=run['id'])
    payload = generation.result
    payload['entries'][0]['weekday'] = 0
    generation.result = payload
    generation.save(update_fields=['result'])
    api_client.force_authenticate(data['admin'])
    response = api_client.post(f'/api/generation-runs/{generation.pk}/apply/')
    assert response.status_code == 409
    assert response.data['code'] == 'GENERATED_TIMETABLE_VALIDATION_FAILED'
    assert any(error['code'] == 'SECTION_WEEKLY_OFF_DAY_VIOLATION' for error in response.data['conflicts'])

