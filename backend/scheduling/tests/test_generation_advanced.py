import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from scheduling.models import GenerationRun, ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
from academics.models import Section, CourseOffering, Course
from faculty.models import Faculty
from common.models import TimeSlot
from rooms.models import Room, RoomAvailability
from accounts.models import User, Role

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

def test_section_break_test(api_client, data):
    data['slots'][1].is_break = True; data['slots'][1].save()
    ScheduleEntry.objects.create(version=data['version'], section=data['section'], course_offering=data['offering'], weekday=0, start_slot=data['slots'][0], room=data['room'], block_length=1, locked=True)
    data['offering'].weekly_periods = 2; data['offering'].save()
    payload = {'mode': 'FILL_GAPS', 'soft_constraints': {'minimize_section_gaps': 100, 'spread_course_days':0, 'balance_section_load':0}, 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [1]}], 'random_seed': 42}
    res = run_gen(api_client, data, payload)
    assert res['status'] == 'SUCCEEDED'
    assert res['result']['entries'][0]['start_slot_id'] == str(data['slots'][2].id)

def test_section_multi_period_test(api_client, data):
    data['offering'].weekly_periods = 2; data['offering'].save()
    payload = {'mode': 'FILL_GAPS', 'soft_constraints': {'minimize_section_gaps': 10, 'spread_course_days':0}, 'offering_rules': [{'course_offering_id': str(data['offering'].id), 'faculty': [{'faculty_id': str(data['faculty'].id)}], 'session_lengths': [2]}], 'random_seed': 42}
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
