from datetime import date, time
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from accounts.models import User
from academics.models import AcademicSession, Course, CourseOffering, Section, SectionDeliveryPolicy, SectionWeeklyOffPolicy, Semester
from common.management.commands.sync_sections_from_excel import alias_identity, duplicate_target_identity
from common.models import TimeSlot, TimeSlotTemplate
from institutions.models import Department, Institution, Program
from scheduling.models import ScheduleEntry, Timetable, TimetableVersion
from reports.services.reporting import analytics


class SectionSyncAuditTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(name='Sync Institute', code='SYNC')
        department = Department.objects.create(institution=institution, name='Engineering', code='SYNC-ENG')
        self.program = Program.objects.create(department=department, name='Computer Science', code='SYNC-CSE', duration_years=4)
        session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
        self.session = session
        self.semester = Semester.objects.create(session=session, name='Odd', number=1, type='ODD', start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))

    def workbook(self, directory, rows):
        path = Path(directory) / 'sections.xlsx'
        book = Workbook()
        sheet = book.active
        sheet.append(['Program', 'Semester', 'Year', 'Section', 'Total Students'])
        for row in rows:
            sheet.append([self.program.code, self.semester.name, *row])
        book.save(path)
        return path

    def test_explicit_aliases_and_duplicate_identity_are_narrow(self):
        self.assertEqual(alias_identity(2, 'CS 2F (LE)'), 'cs2f')
        self.assertEqual(alias_identity(2, 'CSAI 2C (LE)'), 'csai2c')
        self.assertEqual(alias_identity(3, 'CSAI 3A'), 'cseai3a')
        for letter in 'ABCDEFGH':
            self.assertEqual(alias_identity(3, f'CSAI 3{letter}'), f'cseai3{letter.lower()}')
        self.assertIsNone(alias_identity(4, 'CSAI 4A'))
        self.assertEqual(duplicate_target_identity(4, 'CSE(AI)-4A'), 'csai4a')
        for letter in 'ABCDEF':
            self.assertEqual(duplicate_target_identity(4, f'CSE(AI)-4{letter}'), f'csai4{letter.lower()}')
        self.assertEqual(duplicate_target_identity(3, 'CSE(CCML)-3A'), 'ccml3a')
        self.assertEqual(duplicate_target_identity(3, 'CSE(IOTBC)-3A'), 'iotbc3a')
        self.assertEqual(duplicate_target_identity(4, 'CSE(CCML)-4A'), 'ccml4')
        self.assertEqual(duplicate_target_identity(4, 'CSE(IOTBC)-4A'), 'iotbc4')

    def test_dry_run_preserves_alias_pk_and_strength(self):
        old = Section.objects.create(program=self.program, semester=self.semester, year=2, name='CS-2F', student_strength=60)
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(2, 'CS 2F (LE)', 57)])
            output = StringIO()
            call_command('sync_sections_from_excel', str(path), dry_run=True, stdout=output)
        old.refresh_from_db()
        self.assertEqual((old.name, old.student_strength), ('CS-2F', 60))
        self.assertIn(f'ALIAS_RENAME {old.pk}', output.getvalue())
        self.assertIn('matched: 1', output.getvalue())

    def test_canonical_section_prevents_duplicate_rename_and_merges_safely(self):
        canonical = Section.objects.create(program=self.program, semester=self.semester, year=4, name='CSAI-4A', student_strength=60)
        stale = Section.objects.create(program=self.program, semester=self.semester, year=4, name='CSE(AI)-4A', student_strength=60)
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(4, 'CSAI 4A', 55)])
            output = StringIO()
            call_command('sync_sections_from_excel', str(path), dry_run=True, stdout=output)
            call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO(), stderr=StringIO())
        self.assertIn('duplicate candidates: 1', output.getvalue())
        self.assertIn(f'STALE={stale.pk}', output.getvalue())
        self.assertIn(f'TARGET={canonical.pk}', output.getvalue())
        self.assertFalse(Section.objects.filter(pk=stale.pk).exists())
        canonical.refresh_from_db()
        self.assertEqual(canonical.student_strength, 55)

    def make_scheduled_pair(self, *, year=4, stale_name='CSE(AI)-4A', canonical_name='CSAI-4A'):
        canonical = Section.objects.create(program=self.program, semester=self.semester, year=year, name=canonical_name, student_strength=60)
        stale = Section.objects.create(program=self.program, semester=self.semester, year=year, name=stale_name, student_strength=60)
        course = Course.objects.create(code=f'SYNC-{year}', name='Test Course', short_code='TC', credit=3)
        offering = CourseOffering.objects.create(semester=self.semester, section=stale, course=course, weekly_periods=1)
        user = User.objects.create_user(f'sync-{year}@test.local', 'test-password')
        timetable = Timetable.objects.create(institution=self.session.institution, academic_session=self.session, semester=self.semester, department=self.program.department, title='Test', created_by=user)
        version = TimetableVersion.objects.create(timetable=timetable, version_no=1, created_by=user)
        template = TimeSlotTemplate.objects.create(institution=self.session.institution, name='Test')
        slot = TimeSlot.objects.create(template=template, label='09:00-10:00', start_time=time(9), end_time=time(10), order=1)
        entry = ScheduleEntry.objects.create(version=version, section=stale, course_offering=offering, weekday=0, start_slot=slot, delivery_mode='ONLINE', locked=True)
        return canonical, stale, offering, entry

    def test_referenced_merge_preserves_entry_offering_policy_and_alias_pk(self):
        canonical, stale, offering, entry = self.make_scheduled_pair()
        policy = SectionDeliveryPolicy.objects.create(section=stale, academic_session=self.session, semester=self.semester, mode='HYBRID', offline_weekday=0)
        alias = Section.objects.create(program=self.program, semester=self.semester, year=2, name='CS-2F', student_strength=60)
        before = ScheduleEntry.objects.count()
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(4, 'CSAI 4A', 55), (2, 'CS 2F (LE)', 57)])
            call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO())
        entry.refresh_from_db(); offering.refresh_from_db(); policy.refresh_from_db(); alias.refresh_from_db()
        self.assertEqual(ScheduleEntry.objects.count(), before)
        self.assertEqual((entry.section_id, entry.course_offering_id, entry.locked, entry.delivery_mode), (canonical.pk, offering.pk, True, 'ONLINE'))
        self.assertEqual((offering.section_id, policy.section_id), (canonical.pk, canonical.pk))
        self.assertFalse(Section.objects.filter(pk=stale.pk).exists())
        self.assertEqual((alias.pk, alias.name, alias.student_strength), (alias.pk, 'CS 2F (LE)', 57))
        report = analytics(SimpleNamespace(user=SimpleNamespace(role='SUPER_ADMIN'), query_params={}))
        self.assertEqual(report['students_by_year']['Year 4'], 55)
        self.assertEqual(report['students_by_year']['Year 2'], 57)

    def test_weekly_off_policy_moves_to_canonical(self):
        canonical = Section.objects.create(program=self.program, semester=self.semester, year=3, name='CCML-3A', student_strength=60)
        stale = Section.objects.create(program=self.program, semester=self.semester, year=3, name='CSE(CCML)-3A', student_strength=60)
        policy = SectionWeeklyOffPolicy.objects.create(section=stale, academic_session=self.session, semester=self.semester, policy_type='WEEKLY_OFF', weekday=4)
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(3, 'CCML 3A', 54)])
            call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO())
        policy.refresh_from_db()
        self.assertEqual(policy.section_id, canonical.pk)
        self.assertFalse(Section.objects.filter(pk=stale.pk).exists())

    def test_conflicting_policy_prevents_all_updates(self):
        canonical, stale, _, _ = self.make_scheduled_pair()
        SectionDeliveryPolicy.objects.create(section=stale, academic_session=self.session, semester=self.semester, mode='HYBRID', offline_weekday=0)
        SectionDeliveryPolicy.objects.create(section=canonical, academic_session=self.session, semester=self.semester, mode='STANDARD')
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(4, 'CSAI 4A', 55)])
            with self.assertRaises(CommandError):
                call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO())
        canonical.refresh_from_db()
        self.assertEqual(canonical.student_strength, 60)
        self.assertTrue(Section.objects.filter(pk=stale.pk).exists())

    def test_duplicate_offering_prevents_apply(self):
        canonical, stale, offering, _ = self.make_scheduled_pair()
        CourseOffering.objects.create(semester=self.semester, section=canonical, course=offering.course, weekly_periods=1)
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(4, 'CSAI 4A', 55)])
            with self.assertRaises(CommandError):
                call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO())
        self.assertTrue(Section.objects.filter(pk=stale.pk).exists())

    def test_schedule_collision_prevents_apply(self):
        canonical, stale, offering, entry = self.make_scheduled_pair()
        ScheduleEntry.objects.create(version=entry.version, section=canonical, course_offering=offering, weekday=0, start_slot=entry.start_slot)
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(4, 'CSAI 4A', 55)])
            with self.assertRaises(CommandError):
                call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO())
        self.assertTrue(Section.objects.filter(pk=stale.pk).exists())

    def test_late_merge_failure_rolls_back_prior_changes(self):
        canonical, stale, _, _ = self.make_scheduled_pair()
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, [(4, 'CSAI 4A', 55)])
            with patch('common.management.commands.sync_sections_from_excel.merge_pair', side_effect=CommandError('late failure')):
                with self.assertRaises(CommandError):
                    call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO())
        canonical.refresh_from_db()
        self.assertEqual(canonical.student_strength, 60)
        self.assertTrue(Section.objects.filter(pk=stale.pk).exists())

    def test_all_ten_duplicate_name_pairs_merge_in_isolated_database(self):
        names = [(3, 'CSE(CCML)-3A', 'CCML 3A'), (3, 'CSE(IOTBC)-3A', 'IOTBC 3A')]
        names += [(4, f'CSE(AI)-4{letter}', f'CSAI 4{letter}') for letter in 'ABCDEF']
        names += [(4, 'CSE(CCML)-4A', 'CCML 4'), (4, 'CSE(IOTBC)-4A', 'IOTBC 4')]
        rows = []
        canonical_ids = []
        for year, stale_name, canonical_name in names:
            canonical = Section.objects.create(program=self.program, semester=self.semester, year=year, name=canonical_name, student_strength=60)
            Section.objects.create(program=self.program, semester=self.semester, year=year, name=stale_name, student_strength=60)
            canonical_ids.append(canonical.pk)
            rows.append((year, canonical_name, 54))
        with TemporaryDirectory() as directory:
            path = self.workbook(directory, rows)
            call_command('sync_sections_from_excel', str(path), apply=True, stdout=StringIO())
        self.assertEqual(Section.objects.count(), 10)
        self.assertEqual(set(Section.objects.values_list('pk', flat=True)), set(canonical_ids))
        self.assertEqual(sum(Section.objects.values_list('student_strength', flat=True)), 540)
