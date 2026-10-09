from datetime import date
from types import SimpleNamespace

from django.test import TestCase

from academics.models import AcademicSession, Section, Semester
from institutions.models import Department, Institution, Program
from reports.services.reporting import students_by_year


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
