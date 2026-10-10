from datetime import date
from types import SimpleNamespace

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role, User
from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from common.models import TimeSlot, TimeSlotTemplate
from faculty.models import Faculty
from institutions.models import Department, Institution, Program
from reports.services.reporting import faculty_workload, published_faculty_count, scheduled_entry_analytics, students_by_year
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, Timetable, TimetableVersion


class StudentsByYearReportTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(name='Test Institute', code='TEST')
        department = Department.objects.create(institution=institution, name='Engineering', code='ENG')
        self.program = Program.objects.create(department=department, name='Computer Science', code='CSE', duration_years=4)
        session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
        self.semester = Semester.objects.create(session=session, name='Odd', number=1, type='ODD', start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))

    def test_sums_actual_strength_by_year(self):
        for index, strength in enumerate((60, 61, 46, 16)):
            Section.objects.create(program=self.program, semester=self.semester, year=1, name=f'Y1-{index}', student_strength=strength)
        Section.objects.create(program=self.program, semester=self.semester, year=2, name='Y2-A', student_strength=27)
        Section.objects.create(program=self.program, semester=self.semester, year=2, name='Y2-B', student_strength=73)
        rows = list(students_by_year(SimpleNamespace(query_params={})))
        self.assertEqual([(row['year'], row['section_count'], row['student_count']) for row in rows], [(1, 4, 183), (2, 2, 100)])

    def test_session_filter_excludes_other_session(self):
        Section.objects.create(program=self.program, semester=self.semester, year=1, name='Current', student_strength=41)
        other_session = AcademicSession.objects.create(institution=self.semester.session.institution, name='2025-26', start_date=date(2025, 7, 1), end_date=date(2026, 6, 30))
        other_semester = Semester.objects.create(session=other_session, name='Odd', number=1, type='ODD', start_date=date(2025, 7, 1), end_date=date(2025, 12, 31))
        Section.objects.create(program=self.program, semester=other_semester, year=1, name='Historical', student_strength=999)
        rows = list(students_by_year(SimpleNamespace(query_params={'session': str(self.semester.session_id)})))
        self.assertEqual(rows[0]['student_count'], 41)


class FacultyWorkloadOptionsTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(name='Test Institute', code='TEST')
        self.department = Department.objects.create(institution=institution, name='Engineering', code='ENG')
        self.user = User.objects.create_user('admin@example.com', password='password', role=Role.SUPER_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.faculties = [
            Faculty.objects.create(name=f'Faculty {index:02}', employee_code=f'F{index:02}', department=self.department)
            for index in range(25)
        ]
        self.inactive = Faculty.objects.create(name='Inactive Faculty', employee_code='INACTIVE', department=self.department, active=False)

    def test_options_returns_all_active_faculty_sorted_and_unpaginated(self):
        response = self.client.get('/api/reports/faculty-workload/options/')
        self.assertEqual(response.status_code, 200)
        ids = [row['id'] for row in response.data]
        names = [row['name'] for row in response.data]
        self.assertEqual(len(response.data), 25)
        self.assertIn(str(self.faculties[20].pk), ids)
        self.assertEqual(names, sorted(names, key=str.casefold))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn(str(self.inactive.pk), ids)
        self.assertIsInstance(response.data, list)

    def test_report_filter_accepts_faculty_id_from_options(self):
        faculty = self.faculties[20]
        request = SimpleNamespace(user=self.user, query_params={'faculty': str(faculty.pk)})
        rows = faculty_workload(request)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['faculty_id'], str(faculty.pk))
        self.assertEqual(rows[0]['total_scheduled_periods'], 0)


class ReportsOverviewFacultyCountTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(name='Overview Institute', code='OVERVIEW')
        department = Department.objects.create(institution=institution, name='Engineering', code='OVENG')
        program = Program.objects.create(department=department, name='Computing', code='OVCSE', duration_years=4)
        session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
        semester = Semester.objects.create(session=session, name='Odd', number=1, type='ODD', start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))
        section = Section.objects.create(program=program, semester=semester, year=1, name='A')
        course = Course.objects.create(code='OV101', name='Overview Course', short_code='OV', credit=3)
        offering = CourseOffering.objects.create(semester=semester, section=section, course=course, weekly_periods=10)
        self.admin = User.objects.create_user('overview-admin@test.local', 'pass', role=Role.SUPER_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        template = TimeSlotTemplate.objects.create(institution=institution, name='Overview slots')
        slot = TimeSlot.objects.create(template=template, label='09 to 10', start_time='09:00', end_time='10:00', order=1)
        timetable = Timetable.objects.create(institution=institution, academic_session=session, semester=semester, department=department, title='Overview', created_by=self.admin)
        self.current = TimetableVersion.objects.create(timetable=timetable, version_no=2, status='PUBLISHED', created_by=self.admin)
        self.old_published = TimetableVersion.objects.create(timetable=timetable, version_no=1, status='PUBLISHED', created_by=self.admin)
        self.draft = TimetableVersion.objects.create(timetable=timetable, version_no=3, status='DRAFT', created_by=self.admin)
        faculty = [Faculty.objects.create(name=f'Published Faculty {i:02}', employee_code=f'OVF{i:02}', department=department) for i in range(20)]
        zero_workload = Faculty.objects.create(name='No Published Workload', employee_code='OVZERO', department=department)

        for index, member in enumerate(faculty):
            entry = ScheduleEntry.objects.create(version=self.current, section=section, course_offering=offering, weekday=index % 5, start_slot=slot, block_length=1)
            ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=member)
            if index == 0:
                repeated = ScheduleEntry.objects.create(version=self.current, section=section, course_offering=offering, weekday=0, start_slot=slot, block_length=1)
                ScheduleEntryFaculty.objects.create(schedule_entry=repeated, faculty=member)
                ScheduleEntryFaculty.objects.create(schedule_entry=repeated, faculty=faculty[1], role='CO_FACULTY')
        for version in (self.old_published, self.draft):
            historical = ScheduleEntry.objects.create(version=version, section=section, course_offering=offering, weekday=0, start_slot=slot, block_length=1)
            ScheduleEntryFaculty.objects.create(schedule_entry=historical, faculty=zero_workload)

    def test_overview_counts_distinct_current_published_faculty_and_matches_workload(self):
        request = SimpleNamespace(user=self.admin, query_params={})
        entries, _, _ = scheduled_entry_analytics(request)
        self.assertEqual(published_faculty_count(entries), 20)

        overview = self.client.get('/api/reports/analytics/')
        workload = self.client.get('/api/reports/faculty-workload/')
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(workload.status_code, 200)
        workload_count = sum(row['total_scheduled_periods'] > 0 for row in workload.data)
        self.assertEqual(overview.data['total_faculty'], 20)
        self.assertEqual(workload_count, 20)
        self.assertEqual(overview.data['total_faculty'], workload_count)
        self.assertEqual(overview.data['scheduled_classes'], 21)

    def test_count_honors_the_same_section_filter_as_scheduled_entries(self):
        request = SimpleNamespace(user=self.admin, query_params={'section': '00000000-0000-0000-0000-000000000001'})
        entries, _, _ = scheduled_entry_analytics(request)
        self.assertEqual(published_faculty_count(entries), 0)
