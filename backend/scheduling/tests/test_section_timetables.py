import pytest
from rest_framework.test import APIClient

from accounts.models import Role, User
from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from common.models import TimeSlot, TimeSlotTemplate
from faculty.models import CourseOfferingFaculty, Faculty
from institutions.models import Department, Institution, Program
from rooms.models import Room
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, Timetable, TimetableVersion


@pytest.fixture
def section_scope_data(db):
    institution = Institution.objects.create(name='Babu Banarasi Das University', code='BBDU')
    department = Department.objects.create(institution=institution, name='Computer Science & Engineering', code='CSE')
    other_department = Department.objects.create(institution=institution, name='Electrical Engineering', code='EE')
    program = Program.objects.create(department=department, name='B.Tech CSE', code='BTECH-CSE', duration_years=4)
    other_program = Program.objects.create(department=other_department, name='B.Tech EE', code='BTECH-EE', duration_years=4)
    session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date='2026-04-01', end_date='2027-03-31')
    semester = Semester.objects.create(session=session, name='Odd Semester', number=1, type='ODD', start_date='2026-07-01', end_date='2026-12-31')
    section = Section.objects.create(program=program, semester=semester, year=1, name='CS-1A')
    other_section = Section.objects.create(program=other_program, semester=semester, year=1, name='EE-1A')
    course = Course.objects.create(code='CS101', name='Programming', credit=3, short_code='P')
    offering = CourseOffering.objects.create(semester=semester, section=section, course=course, weekly_periods=1)
    admin_user = User.objects.create_user('scheduler@test.local', 'pass', role=Role.SUPER_ADMIN)
    timetable = Timetable.objects.create(institution=institution, academic_session=session, semester=semester, department=department, title='CSE Odd 2026-27', created_by=admin_user)
    published = TimetableVersion.objects.create(timetable=timetable, version_no=1, status='PUBLISHED', created_by=admin_user)
    draft = TimetableVersion.objects.create(timetable=timetable, version_no=2, status='DRAFT', created_by=admin_user)
    template = TimeSlotTemplate.objects.create(name='Standard Day', institution=institution)
    slot = TimeSlot.objects.create(template=template, label='09:00-10:00', start_time='09:00', end_time='10:00', order=1)
    room = Room.objects.create(code='516', building='Engineering', floor='5', capacity=60)
    faculty_user = User.objects.create_user('faculty@test.local', 'pass', role=Role.FACULTY, first_name='Harsh', last_name='Dev')
    faculty = Faculty.objects.create(user=faculty_user, name='Dr. Harsh Dev', employee_code='F-1', initials='HD', department=department)
    CourseOfferingFaculty.objects.create(course_offering=offering, faculty=faculty)
    entry = ScheduleEntry.objects.create(version=published, section=section, course_offering=offering, weekday=0, start_slot=slot, room=room, locked=True)
    ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=faculty)
    return locals()


def manager(email, scope, departments):
    user = User.objects.create_user(email, 'pass', role=Role.HOD_OR_DEAN_APPROVER, management_scope=scope)
    user.managed_departments.set(departments)
    return user


def test_super_admin_lists_and_opens_section_timetable(section_scope_data):
    data = section_scope_data
    client = APIClient()
    client.force_authenticate(User.objects.create_user('root@test.local', 'pass', role=Role.SUPER_ADMIN))
    response = client.get('/api/section-timetables/')
    assert response.status_code == 200
    assert {row['id'] for row in response.data} == {str(data['section'].pk), str(data['other_section'].pk)}
    detail = client.get(f"/api/sections/{data['section'].pk}/timetable/")
    assert detail.status_code == 200
    assert detail.data['version']['status'] == 'PUBLISHED'
    assert detail.data['entries'][0]['locked'] is True
    assert detail.data['entries'][0]['room_code'] == '516'
    assert detail.data['entries'][0]['faculty_assignments'][0]['name'] == 'Harsh Dev'
    assert float(detail.data['entries'][0]['course']['credit']) == 3.0
    assert detail.data['entries'][0]['course']['short_code'] == 'P'
    master_sections = client.get('/api/sections/')
    assert master_sections.status_code == 200
    assert 'coordinator_mobile' not in master_sections.data['results'][0]


def test_hod_scope_is_limited_to_assigned_department_and_direct_access_is_forbidden(section_scope_data):
    data = section_scope_data
    hod = manager('hod@test.local', User.ManagementScope.HOD, [data['department']])
    client = APIClient()
    client.force_authenticate(hod)
    listed = client.get('/api/section-timetables/')
    assert listed.status_code == 200
    assert [row['id'] for row in listed.data] == [str(data['section'].pk)]
    denied = client.get(f"/api/sections/{data['other_section'].pk}/timetable/")
    assert denied.status_code == 403


def test_dean_scope_includes_only_assigned_academic_units(section_scope_data):
    data = section_scope_data
    dean = manager('dean@test.local', User.ManagementScope.DEAN, [data['other_department']])
    client = APIClient()
    client.force_authenticate(dean)
    response = client.get('/api/section-timetables/')
    assert response.status_code == 200
    assert [row['id'] for row in response.data] == [str(data['other_section'].pk)]


def test_coordinator_update_and_pdf_endpoint_enforce_scope(section_scope_data):
    data = section_scope_data
    client = APIClient()
    client.force_authenticate(manager('dept-hod@test.local', User.ManagementScope.HOD, [data['department']]))
    updated = client.patch(f"/api/sections/{data['section'].pk}/coordinator/", {'name': 'Dr. Coordinator', 'mobile': '+91 9876543210'}, format='json')
    assert updated.status_code == 200
    data['section'].refresh_from_db()
    assert data['section'].coordinator_name == 'Dr. Coordinator'
    assert data['section'].coordinator_mobile == '+91 9876543210'
    pdf = client.get(f"/api/sections/{data['section'].pk}/timetable/pdf/")
    assert pdf.status_code == 200
    assert pdf['Content-Type'] == 'application/pdf'
    assert pdf.content.startswith(b'%PDF-1.4')
    assert pdf['Content-Disposition'].endswith('BBDU_CS-1A_Odd-Semester_2026-27_Timetable.pdf"')
    assert b'Class Coordinator: Dr. Coordinator' in pdf.content
    assert b'Mobile No.: +91 9876543210' in pdf.content
    assert b'L/P/HD/516' in pdf.content, pdf.content.decode('cp1252', errors='replace')
    assert b'OFFICIAL FIXED' not in pdf.content
    selected_draft = client.get(f"/api/sections/{data['section'].pk}/timetable/?version={data['draft'].pk}")
    assert selected_draft.status_code == 200
    assert selected_draft.data['version']['id'] == str(data['draft'].pk)
    assert selected_draft.data['entries'] == []
    forbidden = client.patch(f"/api/sections/{data['other_section'].pk}/coordinator/", {'name': 'Intruder', 'mobile': '9876543210'}, format='json')
    assert forbidden.status_code == 403
    forbidden_pdf = client.get(f"/api/sections/{data['other_section'].pk}/timetable/pdf/")
    assert forbidden_pdf.status_code == 403


@pytest.mark.parametrize('year,semester_type,expected', [
    (1, 'ODD', 'I'), (2, 'ODD', 'III'), (3, 'ODD', 'V'), (4, 'ODD', 'VII'),
    (1, 'EVEN', 'II'), (2, 'EVEN', 'IV'), (3, 'EVEN', 'VI'), (4, 'EVEN', 'VIII'),
])
def test_official_pdf_vertical_semester_uses_btech_year(section_scope_data, year, semester_type, expected):
    from scheduling.section_timetables import _detail_data, _draw_official_pdf

    payload = _detail_data(section_scope_data['section'])
    payload['section']['year'] = year
    payload['section']['name'] = 'CSE(AI)-4F' if year == 4 else f'CS-{year}A'
    payload['section']['semester']['type'] = semester_type
    payload['section']['semester']['name'] = 'Odd Semester' if semester_type == 'ODD' else 'Even Semester'
    # This is the shared academic-period record, not a per-year ordinal.
    payload['section']['semester']['number'] = 1

    pdf = _draw_official_pdf(payload)

    escaped_name = payload['section']['name'].replace('(', r'\(').replace(')', r'\)')
    assert f'B.Tech CSE - {expected} Sem - Section: {escaped_name}'.encode() in pdf
    if year == 4 and semester_type == 'ODD':
        assert b'B.Tech CSE Fourth Year, Odd Semester' in pdf
        assert b'B.Tech CSE - I Sem - Section: CSE\\(AI\\)-4F' not in pdf


@pytest.mark.parametrize('year,semester_number,expected', [(1, 1, 'I'), (2, 3, 'III')])
def test_mtech_pdf_uses_selected_sections_program_and_model_semester(section_scope_data, year, semester_number, expected):
    from scheduling.section_timetables import _detail_data, _draw_official_pdf

    data = section_scope_data
    program = Program.objects.create(department=data['department'], name='M.Tech CSE (AI)', code='MTECH-CSEAI', duration_years=2)
    semester = Semester.objects.create(
        session=data['session'], name='Odd Semester', number=semester_number, type='ODD',
        start_date='2026-07-01', end_date='2026-12-31',
    )
    section = Section.objects.create(program=program, semester=semester, year=year, name=f'MTCSAI-{year}')

    pdf = _draw_official_pdf(_detail_data(section))

    year_name = 'First' if year == 1 else 'Second'
    assert f'M.Tech {year_name} Year, Odd Semester'.encode() in pdf
    assert f'M.Tech CSE \\(AI\\) - {expected} Sem - Section: MTCSAI-{year}'.encode() in pdf
    assert b'Academic Session: 2026-27' in pdf
    assert b'B.Tech' not in pdf


def test_manager_without_explicit_department_scope_fails_closed(section_scope_data):
    data = section_scope_data
    unassigned = User.objects.create_user('unassigned@test.local', 'pass', role=Role.HOD_OR_DEAN_APPROVER)
    client = APIClient()
    client.force_authenticate(unassigned)
    assert client.get('/api/section-timetables/').status_code == 403
    assert client.get(f"/api/sections/{data['section'].pk}/timetable/").status_code == 403


def test_official_compact_metadata_includes_room_only_for_physical_entries(section_scope_data):
    from scheduling.section_timetables import _compact_metadata, _detail_data

    data = section_scope_data
    entry = _detail_data(data['section'])['entries'][0]
    assert _compact_metadata(entry, include_room=True) == 'L/P/HD/516'
    entry['delivery_mode'] = 'ONLINE'
    assert _compact_metadata(entry, include_room=True) == 'L/P/HD'
    assert _compact_metadata(entry, include_room=False) == 'L/P/HD'


def test_official_metadata_does_not_invent_library_row(section_scope_data):
    from scheduling.section_timetables import _detail_data

    entries = _detail_data(section_scope_data['section'])['entries']
    assert all(entry['entry_type'] != 'LIBRARY' for entry in entries)
