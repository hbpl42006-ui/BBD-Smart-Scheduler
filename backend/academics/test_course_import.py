from decimal import Decimal

from django.test import TestCase

from academics.models import AcademicSession, Course, CourseOffering, Semester, Section
from common.imports import commit, validate
from institutions.models import Department, Institution, Program
from accounts.models import Role, User
from faculty.models import Faculty, FacultyArrangement, CourseOfferingFaculty
from rooms.models import Room
from common.models import TimeSlotTemplate, TimeSlot
from scheduling.models import Timetable, TimetableVersion, ScheduleEntry, ScheduleEntryFaculty
from rest_framework.test import APIClient


class CourseImportSafetyTests(TestCase):
    def setUp(self):
        self.institution = Institution.objects.create(name='Test University', code='TU')
        self.department = Department.objects.create(institution=self.institution, name='Computer Science', code='CSE')
        self.program = Program.objects.create(department=self.department, name='B.Tech CSE', code='BT', duration_years=4)
        self.session = AcademicSession.objects.create(institution=self.institution, name='2026-27', start_date='2026-01-01', end_date='2026-12-31')
        self.semester = Semester.objects.create(session=self.session, name='Odd', number=1, type='ODD', start_date='2026-01-01', end_date='2026-06-30')
        self.section = Section.objects.create(program=self.program, semester=self.semester, year=1, name='A')
        self.admin = User.objects.create_user('admin@test.local', 'pass', role=Role.SUPER_ADMIN)

    def row(self, code='CS101', name='Programming', short='PF', credit='3.0', active='TRUE'):
        return {'Code': code, 'Name': name, 'Short Code': short, 'Credit': credit, 'Active': active}

    def test_upsert_updates_same_pk_and_is_idempotent(self):
        course = Course.objects.create(code='CS101', name='Old', short_code='OLD', credit=2, active=True)
        result = commit('courses', [self.row(name='New', short='NEW', credit='4.0', active='FALSE')], 'UPSERT')
        course.refresh_from_db()
        self.assertEqual(result['updated'], 1)
        self.assertEqual(course.name, 'New')
        self.assertEqual(course.short_code, 'NEW')
        self.assertEqual(course.credit, Decimal('4.0'))
        self.assertFalse(course.active)
        self.assertEqual(Course.objects.filter(code='CS101').count(), 1)
        commit('courses', [self.row(name='Newer')], 'UPSERT')
        self.assertEqual(Course.objects.filter(code='CS101').count(), 1)

    def test_create_only_skips_existing(self):
        Course.objects.create(code='CS101', name='Existing', short_code='E', credit=1)
        valid, errors = validate('courses', [self.row()], 'CREATE_ONLY')
        self.assertFalse(errors)
        self.assertEqual(valid[0].get('_skip'), 'Course code already exists')
        result = commit('courses', [self.row()], 'CREATE_ONLY')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(Course.objects.filter(code='CS101').count(), 1)

    def test_replace_archives_missing_course_without_deleting_offering(self):
        kept = Course.objects.create(code='CS101', name='Keep', short_code='K', credit=3)
        missing = Course.objects.create(code='MA101', name='Math', short_code='M', credit=3)
        offering = CourseOffering.objects.create(course=missing, semester=self.semester, section=self.section, weekly_periods=3)
        valid, errors = validate('courses', [self.row()], 'REPLACE')
        self.assertFalse(errors)
        self.assertEqual(len([c for c in Course.objects.filter(active=True) if c.code != 'CS101']), 1)
        result = commit('courses', [self.row()], 'REPLACE')
        kept.refresh_from_db(); missing.refresh_from_db(); offering.refresh_from_db()
        self.assertEqual(result['archive'], 1)
        self.assertTrue(kept.active)
        self.assertFalse(missing.active)
        self.assertEqual(offering.course_id, missing.pk)

    def test_duplicate_codes_and_invalid_active_are_rejected(self):
        rows = [self.row(), self.row(name='Duplicate')]
        _, errors = validate('courses', rows, 'UPSERT')
        self.assertTrue(any('Duplicate course code' in e['message'] for e in errors))
        _, errors = validate('courses', [self.row(active='maybe')], 'UPSERT')
        self.assertTrue(any('Active must be' in e['message'] for e in errors))

    def test_header_normalization_and_boolean_values(self):
        for value, expected in [('TRUE', True), ('False', False), ('Yes', True), ('No', False), ('1', True), ('0', False)]:
            valid, errors = validate('courses', [{' CODE ': 'X'+value, 'Name': 'Course', 'short code': 'C', 'Credit': '1', 'Active': value}], 'UPSERT')
            self.assertFalse(errors)
            self.assertEqual(valid[0]['active'], expected)

    def _published_entry(self, course):
        timetable=Timetable.objects.create(institution=self.institution,academic_session=self.session,semester=self.semester,department=self.department,title='Published',created_by=self.admin)
        version=TimetableVersion.objects.create(timetable=timetable,version_no=1,status='PUBLISHED',created_by=self.admin)
        room=Room.objects.create(code='R1',building='Main',floor='1',capacity=60,room_type='CLASSROOM')
        template=TimeSlotTemplate.objects.create(institution=self.institution,name='T')
        slot=TimeSlot.objects.create(template=template,label='09-10',start_time='09:00',end_time='10:00',order=1)
        offering=CourseOffering.objects.create(course=course,semester=self.semester,section=self.section,weekly_periods=1)
        entry=ScheduleEntry.objects.create(version=version,section=self.section,course_offering=offering,weekday=0,start_slot=slot,room=room)
        faculty=Faculty.objects.create(name='Faculty',employee_code='F1',initials='F',department=self.department)
        assignment=ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=faculty)
        return version, offering, entry, assignment, faculty

    def test_published_timetable_is_immutable_during_upsert(self):
        course=Course.objects.create(code='CS101',name='Old',short_code='OLD',credit=2)
        version, offering, entry, assignment, _ = self._published_entry(course)
        result=commit('courses',[self.row(name='Updated',short='UPD',credit='4.0')],'UPSERT')
        course.refresh_from_db()
        self.assertEqual(result['updated'],1); self.assertEqual(course.pk,offering.course_id); self.assertEqual(course.name,'Updated')
        self.assertTrue(TimetableVersion.objects.filter(pk=version.pk,status='PUBLISHED').exists())
        self.assertTrue(ScheduleEntry.objects.filter(pk=entry.pk).exists()); self.assertTrue(ScheduleEntryFaculty.objects.filter(pk=assignment.pk).exists())

    def test_referenced_course_delete_returns_409_and_archive_preserves_history(self):
        course=Course.objects.create(code='CS101',name='Course',short_code='C',credit=3)
        _, offering, entry, _, faculty=self._published_entry(course)
        FacultyArrangement.objects.create(schedule_entry=entry,arrangement_date='2026-02-02',absent_faculty=faculty,substitute_faculty=faculty,created_by=self.admin)
        client=APIClient(); client.force_authenticate(self.admin)
        response=client.delete(f'/api/courses/{course.pk}/')
        self.assertEqual(response.status_code,409); self.assertTrue(response.data['can_archive']); self.assertTrue(Course.objects.filter(pk=course.pk).exists())
        course.active=False; course.save(update_fields=['active'])
        self.assertTrue(CourseOffering.objects.filter(pk=offering.pk,course=course).exists()); self.assertTrue(ScheduleEntry.objects.filter(pk=entry.pk).exists()); self.assertEqual(FacultyArrangement.objects.filter(schedule_entry=entry).count(),1)

    def test_invalid_import_does_not_partially_update_or_create(self):
        course=Course.objects.create(code='CS101',name='Old',short_code='OLD',credit=2)
        rows=[self.row(name='Changed'),self.row(code='NEW101',name='New'),self.row(code='BAD',active='maybe')]
        with self.assertRaises(ValueError): commit('courses',rows,'UPSERT')
        course.refresh_from_db(); self.assertEqual(course.name,'Old'); self.assertFalse(Course.objects.filter(code='NEW101').exists())
