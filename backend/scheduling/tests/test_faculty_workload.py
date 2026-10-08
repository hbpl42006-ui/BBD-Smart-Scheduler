from datetime import date, time

import pytest
from rest_framework.test import APIClient

from accounts.models import Role, User
from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from common.models import TimeSlot, TimeSlotTemplate
from faculty.models import Faculty
from institutions.models import Department, Institution, Program
from rooms.models import Room
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, Timetable, TimetableVersion


@pytest.fixture
def workload_data(db):
    institution = Institution.objects.create(name='Workload University', code='WU')
    department = Department.objects.create(institution=institution, name='Computing', code='WC')
    other_department = Department.objects.create(institution=institution, name='Physics', code='WP')
    program = Program.objects.create(department=department, name='B.Tech Computing', code='WBT', duration_years=4)
    other_program = Program.objects.create(department=other_department, name='B.Sc Physics', code='WBS', duration_years=3)
    session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
    semester = Semester.objects.create(session=session, name='Odd Semester', number=1, type='ODD', start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))
    section_a = Section.objects.create(program=program, semester=semester, year=1, name='A')
    section_b = Section.objects.create(program=program, semester=semester, year=1, name='B')
    other_section = Section.objects.create(program=other_program, semester=semester, year=1, name='A')
    course = Course.objects.create(code='WL101', name='Workload Systems', short_code='WS', credit=3)
    offering_a = CourseOffering.objects.create(semester=semester, section=section_a, course=course, weekly_periods=2, default_class_type='PRACTICAL', required_block_size=2)
    offering_b = CourseOffering.objects.create(semester=semester, section=section_b, course=course, weekly_periods=1, default_class_type='LECTURE', required_block_size=1)
    other_offering = CourseOffering.objects.create(semester=semester, section=other_section, course=course, weekly_periods=1, default_class_type='LECTURE', required_block_size=1)
    admin = User.objects.create_user('workload-admin@test.local', 'pass', role=Role.SUPER_ADMIN)
    user_a = User.objects.create_user('workload-a@test.local', 'pass', role=Role.FACULTY, first_name='Asha', last_name='Faculty')
    user_b = User.objects.create_user('workload-b@test.local', 'pass', role=Role.FACULTY, first_name='Bharat', last_name='Faculty')
    faculty_a = Faculty.objects.create(user=user_a, name='Asha Faculty', employee_code='WL-A', department=department)
    faculty_b = Faculty.objects.create(user=user_b, name='Bharat Faculty', employee_code='WL-B', department=department)
    other_user = User.objects.create_user('workload-other@test.local', 'pass', role=Role.FACULTY)
    other_faculty = Faculty.objects.create(user=other_user, name='Other Faculty', employee_code='WL-O', department=other_department)
    template = TimeSlotTemplate.objects.create(institution=institution, name='Workload slots')
    slots = [TimeSlot.objects.create(template=template, label=label, start_time=time(hour), end_time=time(hour + 1), order=index, is_break=is_break) for index, (label, hour, is_break) in enumerate([('09 to 10', 9, False), ('10 to 11', 10, False), ('1 to 2', 13, True)], start=1)]
    room = Room.objects.create(code='WL-101', building='Main', floor='1', capacity=40, room_type='CLASSROOM')
    timetable = Timetable.objects.create(institution=institution, academic_session=session, semester=semester, department=department, title='Workload test', created_by=admin)
    version = TimetableVersion.objects.create(timetable=timetable, version_no=1, status='PUBLISHED', created_by=admin)
    draft_version = TimetableVersion.objects.create(timetable=timetable, version_no=2, status='DRAFT', created_by=admin)
    practical = ScheduleEntry.objects.create(version=version, section=section_a, course_offering=offering_a, weekday=0, start_slot=slots[0], block_length=2, room=None, entry_type='PRACTICAL', delivery_mode='ONLINE')
    shared = ScheduleEntry.objects.create(version=version, section=section_b, course_offering=offering_b, weekday=0, start_slot=slots[0], block_length=1, room=room, entry_type='LECTURE', delivery_mode='OFFLINE')
    ScheduleEntry.objects.create(version=version, section=other_section, course_offering=other_offering, weekday=1, start_slot=slots[1], room=room, entry_type='LECTURE', delivery_mode='OFFLINE')
    ScheduleEntryFaculty.objects.create(schedule_entry=practical, faculty=faculty_a, role='PRIMARY')
    ScheduleEntryFaculty.objects.create(schedule_entry=shared, faculty=faculty_a, role='PRIMARY')
    ScheduleEntryFaculty.objects.create(schedule_entry=shared, faculty=faculty_b, role='CO_FACULTY')
    draft_entry = ScheduleEntry.objects.create(version=draft_version, section=section_a, course_offering=offering_a, weekday=4, start_slot=slots[1], block_length=1, room=room, entry_type='PRACTICAL', delivery_mode='OFFLINE')
    ScheduleEntryFaculty.objects.create(schedule_entry=draft_entry, faculty=faculty_a, role='PRIMARY')
    return locals()


@pytest.mark.django_db
def test_my_timetable_endpoint_remains_separate_and_published(workload_data):
    client = APIClient()
    client.force_authenticate(workload_data['user_a'])
    response = client.get('/api/me/faculty-timetable/')
    assert response.status_code == 200
    assert response.data['version']['status'] == 'PUBLISHED'
    assert {row['name'] for row in response.data['sections']} == {'A', 'B'}


@pytest.mark.django_db
def test_faculty_self_workload_combines_sections_multi_faculty_and_counts_blocks(workload_data):
    client = APIClient()
    client.force_authenticate(workload_data['user_a'])
    response = client.get('/api/faculty-workload/me/')
    assert response.status_code == 200
    assert {entry['section_name'] for entry in response.data['entries']} == {'A', 'B'}
    assert response.data['summary']['total_periods'] == 3
    assert response.data['summary']['practical_periods'] == 2
    assert response.data['summary']['online_periods'] == 2
    assert response.data['summary']['offline_periods'] == 1
    practical_entry = next(entry for entry in response.data['entries'] if entry['entry_type'] == 'PRACTICAL')
    assert practical_entry['block_length'] == 2
    assert practical_entry['delivery_mode'] == 'ONLINE' and practical_entry['room'] is None
    lecture_entry = next(entry for entry in response.data['entries'] if entry['entry_type'] == 'LECTURE')
    assert lecture_entry['block_length'] == 1
    assert len([entry for entry in response.data['entries'] if entry['section_name'] == 'B']) == 1
    offline = next(entry for entry in response.data['entries'] if entry['section_name'] == 'B')
    assert offline['delivery_mode'] == 'OFFLINE'
    assert offline['room_code'] == 'WL-101'
    client.force_authenticate(workload_data['user_b'])
    shared_workload = client.get('/api/faculty-workload/me/')
    assert len(shared_workload.data['entries']) == 1
    assert shared_workload.data['entries'][0]['section_name'] == 'B'


@pytest.mark.django_db
def test_faculty_cannot_view_another_workload_and_unlinked_user_gets_code(workload_data):
    client = APIClient()
    client.force_authenticate(workload_data['user_a'])
    assert client.get(f"/api/faculty-workload/{workload_data['faculty_b'].pk}/").status_code == 403
    assert client.get('/api/faculty-workload/').status_code == 403
    self_response = client.get(f"/api/faculty-workload/me/?faculty={workload_data['faculty_b'].pk}")
    assert self_response.status_code == 200
    assert self_response.data['faculty']['id'] == str(workload_data['faculty_a'].pk)
    unlinked = User.objects.create_user('unlinked-workload@test.local', 'pass', role=Role.FACULTY)
    client.force_authenticate(unlinked)
    response = client.get('/api/faculty-workload/me/')
    assert response.status_code == 404
    assert response.data['code'] == 'FACULTY_PROFILE_NOT_LINKED'


@pytest.mark.django_db
def test_super_admin_and_hod_department_scope(workload_data):
    client = APIClient()
    client.force_authenticate(workload_data['admin'])
    assert client.get(f"/api/faculty-workload/{workload_data['other_faculty'].pk}/").status_code == 200
    hod = User.objects.create_user('workload-hod@test.local', 'pass', role=Role.HOD_OR_DEAN_APPROVER)
    Faculty.objects.create(user=hod, employee_code='WL-HOD', department=workload_data['department'])
    client.force_authenticate(hod)
    listing = client.get('/api/faculty-workload/')
    assert listing.status_code == 200
    assert {row['id'] for row in listing.data} == {str(workload_data['faculty_a'].pk), str(workload_data['faculty_b'].pk), str(Faculty.objects.get(user=hod).pk)}
    assert client.get(f"/api/faculty-workload/{workload_data['other_faculty'].pk}/").status_code == 404


@pytest.mark.django_db
def test_workload_returns_multiple_same_slot_entries_without_overwriting(workload_data):
    client = APIClient()
    client.force_authenticate(workload_data['user_a'])
    response = client.get('/api/faculty-workload/me/')
    same_slot = [entry for entry in response.data['entries'] if entry['weekday'] == 0 and str(entry['start_slot']) == str(workload_data['slots'][0].pk)]
    assert response.status_code == 200
    assert len(same_slot) == 2
