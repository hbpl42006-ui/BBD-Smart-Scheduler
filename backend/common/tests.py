from datetime import date

from django.test import TestCase

from academics.models import AcademicSession, Section, Semester
from common.import_services import section_import
from institutions.models import Department, Institution, Program


class SectionImportTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(name='Import Institute', code='IMP')
        department = Department.objects.create(institution=institution, name='Engineering', code='IMP-ENG')
        self.program = Program.objects.create(department=department, name='Computer Science', code='IMP-CSE', duration_years=4)
        self.session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
        self.semester = Semester.objects.create(session=self.session, name='Odd', number=1, type='ODD', start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))

    def test_total_students_is_imported_and_existing_pk_is_updated(self):
        existing = Section.objects.create(program=self.program, semester=self.semester, year=1, name='CS-1A', student_strength=60)
        result = section_import([{'Program': 'IMP-CSE', 'Semester': 'Odd', 'Year': 1, 'Section': 'CS 1A', 'Total Students': 61}, {'Program': 'IMP-CSE', 'Semester': 'Odd', 'Year': 1, 'Section': 'CS-1B', 'Total Students': 46}, {'Program': 'IMP-CSE', 'Semester': 'Odd', 'Year': 1, 'Section': 'CS-1C', 'Total Students': 16}])
        existing.refresh_from_db()
        self.assertEqual(existing.student_strength, 61)
        self.assertEqual(Section.objects.filter(program=self.program, semester=self.semester).count(), 3)
        self.assertEqual(result['updated'], 1)
        self.assertEqual(result['created'], 2)

    def test_referenced_section_is_not_deleted_by_import(self):
        existing = Section.objects.create(program=self.program, semester=self.semester, year=1, name='CS-1A', student_strength=60)
        section_import([{'Program': 'IMP-CSE', 'Semester': 'Odd', 'Year': 1, 'Section': 'CS-1A', 'Total Students': 46}])
        self.assertTrue(Section.objects.filter(pk=existing.pk).exists())
