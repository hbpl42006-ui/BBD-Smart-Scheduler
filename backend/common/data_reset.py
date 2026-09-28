from django.db import transaction
from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from audit.models import AuditEvent
from common.models import TimeSlot, TimeSlotTemplate
from faculty.models import ArrangementAttendanceEvidence, CourseOfferingFaculty, Faculty, FacultyArrangement, FacultyAvailability
from institutions.models import Program
from notifications.models import Notification, NotificationDelivery
from rooms.models import Room
from scheduling.models import GenerationRun, ScheduleEntry, ScheduleEntryFaculty, Timetable, TimetableVersion

MODES = {'ACADEMIC_DATA_ONLY', 'SCHEDULING_ONLY'}
COUNT_MODELS = {'arrangement_attendance_evidence': ArrangementAttendanceEvidence, 'faculty_arrangements': FacultyArrangement, 'schedule_entry_faculty': ScheduleEntryFaculty, 'schedule_entries': ScheduleEntry, 'generation_runs': GenerationRun, 'timetable_versions': TimetableVersion, 'timetables': Timetable, 'faculty_availability': FacultyAvailability, 'course_offering_faculty': CourseOfferingFaculty, 'course_offerings': CourseOffering, 'sections': Section, 'faculty': Faculty, 'courses': Course, 'rooms': Room, 'time_slots': TimeSlot, 'time_slot_templates': TimeSlotTemplate, 'semesters': Semester, 'sessions': AcademicSession, 'programs': Program}

def _names(mode):
    return ['arrangement_attendance_evidence', 'faculty_arrangements', 'schedule_entry_faculty', 'schedule_entries', 'generation_runs', 'timetable_versions', 'timetables'] if mode == 'SCHEDULING_ONLY' else list(COUNT_MODELS)

def preview(mode):
    if mode not in MODES: raise ValueError('Invalid reset mode.')
    return {'mode': mode, 'will_delete': {name: COUNT_MODELS[name].objects.count() for name in _names(mode)}, 'preserved': {'users': 'All user accounts', 'roles': 'User roles and permissions', 'system_configuration': 'Institutions, audit configuration, and migrations'}}

@transaction.atomic
def execute(mode):
    if mode not in MODES: raise ValueError('Invalid reset mode.')
    names = _names(mode)
    if 'timetable_versions' in names: TimetableVersion.objects.update(previous_version=None)
    if mode == 'ACADEMIC_DATA_ONLY': Faculty.objects.update(user=None)
    deleted = {}
    for name in names:
        deleted[name] = COUNT_MODELS[name].objects.all().delete()[0]
    if mode == 'ACADEMIC_DATA_ONLY':
        event_types = ['FACULTY_ARRANGEMENT_ASSIGNED', 'FACULTY_ARRANGEMENT_REASSIGNED']
        deleted['notification_deliveries'] = NotificationDelivery.objects.filter(notification__event_type__in=event_types).delete()[0]
        deleted['notifications'] = Notification.objects.filter(event_type__in=event_types).delete()[0]
    return deleted
