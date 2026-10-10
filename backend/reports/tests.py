from datetime import date
from types import SimpleNamespace

from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role, User
from academics.models import AcademicSession, Course, CourseOffering, Section, SectionDeliveryPolicy, SectionWeeklyOffPolicy, Semester
from common.models import TimeSlot, TimeSlotTemplate
from faculty.models import CourseOfferingFaculty, Faculty, FacultyAvailability
from institutions.models import Department, Institution, Program
from reports.services.reporting import faculty_workload, published_faculty_count, scheduled_entry_analytics, students_by_year
from rooms.models import Room, RoomAvailability
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, Timetable, TimetableVersion
from scheduling.weekdays import WORKING_DAYS


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
        self.faculty = [Faculty.objects.create(name=f'Published Faculty {i:02}', employee_code=f'OVF{i:02}', department=department) for i in range(20)]
        zero_workload = Faculty.objects.create(name='No Published Workload', employee_code='OVZERO', department=department)

        for index, member in enumerate(self.faculty):
            entry = ScheduleEntry.objects.create(version=self.current, section=section, course_offering=offering, weekday=index % 5, start_slot=slot, block_length=1)
            ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=member)
            if index == 0:
                repeated = ScheduleEntry.objects.create(version=self.current, section=section, course_offering=offering, weekday=0, start_slot=slot, block_length=1)
                ScheduleEntryFaculty.objects.create(schedule_entry=repeated, faculty=member)
                ScheduleEntryFaculty.objects.create(schedule_entry=repeated, faculty=self.faculty[1], role='CO_FACULTY')
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

        filtered_query = {'faculty': str(self.faculty[5].pk)}
        filtered_overview = self.client.get('/api/reports/analytics/', filtered_query)
        filtered_workload = self.client.get('/api/reports/faculty-workload/', filtered_query)
        filtered_workload_count = sum(row['total_scheduled_periods'] > 0 for row in filtered_workload.data)
        self.assertEqual(filtered_overview.data['total_faculty'], 1)
        self.assertEqual(filtered_workload_count, 1)
        self.assertEqual(filtered_overview.data['total_faculty'], filtered_workload_count)

    def test_count_honors_the_same_section_filter_as_scheduled_entries(self):
        request = SimpleNamespace(user=self.admin, query_params={'section': '00000000-0000-0000-0000-000000000001'})
        entries, _, _ = scheduled_entry_analytics(request)
        self.assertEqual(published_faculty_count(entries), 0)


class RoomUtilizationReportTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(name='Room Report Institute', code='ROOMREPORT')
        department = Department.objects.create(institution=institution, name='Engineering', code='ROOMENG')
        program = Program.objects.create(department=department, name='Computing', code='ROOMCSE', duration_years=4)
        self.program = program
        session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
        semester = Semester.objects.create(session=session, name='Odd', number=1, type='ODD', start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))
        section = Section.objects.create(program=program, semester=semester, year=1, name='A')
        self.section = section
        course = Course.objects.create(code='RU101', name='Room Utilization', short_code='RU', credit=3)
        offering = CourseOffering.objects.create(semester=semester, section=section, course=course, weekly_periods=2)
        self.offering = offering
        self.admin = User.objects.create_user('room-report@test.local', 'pass', role=Role.SUPER_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        template = TimeSlotTemplate.objects.create(institution=institution, name='Seven teaching periods')
        self.slots = []
        periods = [(9, False), (10, False), (11, False), (12, False), (13, True), (14, False), (15, False), (16, False)]
        for order, (hour, is_break) in enumerate(periods, start=1):
            slot = TimeSlot.objects.create(template=template, label=f'{hour:02}:00', start_time=f'{hour:02}:00', end_time=f'{hour + 1:02}:00', order=order, is_break=is_break)
            if not is_break:
                self.slots.append(slot)

        timetable = Timetable.objects.create(institution=institution, academic_session=session, semester=semester, department=department, title='Room utilization', created_by=self.admin)
        self.current = TimetableVersion.objects.create(timetable=timetable, version_no=2, status='PUBLISHED', created_by=self.admin)
        previous = TimetableVersion.objects.create(timetable=timetable, version_no=1, status='PUBLISHED', created_by=self.admin)
        draft = TimetableVersion.objects.create(timetable=timetable, version_no=3, status='DRAFT', created_by=self.admin)

        self.room_30 = Room.objects.create(code='LGF001', building='North', floor='LGF', capacity=40, room_type='CLASSROOM')
        self.room_35 = Room.objects.create(code='LGF004', building='North', floor='LGF', capacity=40, room_type='CLASSROOM')
        self.room_zero = Room.objects.create(code='UGF008', building='', floor='UGF', capacity=40, room_type='CLASSROOM')
        self.room_block = Room.objects.create(code='407', building='South', floor='4', capacity=40, room_type='CLASSROOM')

        for weekday in WORKING_DAYS:
            for slot_index, slot in enumerate(self.slots):
                if slot_index < 6:
                    self._entry(self.current, section, offering, self.room_30, weekday, slot, 1)
                self._entry(self.current, section, offering, self.room_35, weekday, slot, 1)
        self._entry(self.current, section, offering, self.room_block, 1, self.slots[0], 2)
        self._entry(self.current, section, offering, None, 2, self.slots[0], 1, delivery_mode='ONLINE')
        self._entry(previous, section, offering, self.room_zero, 0, self.slots[1], 1)
        self._entry(draft, section, offering, self.room_zero, 0, self.slots[2], 1)

        # A global weekly block removes one period on each canonical teaching day.
        RoomAvailability.objects.create(room=self.room_zero, weekday=None, time_slot=self.slots[0], status='BLOCKED')
        # A weekday-specific maintenance block removes only one room-period.
        RoomAvailability.objects.create(room=self.room_block, weekday=1, time_slot=self.slots[6], status='MAINTENANCE')
        RoomAvailability.objects.create(room=self.room_block, weekday=1, time_slot=self.slots[6], status='BLOCKED')

    def _entry(self, version, section, offering, room, weekday, slot, block_length, delivery_mode='OFFLINE'):
        return ScheduleEntry.objects.create(
            version=version, section=section, course_offering=offering,
            weekday=weekday, start_slot=slot, block_length=block_length,
            room=room, delivery_mode=delivery_mode,
        )

    def _rows_by_code(self):
        response = self.client.get('/api/reports/room-utilization/')
        self.assertEqual(response.status_code, 200)
        return {row['room_no']: row for row in response.data}

    def test_room_percentages_use_weekday_capacity_block_lengths_and_published_scope(self):
        rows = self._rows_by_code()
        self.assertEqual(len(self.slots), 7)
        self.assertEqual(rows['LGF001']['total_available_slots'], 35)
        self.assertEqual(rows['LGF001']['occupied_slots'], 30)
        self.assertEqual(rows['LGF001']['utilization_percentage'], 85.71)
        self.assertEqual(rows['LGF004']['occupied_slots'], 35)
        self.assertEqual(rows['LGF004']['utilization_percentage'], 100.0)
        self.assertEqual(rows['UGF008']['occupied_slots'], 0)
        self.assertEqual(rows['UGF008']['total_available_slots'], 30)
        self.assertEqual(rows['UGF008']['utilization_percentage'], 0)
        self.assertEqual(rows['407']['occupied_slots'], 2)
        self.assertEqual(rows['407']['total_available_slots'], 34)
        self.assertEqual(rows['407']['utilization_percentage'], 5.88)
        self.assertTrue(all(0 <= row['utilization_percentage'] <= 100 for row in rows.values()))

    def test_roomless_classes_do_not_count_and_breaks_are_not_capacity(self):
        rows = self._rows_by_code()
        self.assertEqual(rows['LGF001']['total_available_slots'], 35)
        self.assertEqual(rows['407']['occupied_slots'], 2)
        self.assertEqual(rows['UGF008']['building'], 'Unknown')

    def test_building_and_overall_analytics_use_weighted_room_period_totals(self):
        rows = list(self._rows_by_code().values())
        north_occupied = sum(row['occupied_slots'] for row in rows if row['building'] == 'North')
        north_available = sum(row['total_available_slots'] for row in rows if row['building'] == 'North')
        self.assertEqual(round(north_occupied * 100 / north_available, 2), 92.86)

        analytics = self.client.get('/api/reports/analytics/')
        self.assertEqual(analytics.status_code, 200)
        self.assertEqual(analytics.data['room_utilization_percentage'], 50.0)
        self.assertLessEqual(analytics.data['room_utilization_percentage'], 100)

    def test_free_rooms_counts_block_overlap_and_scoped_room_availability(self):
        overlap_room = Room.objects.create(code='OVERLAP', building='West', floor='1', capacity=60, has_projector=True)
        weekday_only_room = Room.objects.create(code='TUESDAY', building='West', floor='1', capacity=55)
        monday_blocked = Room.objects.create(code='MONBLOCK', building='West', floor='1', capacity=50)
        global_blocked = Room.objects.create(code='GLOBALBLOCK', building='West', floor='1', capacity=45)
        maintenance_room = Room.objects.create(code='MAINT', building='West', floor='1', capacity=45)
        self._entry(self.current, self.section, self.offering, overlap_room, 0, self.slots[0], 2)
        RoomAvailability.objects.create(room=weekday_only_room, weekday=1, time_slot=self.slots[1], status='BLOCKED')
        RoomAvailability.objects.create(room=self.room_block, weekday=1, time_slot=self.slots[1], status='MAINTENANCE')
        RoomAvailability.objects.create(room=monday_blocked, weekday=0, time_slot=self.slots[1], status='BLOCKED')
        RoomAvailability.objects.create(room=global_blocked, weekday=None, time_slot=self.slots[1], status='BLOCKED')
        RoomAvailability.objects.create(room=maintenance_room, weekday=0, time_slot=self.slots[1], status='MAINTENANCE')

        response = self.client.get('/api/reports/free-rooms/', {'weekday': 0, 'time_slot': str(self.slots[1].pk)})
        self.assertEqual(response.status_code, 200)
        payload = response.data
        free_codes = {room['room_no'] for room in payload['rooms']}
        self.assertNotIn('OVERLAP', free_codes)  # starts at 09; block overlaps 10
        self.assertNotIn('MONBLOCK', free_codes)
        self.assertNotIn('GLOBALBLOCK', free_codes)
        self.assertNotIn('MAINT', free_codes)
        self.assertIn('TUESDAY', free_codes)  # Tuesday-only block does not affect Monday
        self.assertIn('407', free_codes)  # Tuesday occupancy/maintenance does not affect Monday
        self.assertEqual(payload['audit']['free_rooms'], payload['audit']['active_rooms'] - payload['audit']['occupied_rooms'] - payload['audit']['blocked_rooms'])
        self.assertEqual(payload['summary']['free_rooms'], len(payload['rooms']))
        self.assertEqual(payload['summary']['projector_available'], sum(row['projector'] == 'Yes' for row in payload['rooms']))
        self.assertEqual(payload['summary']['largest_capacity'], max((row['capacity'] for row in payload['rooms']), default=0))
        self.assertIn('OVERLAP', payload['audit']['occupied_codes'])
        self.assertIn('MONBLOCK', payload['audit']['blocked_codes'])

    def test_free_rooms_filters_and_analytics_use_final_result(self):
        Room.objects.create(code='PROJECTED', building='East', floor='2', capacity=65, room_type='LAB', has_projector=True)
        Room.objects.create(code='SMALL', building='East', floor='1', capacity=25, room_type='CLASSROOM')
        self._entry(self.current, self.section, self.offering, None, 0, self.slots[0], 1, delivery_mode='ONLINE')
        unfiltered = self.client.get('/api/reports/free-rooms/', {'weekday': 0, 'time_slot': str(self.slots[0].pk)})
        self.assertEqual(unfiltered.status_code, 200)
        self.assertIn('PROJECTED', {row['room_no'] for row in unfiltered.data['rooms']})
        response = self.client.get('/api/reports/free-rooms/', {
            'weekday': 0, 'time_slot': str(self.slots[0].pk), 'capacity': '', 'room_type': '', 'projector': 'true',
        })
        self.assertEqual(response.status_code, 200)
        payload = response.data
        self.assertTrue(all(row['projector'] == 'Yes' for row in payload['rooms']))
        self.assertTrue(all(row['capacity'] >= 0 for row in payload['rooms']))
        self.assertEqual(payload['summary']['free_rooms'], len(payload['rooms']))
        self.assertEqual(sum(row['count'] for row in payload['analytics']['by_building']), len(payload['rooms']))
        self.assertEqual(sum(row['count'] for row in payload['analytics']['capacity_distribution']), len(payload['rooms']))
        lab = self.client.get('/api/reports/free-rooms/', {
            'weekday': 0, 'time_slot': str(self.slots[0].pk), 'room_type': 'LAB',
        })
        self.assertTrue(all(row['room_type'] == 'Lab' for row in lab.data['rooms']))
        exported = self.client.get('/api/reports/free-rooms/', {
            'weekday': 0, 'time_slot': str(self.slots[0].pk), 'export': 'csv',
        })
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.content.decode().startswith('Room,Room Type,Capacity,Building,Floor,Projector'))


class SectionTimetableReportTests(TestCase):
    def setUp(self):
        institution = Institution.objects.create(name='Section Report Institute', code='SECTIONREPORT')
        self.department = Department.objects.create(institution=institution, name='Engineering', code='SECENG')
        self.btech = Program.objects.create(department=self.department, name='B.Tech Computer Science', code='BTCS', duration_years=4)
        self.mtech = Program.objects.create(department=self.department, name='M.Tech CSE (AI)', code='MTCSAI', duration_years=2)
        self.session = AcademicSession.objects.create(institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30))
        self.semester = Semester.objects.create(session=self.session, name='V Semester', number=5, type='ODD', start_date=date(2026, 7, 1), end_date=date(2026, 12, 31))
        self.btech_section = Section.objects.create(program=self.btech, semester=self.semester, year=3, name='CS-3A', student_strength=58)
        self.mtech_section = Section.objects.create(program=self.mtech, semester=self.semester, year=1, name='MTCSAI-1', student_strength=24)
        self.gap_section = Section.objects.create(program=self.btech, semester=self.semester, year=4, name='CS-4A')
        self.courses = [
            Course.objects.create(code='ST101', name='Algorithms', short_code='ALG', credit=3),
            Course.objects.create(code='ST102', name='Networks', short_code='NET', credit=3),
            Course.objects.create(code='ST201', name='Machine Learning', short_code='ML', credit=4),
        ]
        self.offerings = [
            CourseOffering.objects.create(semester=self.semester, section=self.btech_section, course=self.courses[0], weekly_periods=2),
            CourseOffering.objects.create(semester=self.semester, section=self.btech_section, course=self.courses[1], weekly_periods=1),
            CourseOffering.objects.create(semester=self.semester, section=self.mtech_section, course=self.courses[2], weekly_periods=1),
            CourseOffering.objects.create(semester=self.semester, section=self.gap_section, course=self.courses[1], weekly_periods=2),
        ]
        self.user = User.objects.create_user('section-report@test.local', 'pass', role=Role.SUPER_ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.template = TimeSlotTemplate.objects.create(institution=institution, name='Section day')
        self.slots = [
            TimeSlot.objects.create(template=self.template, label='09:00-10:00', start_time='09:00', end_time='10:00', order=1),
            TimeSlot.objects.create(template=self.template, label='10:00-11:00', start_time='10:00', end_time='11:00', order=2),
            TimeSlot.objects.create(template=self.template, label='11:00-12:00 BREAK', start_time='11:00', end_time='12:00', order=3, is_break=True),
            TimeSlot.objects.create(template=self.template, label='12:00-13:00', start_time='12:00', end_time='13:00', order=4),
        ]
        self.room = Room.objects.create(code='ST407', building='Engineering', floor='4', capacity=40)
        self.faculty_a = Faculty.objects.create(name='Dr. Alpha', employee_code='STFA', department=self.department)
        self.faculty_b = Faculty.objects.create(name='Dr. Beta', employee_code='STFB', department=self.department)
        timetable = Timetable.objects.create(institution=institution, academic_session=self.session, semester=self.semester, department=self.department, title='Section report', created_by=self.user)
        self.current = TimetableVersion.objects.create(timetable=timetable, version_no=2, status='PUBLISHED', created_by=self.user)
        self.previous = TimetableVersion.objects.create(timetable=timetable, version_no=1, status='PUBLISHED', created_by=self.user)
        self.draft = TimetableVersion.objects.create(timetable=timetable, version_no=3, status='DRAFT', created_by=self.user)
        team_entry = self._entry(self.current, self.btech_section, self.offerings[0], 0, self.slots[0], 2, self.room)
        ScheduleEntryFaculty.objects.create(schedule_entry=team_entry, faculty=self.faculty_a)
        ScheduleEntryFaculty.objects.create(schedule_entry=team_entry, faculty=self.faculty_b, role='CO_FACULTY')
        online_entry = self._entry(self.current, self.btech_section, self.offerings[1], 1, self.slots[3], 1, None, delivery_mode='ONLINE')
        ScheduleEntryFaculty.objects.create(schedule_entry=online_entry, faculty=self.faculty_a)
        mtech_entry = self._entry(self.current, self.mtech_section, self.offerings[2], 2, self.slots[0], 1, self.room)
        ScheduleEntryFaculty.objects.create(schedule_entry=mtech_entry, faculty=self.faculty_b)
        self._entry(self.previous, self.btech_section, self.offerings[0], 3, self.slots[0], 1, self.room)
        self._entry(self.draft, self.btech_section, self.offerings[0], 4, self.slots[0], 1, self.room)
        SectionWeeklyOffPolicy.objects.create(section=self.btech_section, academic_session=self.session, semester=self.semester,
            policy_type=SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF, weekday=4)
        SectionDeliveryPolicy.objects.create(section=self.gap_section, academic_session=self.session, semester=self.semester,
            mode=SectionDeliveryPolicy.Mode.HYBRID, offline_weekday=0)

    def _entry(self, version, section, offering, weekday, slot, block_length, room, delivery_mode='OFFLINE'):
        return ScheduleEntry.objects.create(version=version, section=section, course_offering=offering,
            weekday=weekday, start_slot=slot, block_length=block_length, room=room, delivery_mode=delivery_mode)

    def test_selected_section_uses_current_published_entries_and_correct_period_metrics(self):
        response = self.client.get('/api/reports/section-timetable/visual/', {'section': str(self.btech_section.pk)})
        self.assertEqual(response.status_code, 200)
        payload = response.data
        self.assertEqual(payload['selected_section']['program'], 'B.Tech Computer Science')
        self.assertEqual(payload['selected_section']['year'], 3)
        self.assertEqual(payload['selected_section']['semester'], 'V Semester')
        self.assertEqual(payload['summary']['scheduled_classes'], 2)
        self.assertEqual(payload['summary']['scheduled_periods'], 3)
        self.assertEqual(payload['summary']['distinct_courses'], 2)
        self.assertEqual(payload['summary']['distinct_faculty'], 2)
        self.assertEqual(payload['summary']['rooms_used'], 1)
        self.assertEqual(payload['summary']['online_periods'], 1)
        self.assertEqual(payload['summary']['offline_periods'], 2)
        self.assertEqual(len(payload['entries']), 2)
        self.assertTrue(all(entry['section_id'] == str(self.btech_section.pk) for entry in payload['entries']))
        block = next(entry for entry in payload['entries'] if entry['block_length'] == 2)
        self.assertEqual(len(block['occupied_slot_ids']), 2)
        self.assertEqual(block['end_time'], '11:00')
        self.assertTrue(any(slot['is_break'] for slot in payload['slots']))
        self.assertEqual(payload['weekly_off']['day'], 'Friday')
        self.assertEqual(payload['summary']['free_periods'], 9)
        self.assertEqual(payload['summary']['weekly_off_periods'], 3)
        self.assertEqual(payload['summary']['break_periods'], 5)
        self.assertEqual(payload['room_load'][0]['room'], 'ST407')
        self.assertEqual(sorted(row['periods'] for row in payload['faculty_load']), [2, 3])
        online = next(entry for entry in payload['entries'] if entry['delivery_mode'] == 'ONLINE')
        self.assertEqual(online['room'], '')
        self.assertEqual(next(entry for entry in payload['entries'] if entry['delivery_mode'] == 'OFFLINE')['room'], 'ST407')
        self.assertEqual(payload['course_load'][0]['status'], 'Complete')

    def test_all_sections_overview_includes_gap_sections_and_aggregates_periods(self):
        response = self.client.get('/api/reports/section-timetable/visual/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['entries'], [])
        self.assertEqual(response.data['summary']['total_sections'], 3)
        self.assertEqual(response.data['summary']['scheduled_classes'], 3)
        self.assertEqual(response.data['summary']['scheduled_periods'], 4)
        self.assertEqual(response.data['summary']['distinct_courses'], 3)
        self.assertEqual(response.data['summary']['distinct_faculty'], 2)
        self.assertEqual(response.data['summary']['sections_complete'], 2)
        self.assertEqual(response.data['summary']['sections_with_gaps'], 1)
        gap = next(row for row in response.data['sections'] if row['section_id'] == str(self.gap_section.pk))
        self.assertEqual(gap['scheduling_gap'], 2)
        self.assertEqual(gap['delivery_policy'], 'HYBRID')
        self.assertEqual(gap['offline_weekday'], 0)

    def test_program_year_semester_session_and_day_filters_share_one_scope(self):
        response = self.client.get('/api/reports/section-timetable/visual/', {
            'program': str(self.btech.pk), 'year': 3, 'semester': str(self.semester.pk),
            'session': str(self.session.pk), 'weekday': 1, 'section': str(self.btech_section.pk),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['scheduled_classes'], 1)
        self.assertEqual(response.data['summary']['scheduled_periods'], 1)
        self.assertEqual(response.data['entries'][0]['delivery_mode'], 'ONLINE')
        self.assertEqual(response.data['day_load'][0]['weekday'], 1)
        self.assertEqual(response.data['day_load'][0]['periods'], 1)

    def test_options_are_unpaginated_and_labels_are_from_section_master_data(self):
        options = self.client.get('/api/reports/section-timetable/options/')
        self.assertEqual(options.status_code, 200)
        self.assertEqual(len(options.data['sections']), 3)
        labels = {row['name']: row['label'] for row in options.data['sections']}
        self.assertIn('M.Tech CSE (AI)', labels['MTCSAI-1'])
        self.assertIn('Year 1', labels['MTCSAI-1'])
        self.assertIn('B.Tech Computer Science', labels['CS-3A'])
        self.assertEqual(set(options.data['years']), {3, 1, 4})

    def test_exports_have_human_readable_columns_without_uuid_fields(self):
        csv_response = self.client.get('/api/reports/section-timetable/', {'section': str(self.btech_section.pk), 'export': 'csv'})
        self.assertEqual(csv_response.status_code, 200)
        header = csv_response.content.decode().splitlines()[0]
        for heading in ('Section', 'Program', 'Year', 'Semester', 'Academic Session', 'Day', 'Start Time', 'End Time', 'Course Code', 'Course Name', 'Faculty', 'Room', 'Delivery Mode', 'Block Length'):
            self.assertIn(heading, header)
        self.assertNotIn('entry_id', header)
        empty_export = self.client.get('/api/reports/section-timetable/', {'section': str(self.gap_section.pk), 'export': 'csv'})
        self.assertEqual(len(empty_export.content.decode().splitlines()), 1)
        self.assertIn('Course Code', empty_export.content.decode().splitlines()[0])
        xlsx_response = self.client.get('/api/reports/section-timetable/', {'section': str(self.mtech_section.pk), 'export': 'xlsx'})
        self.assertEqual(xlsx_response.status_code, 200)
        from openpyxl import load_workbook
        from io import BytesIO
        sheet = load_workbook(BytesIO(xlsx_response.content), read_only=True).active
        self.assertIn('Program', [cell.value for cell in next(sheet.iter_rows())])

class FacultyTimetableVisualReportTests(SectionTimetableReportTests):
    def test_selected_faculty_metrics_blocks_availability_team_teaching_and_current_version(self):
        FacultyAvailability.objects.create(faculty=self.faculty_a, weekday=0, time_slot=self.slots[3], is_available=False)
        response = self.client.get('/api/reports/faculty-timetable/visual/', {'faculty': str(self.faculty_a.pk)})
        self.assertEqual(response.status_code, 200)
        payload = response.data
        self.assertEqual(payload['summary']['scheduled_classes'], 2)
        self.assertEqual(payload['summary']['teaching_periods'], 3)
        self.assertEqual(payload['summary']['distinct_courses'], 2)
        self.assertEqual(payload['summary']['distinct_sections'], 1)
        self.assertEqual(payload['summary']['rooms_used'], 1)
        self.assertEqual(payload['summary']['online_periods'], 1)
        self.assertEqual(len(payload['entries']), 2)
        block = next(entry for entry in payload['entries'] if entry['block_length'] == 2)
        self.assertEqual(len(block['occupied_slot_ids']), 2)
        self.assertEqual(block['end_time'], '11:00')
        self.assertEqual(next(entry for entry in payload['entries'] if entry['delivery_mode'] == 'ONLINE')['room'], '')
        self.assertEqual(payload['availability']['unavailable_periods'], 1)
        self.assertEqual(payload['availability_states'][2]['state'], 'BREAK')
        self.assertEqual(payload['availability_states'][3]['state'], 'UNAVAILABLE')
        self.assertTrue(payload['availability']['configured'])
        self.assertEqual(payload['course_load'][0]['sections'], 1)
        self.assertEqual(payload['section_load'][0]['periods'], 3)
        self.assertEqual(payload['room_load'][-1]['delivery_mode'], 'ONLINE')

        # Team-taught blocks belong in both faculty schedules, without duplicate rows.
        teammate = self.client.get('/api/reports/faculty-timetable/visual/', {'faculty': str(self.faculty_b.pk)})
        team_rows = [row for row in teammate.data['entries'] if row['block_length'] == 2]
        self.assertEqual(len(team_rows), 1)
        self.assertEqual(team_rows[0]['faculty_names'], 'Dr. Alpha, Dr. Beta')
        self.assertEqual(len(payload['slots']), 4)
        self.assertTrue(any(slot['is_break'] for slot in payload['slots']))

    def test_all_faculty_analytics_and_filters_share_current_published_scope(self):
        response = self.client.get('/api/reports/faculty-timetable/visual/')
        self.assertEqual(response.status_code, 200)
        payload = response.data
        self.assertIsNone(payload['selected_faculty'])
        self.assertEqual(payload['summary']['faculty_with_published_classes'], 2)
        self.assertEqual(payload['summary']['total_teaching_periods'], 6)
        self.assertEqual(len(payload['entries']), 0)
        self.assertEqual(len(payload['faculties']), 2)

        filtered = self.client.get('/api/reports/faculty-timetable/visual/', {
            'program': str(self.btech.pk), 'semester': str(self.semester.pk), 'weekday': 1,
        })
        self.assertEqual(filtered.data['summary']['total_teaching_periods'], 1)
        self.assertEqual(filtered.data['summary']['faculty_with_published_classes'], 1)
        selected = self.client.get('/api/reports/faculty-timetable/visual/', {
            'faculty': str(self.faculty_a.pk), 'program': str(self.btech.pk), 'weekday': 1,
        })
        self.assertEqual(selected.data['summary']['scheduled_classes'], 1)
        self.assertEqual(selected.data['summary']['teaching_periods'], 1)
        self.assertEqual(selected.data['day_load'][0]['day'], 'Tuesday')

    def test_faculty_without_user_profile_and_role_self_restriction(self):
        self.assertIsNone(self.faculty_a.user_id)
        account = User.objects.create_user('faculty-self@test.local', 'pass', role=Role.FACULTY)
        self.faculty_a.user = account
        self.faculty_a.save(update_fields=['user'])
        self.client.force_authenticate(account)
        own = self.client.get('/api/reports/faculty-timetable/visual/', {'faculty': str(self.faculty_b.pk)})
        self.assertEqual(own.status_code, 403)
        self_only = self.client.get('/api/reports/faculty-timetable/visual/')
        self.assertEqual(self_only.status_code, 200)
        self.assertEqual(self_only.data['selected_faculty']['id'], str(self.faculty_a.pk))
        self.assertEqual(self_only.data['summary']['scheduled_classes'], 2)
        self.options = self.client.get('/api/reports/faculty-timetable/options/')
        self.assertEqual([item['id'] for item in self.options.data], [str(self.faculty_a.pk)])

    def test_human_readable_exports_keep_csv_and_xlsx(self):
        query = {'faculty': str(self.faculty_a.pk)}
        csv_response = self.client.get('/api/reports/faculty-timetable/', {**query, 'export': 'csv'})
        self.assertEqual(csv_response.status_code, 200)
        header = csv_response.content.decode().splitlines()[0]
        for label in ('Faculty', 'Employee Code', 'Department', 'Day', 'Start Time', 'End Time', 'Course Code', 'Course Name', 'Section', 'Program', 'Year', 'Semester', 'Academic Session', 'Room', 'Delivery Mode', 'Block Length'):
            self.assertIn(label, header)
        self.assertNotIn('entry_id', header)
        xlsx_response = self.client.get('/api/reports/faculty-timetable/', {**query, 'export': 'xlsx'})
        self.assertEqual(xlsx_response.status_code, 200)
        from io import BytesIO
        from openpyxl import load_workbook
        sheet = load_workbook(BytesIO(xlsx_response.content), read_only=True).active
        self.assertIn('Course Code', [cell.value for cell in next(sheet.iter_rows())])


class CourseAllocationReportTests(SectionTimetableReportTests):
    def setUp(self):
        super().setUp()

    def prepare_allocation_cases(self):
        # Four distinct allocation states, including a requirement with no
        # published ScheduleEntry at all.
        self.offerings[0].weekly_periods = 3  # two-period block => under-scheduled
        self.offerings[0].save(update_fields=['weekly_periods'])
        self.offerings[1].weekly_periods = 1  # complete
        self.offerings[1].save(update_fields=['weekly_periods'])
        self.offerings[2].weekly_periods = 2  # one period => under-scheduled
        self.offerings[2].save(update_fields=['weekly_periods'])
        extra_course = Course.objects.create(code='ST301', name='Over Plan', short_code='OVER', credit=3)
        self.over_offering = CourseOffering.objects.create(semester=self.semester, section=self.btech_section,
            course=extra_course, weekly_periods=1, default_class_type=CourseOffering.ClassType.PRACTICAL)
        self.over_entry = self._entry(self.current, self.btech_section, self.over_offering, 3, self.slots[0], 2, self.room)
        ScheduleEntryFaculty.objects.create(schedule_entry=self.over_entry, faculty=self.faculty_a)
        self.other_room = Room.objects.create(code='ST408', building='Engineering', floor='4', capacity=35)
        self.second_block = self._entry(self.current, self.btech_section, self.offerings[0], 0, self.slots[3], 1, self.other_room)
        ScheduleEntryFaculty.objects.create(schedule_entry=self.second_block, faculty=self.faculty_a)
        CourseOfferingFaculty.objects.create(course_offering=self.offerings[0], faculty=self.faculty_b, role='CO_FACULTY')
        CourseOfferingFaculty.objects.create(course_offering=self.offerings[0], faculty=self.faculty_a)
        CourseOfferingFaculty.objects.create(course_offering=self.offerings[1], faculty=self.faculty_a)
        CourseOfferingFaculty.objects.create(course_offering=self.over_offering, faculty=self.faculty_a)

    def test_offering_first_metrics_statuses_and_zero_schedule_are_correct(self):
        self.prepare_allocation_cases()
        response = self.client.get('/api/reports/course-allocation/visual/')
        self.assertEqual(response.status_code, 200)
        payload = response.data
        self.assertEqual(payload['summary']['total_offerings'], 5)
        self.assertEqual(payload['summary']['fully_scheduled'], 2)
        self.assertEqual(payload['summary']['under_scheduled'], 1)
        self.assertEqual(payload['summary']['unscheduled'], 1)
        self.assertEqual(payload['summary']['over_scheduled'], 1)
        self.assertEqual(payload['summary']['required_periods'], 9)
        self.assertEqual(payload['summary']['scheduled_periods'], 7)
        self.assertEqual(payload['summary']['scheduled_classes'], 5)
        self.assertEqual(payload['summary']['coverage_percentage'], 66.67)
        by_section = {(row['course_code'], row['section']): row for row in payload['offerings']}
        self.assertEqual(by_section[('ST101', 'CS-3A')]['scheduled_periods'], 3)
        self.assertEqual(by_section[('ST101', 'CS-3A')]['status'], 'COMPLETE')
        self.assertEqual(by_section[('ST101', 'CS-3A')]['scheduled_classes'], 2)
        self.assertEqual(by_section[('ST101', 'CS-3A')]['actual_rooms'], ['ST407', 'ST408'])
        self.assertEqual(by_section[('ST101', 'CS-3A')]['faculty'], ['Dr. Alpha', 'Dr. Beta'])
        self.assertEqual(by_section[('ST201', 'MTCSAI-1')]['status'], 'UNDER_SCHEDULED')
        self.assertEqual(by_section[('ST102', 'CS-3A')]['status'], 'COMPLETE')
        self.assertEqual(by_section[('ST301', 'CS-3A')]['status'], 'OVER_SCHEDULED')
        self.assertEqual(by_section[('ST301', 'CS-3A')]['completion_percentage'], 200)
        self.assertEqual(by_section[('ST301', 'CS-3A')]['actual_rooms'], ['ST407'])
        self.assertEqual(by_section[('ST301', 'CS-3A')]['activity_type_label'], 'Practical')
        self.assertEqual(by_section[('ST201', 'MTCSAI-1')]['scheduled_periods'], 1)
        self.assertEqual(by_section[('ST301', 'CS-3A')]['scheduled_classes'], 1)

    def test_unscheduled_offering_faculty_filters_and_report_filters(self):
        self.prepare_allocation_cases()
        facultyless = self.client.get('/api/reports/course-allocation/visual/')
        zero = next(row for row in facultyless.data['offerings'] if row['course_code'] == 'ST102' and row['section'] == 'CS-4A')
        self.assertEqual(zero['scheduled_periods'], 0)
        self.assertEqual(zero['difference'], -2)
        self.assertEqual(zero['completion_percentage'], 0)
        self.assertEqual(zero['status'], 'UNSCHEDULED')
        self.assertIn('Needs Faculty Assignment', zero['issues'])

        filtered = self.client.get('/api/reports/course-allocation/visual/', {
            'program': str(self.btech.pk), 'year': 3, 'semester': str(self.semester.pk),
            'faculty': str(self.faculty_a.pk),
        })
        self.assertEqual(filtered.status_code, 200)
        self.assertTrue(all(row['year'] == 3 and row['program_id'] == str(self.btech.pk) for row in filtered.data['offerings']))
        self.assertEqual(filtered.data['summary']['total_offerings'], 3)
        self.assertEqual(filtered.data['summary']['scheduled_periods'], 6)
        selected_section = self.client.get('/api/reports/course-allocation/visual/', {'section': str(self.gap_section.pk)})
        self.assertEqual(selected_section.data['summary']['total_offerings'], 1)
        self.assertEqual(selected_section.data['summary']['unscheduled'], 1)

    def test_global_coverage_does_not_allow_over_schedule_to_offset_unscheduled(self):
        self.prepare_allocation_cases()
        self.offerings[0].weekly_periods = 2
        self.offerings[0].save(update_fields=['weekly_periods'])
        self.offerings[1].weekly_periods = 2
        self.offerings[1].save(update_fields=['weekly_periods'])
        # ST101 is complete at 2, ST102 in CSAI-3A has two scheduled periods,
        # and the new offering is over by one; remove the schedules from ST102
        # for an explicit zero-coverage requirement in this ratio fixture.
        ScheduleEntry.objects.filter(version=self.current, course_offering=self.offerings[1]).delete()
        response = self.client.get('/api/reports/course-allocation/visual/', {'program': str(self.btech.pk)})
        rows = response.data['offerings']
        required = sum(row['required_periods'] for row in rows)
        covered = sum(min(row['scheduled_periods'], row['required_periods']) for row in rows)
        self.assertEqual(response.data['summary']['coverage_percentage'], round(covered * 100 / required, 2))
        self.assertLess(response.data['summary']['coverage_percentage'], 100)

    def test_three_required_two_periods_is_under_scheduled_and_schedule_blocks_are_distinct(self):
        self.prepare_allocation_cases()
        self.second_block.delete()
        self.offerings[0].weekly_periods = 3
        self.offerings[0].save(update_fields=['weekly_periods'])
        response = self.client.get('/api/reports/course-allocation/visual/')
        row = next(item for item in response.data['offerings'] if item['course_code'] == 'ST101' and item['section'] == 'CS-3A')
        self.assertEqual(row['scheduled_periods'], 2)
        self.assertEqual(row['scheduled_classes'], 1)
        self.assertEqual(row['status'], 'UNDER_SCHEDULED')
        self.assertEqual(row['difference'], -1)

    def test_activity_types_options_and_human_exports_include_unscheduled_offerings(self):
        self.prepare_allocation_cases()
        options = self.client.get('/api/reports/course-allocation/options/')
        self.assertEqual(options.status_code, 200)
        self.assertIn({'id': 'PRACTICAL', 'name': 'Practical'}, options.data['activity_types'])
        self.assertIn({'id': 'LECTURE', 'name': 'Lecture'}, options.data['activity_types'])
        csv_response = self.client.get('/api/reports/course-allocation/', {'export': 'csv'})
        self.assertEqual(csv_response.status_code, 200)
        lines = csv_response.content.decode().splitlines()
        self.assertIn('Required Weekly Periods', lines[0])
        self.assertIn('Actual Rooms', lines[0])
        self.assertNotIn('course_offering_id', lines[0])
        self.assertTrue(any('CS-4A' in line and ',0,' in line for line in lines[1:]))
        xlsx_response = self.client.get('/api/reports/course-allocation/', {'export': 'xlsx'})
        self.assertEqual(xlsx_response.status_code, 200)
        from io import BytesIO
        from openpyxl import load_workbook
        sheet = load_workbook(BytesIO(xlsx_response.content), read_only=True).active
        self.assertIn('Required Weekly Periods', [cell.value for cell in next(sheet.iter_rows())])


class UnscheduledDashboardReportTests(CourseAllocationReportTests):
    def test_scope_wide_metrics_and_zero_schedule_rows_are_returned(self):
        self.prepare_allocation_cases()
        response = self.client.get('/api/reports/unscheduled/')
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(data['summary']['total_offerings'], 5)
        self.assertEqual(data['summary']['required_periods'], 9)
        self.assertEqual(data['summary']['scheduled_periods'], 7)
        self.assertEqual(data['summary']['remaining_periods'], 3)
        self.assertEqual(data['summary']['coverage_percentage'], 66.67)
        zero = next(row for row in data['offerings'] if row['course_code'] == 'ST102' and row['section'] == 'CS-4A')
        self.assertEqual((zero['required_periods'], zero['scheduled_periods'], zero['difference'], zero['status']), (2, 0, -2, 'UNSCHEDULED'))

    def test_all_clear_scope_keeps_nonzero_scheduled_metrics(self):
        self.offerings[0].weekly_periods = 2
        self.offerings[0].save(update_fields=['weekly_periods'])
        response = self.client.get('/api/reports/unscheduled/', {'course': str(self.offerings[0].course_id)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['incomplete_offerings'], 0)
        self.assertEqual(response.data['summary']['required_periods'], 2)
        self.assertEqual(response.data['summary']['scheduled_periods'], 2)
        self.assertEqual(response.data['summary']['coverage_percentage'], 100)


class RoomTimetableVisualReportTests(RoomUtilizationReportTests):
    def test_visual_report_uses_only_current_published_physical_room_entries(self):
        response = self.client.get('/api/reports/room-timetable/visual/', {'room': str(self.room_block.pk)})
        self.assertEqual(response.status_code, 200)
        payload = response.data
        self.assertEqual(payload['summary']['room_no'], '407')
        self.assertEqual(payload['summary']['scheduled_classes'], 1)
        self.assertEqual(payload['summary']['occupied_periods'], 2)
        self.assertEqual(payload['summary']['available_periods'], 34)
        self.assertEqual(payload['summary']['sections'], 1)
        self.assertEqual(payload['summary']['courses'], 1)
        self.assertEqual(len(payload['entries']), 1)
        entry = payload['entries'][0]
        self.assertEqual(entry['room'], '407')
        self.assertEqual(entry['block_length'], 2)
        self.assertEqual(len(entry['occupied_slot_ids']), 2)
        self.assertEqual(entry['delivery_mode'], 'OFFLINE')
        self.assertNotIn(self.slots[0].label, [slot['label'] for slot in payload['slots'] if slot['is_break']])
        self.assertIn('MAINTENANCE', [row['status'] for row in payload['availability']])

        all_rooms = self.client.get('/api/reports/room-timetable/visual/')
        self.assertEqual(all_rooms.status_code, 200)
        self.assertTrue(all(row['room_id'] for row in all_rooms.data['entries']))
        self.assertNotIn('ONLINE', [row['delivery_mode'] for row in all_rooms.data['entries']])
        self.assertEqual(all_rooms.data['summary']['scheduled_classes'], 66)

    def test_visual_report_filters_apply_to_entries_and_metrics(self):
        second_semester = Semester.objects.create(
            session=self.current.timetable.academic_session, name='Even', number=2, type='EVEN',
            start_date=date(2027, 1, 1), end_date=date(2027, 6, 30),
        )
        other_section = Section.objects.create(program=self.program, semester=second_semester, year=2, name='B')
        other_course = Course.objects.create(code='RU202', name='Filtered Course', short_code='RF', credit=3)
        other_offering = CourseOffering.objects.create(semester=second_semester, section=other_section, course=other_course, weekly_periods=1)
        self._entry(self.current, other_section, other_offering, self.room_30, 0, self.slots[6], 1)

        response = self.client.get('/api/reports/room-timetable/visual/', {'room': str(self.room_30.pk), 'semester': str(second_semester.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['scheduled_classes'], 1)
        self.assertEqual(response.data['summary']['occupied_periods'], 1)
        self.assertEqual(response.data['summary']['sections'], 1)
        self.assertEqual(response.data['summary']['courses'], 1)
        self.assertEqual(response.data['entries'][0]['course_code'], 'RU202')

    def test_room_summary_counts_distinct_sections_and_courses(self):
        self._entry(self.current, self.section, self.offering, self.room_block, 2, self.slots[2], 1)
        section = self.section
        course = Course.objects.create(code='RU102', name='Second Course', short_code='R2', credit=3)
        offering = CourseOffering.objects.create(semester=self.current.timetable.semester, section=section, course=course, weekly_periods=1)
        self._entry(self.current, section, offering, self.room_block, 3, self.slots[3], 1)
        response = self.client.get('/api/reports/room-timetable/visual/', {'room': str(self.room_block.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['scheduled_classes'], 3)
        self.assertEqual(response.data['summary']['occupied_periods'], 4)
        self.assertEqual(response.data['summary']['sections'], 1)
        self.assertEqual(response.data['summary']['courses'], 2)

    def test_visual_report_validates_room_and_returns_valid_empty_room(self):
        invalid = self.client.get('/api/reports/room-timetable/visual/', {'room': 'not-a-uuid'})
        self.assertEqual(invalid.status_code, 400)
        empty = self.client.get('/api/reports/room-timetable/visual/', {'room': str(self.room_zero.pk)})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data['summary']['scheduled_classes'], 0)
        self.assertEqual(empty.data['summary']['occupied_periods'], 0)
        self.assertGreater(empty.data['summary']['available_periods'], 0)

    def test_room_timetable_options_are_all_active_rooms_and_exports_are_human_readable(self):
        options = self.client.get('/api/reports/room-timetable/options/')
        self.assertEqual(options.status_code, 200)
        self.assertEqual(len(options.data), 4)
        self.assertIn('Classroom', next(row['name'] for row in options.data if row['id'] == str(self.room_block.pk)))

        exported = self.client.get('/api/reports/room-timetable/', {'room': str(self.room_block.pk), 'export': 'csv'})
        self.assertEqual(exported.status_code, 200)
        for heading in ('Room', 'Day', 'Start Time', 'End Time', 'Course Code', 'Course Name', 'Section', 'Program', 'Year', 'Semester', 'Faculty', 'Delivery Mode'):
            self.assertIn(heading, exported.content.decode().splitlines()[0])
        self.assertNotIn('entry_id', exported.content.decode())

    def test_weekday_filter_uses_matching_capacity_scope(self):
        response = self.client.get('/api/reports/room-timetable/visual/', {'room': str(self.room_block.pk), 'weekday': 1})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary']['available_periods'], 6)
        self.assertEqual(response.data['summary']['occupied_periods'], 2)
        self.assertTrue(all(row['weekday'] == 1 for row in response.data['entries']))
