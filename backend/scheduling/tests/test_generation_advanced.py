import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from scheduling.models import GenerationRun, ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
from academics.models import Section, CourseOffering, Course
from faculty.models import Faculty
from common.models import TimeSlot
from rooms.models import Room, RoomAvailability
from accounts.models import User, Role
from scheduling.solver.engine import Candidate, FacultyAssignment, build_candidates, build_requirements
from scheduling.solver.objective import build_objective_context, candidate_score

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

def test_unknown_solver_result_is_reported_as_time_limit_not_infeasible(api_client, data, monkeypatch):
    from ortools.sat.python import cp_model
    monkeypatch.setattr(cp_model.CpSolver, 'Solve', lambda _solver, _model: cp_model.UNKNOWN)
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
    assert run_data['solver_status'] == 'UNKNOWN'
    assert run_data['status'] == 'FAILED'
    assert run_data['result']['entries'] == []
    assert run_data['diagnostics']['errors'][0]['code'] == 'TIME_LIMIT'

def test_preflight_reports_hard_room_period_capacity_shortfall(data):
    from scheduling.solver.service import preflight_generation
    data['offering'].weekly_periods = 31
    data['offering'].save()
    result = preflight_generation(data['version'], {
        'mode': 'FILL_GAPS',
        'section_ids': [str(data['section'].id)],
        'offering_rules': [{
            'course_offering_id': str(data['offering'].id),
            'faculty': [{'faculty_id': str(data['faculty'].id), 'role': 'PRIMARY'}],
            'session_lengths': [1] * 31,
        }],
    })
    capacity_error = next(error for error in result['errors'] if error['code'] == 'INSUFFICIENT_ROOM_CAPACITY')
    assert result['valid'] is False
    assert capacity_error['required_periods'] == 31
    assert capacity_error['available_periods'] == 30
    assert capacity_error['shortfall_periods'] == 1

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
    assert shortage['required_periods']==31 and shortage['available_periods']==30
    candidates,diagnostics=build_candidates(data['version'],requirements[:1],config)
    assert not diagnostics and all(item.room_id.startswith('ROOM_GROUP:') and item.delivery_mode=='OFFLINE' for items in candidates.values() for item in items)
    assert lab.active

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

