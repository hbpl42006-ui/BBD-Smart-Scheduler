"""Deterministic, compact provenance for a generation run's scheduling inputs."""
import hashlib
import json


SNAPSHOT_VERSION = 1


def _rows(queryset, fields, ordering=None):
    rows = list(queryset.order_by(*(ordering or fields[:1])).values(*fields))
    return sorted(rows, key=lambda row: tuple(str(row.get(key) or '') for key in (ordering or fields[:1])))


def build_input_snapshot(version, config, source_fingerprint):
    from academics.models import Course, CourseOffering, Section, SectionDeliveryPolicy, SectionWeeklyOffPolicy
    from common.models import TimeSlot, TimeSlotTemplate
    from faculty.models import CourseOfferingFaculty, Faculty, FacultyAvailability
    from rooms.models import Room, RoomAvailability, RoomEligibilityException

    timetable = version.timetable
    scope = {str(value) for value in config.get('section_ids', [])}
    sections = Section.objects.filter(semester=timetable.semester, program__department__institution=timetable.institution)
    if scope:
        sections = sections.filter(pk__in=scope)
    section_ids = list(sections.values_list('id', flat=True))
    offerings = CourseOffering.objects.filter(semester=timetable.semester, section_id__in=section_ids)
    if config.get('offering_rules'):
        offering_ids = [rule.get('course_offering_id') for rule in config['offering_rules'] if rule.get('course_offering_id')]
        offerings = offerings.filter(pk__in=offering_ids)
    offering_ids = list(offerings.values_list('id', flat=True))
    faculty_ids = set(CourseOfferingFaculty.objects.filter(course_offering_id__in=offering_ids).values_list('faculty_id', flat=True))
    faculty_ids.update(member.get('faculty_id') for rule in config.get('offering_rules', []) for member in rule.get('faculty', []) if member.get('faculty_id'))
    slots = TimeSlot.objects.filter(template__institution=timetable.institution)
    slot_ids = list(slots.values_list('id', flat=True))

    snapshot = {
        'version': SNAPSHOT_VERSION,
        'source': {'version_id': str(version.pk), 'fingerprint': source_fingerprint},
        'scope': {'institution_id': str(timetable.institution_id), 'session_id': str(timetable.academic_session_id), 'semester_id': str(timetable.semester_id)},
        'locked_source_entries': _rows(version.entries.filter(locked=True), ['id', 'section_id', 'course_offering_id', 'weekday', 'start_slot_id', 'block_length', 'room_id', 'entry_type', 'delivery_mode', 'locked']),
        'sections': _rows(sections, ['id', 'program_id', 'semester_id', 'year', 'name', 'student_strength', 'delivery_policy', 'offline_weekday']),
        'offerings': _rows(offerings, ['id', 'semester_id', 'section_id', 'course_id', 'weekly_periods', 'default_class_type', 'required_block_size', 'allow_remainder_period', 'room_type_requirement', 'preferred_room_id', 'active']),
        'courses': _rows(Course.objects.filter(pk__in=offerings.values_list('course_id', flat=True)), ['id', 'code', 'active']),
        'faculty_policy': _rows(Faculty.objects.filter(pk__in=faculty_ids), ['id', 'department_id', 'active', 'max_daily_periods', 'max_weekly_periods']),
        'offering_faculty': _rows(CourseOfferingFaculty.objects.filter(course_offering_id__in=offering_ids), ['id', 'course_offering_id', 'faculty_id', 'role', 'priority']),
        'faculty_availability': _rows(FacultyAvailability.objects.filter(faculty_id__in=faculty_ids, time_slot_id__in=slot_ids), ['id', 'faculty_id', 'weekday', 'time_slot_id', 'is_available', 'preference_weight']),
        'section_delivery_policies': _rows(SectionDeliveryPolicy.objects.filter(section_id__in=section_ids, academic_session=timetable.academic_session, semester=timetable.semester), ['id', 'section_id', 'academic_session_id', 'semester_id', 'mode', 'offline_weekday', 'source', 'active']),
        'section_weekly_off_policies': _rows(SectionWeeklyOffPolicy.objects.filter(section_id__in=section_ids, academic_session=timetable.academic_session, semester=timetable.semester), ['id', 'section_id', 'academic_session_id', 'semester_id', 'policy_type', 'weekday', 'source', 'is_active']),
        'time_slots': _rows(slots, ['id', 'template_id', 'order', 'start_time', 'end_time', 'is_break']),
        'time_slot_templates': _rows(TimeSlotTemplate.objects.filter(institution=timetable.institution), ['id', 'institution_id']),
        # Room building/floor/facilities are display-only; scheduling constraints are captured below.
        'rooms': _rows(Room.objects.all(), ['id', 'code', 'capacity', 'room_type', 'allowed_year', 'reserved_program_id', 'reserved_year', 'exclusive_reservation', 'active']),
        'room_availability': _rows(RoomAvailability.objects.filter(time_slot_id__in=slot_ids), ['id', 'room_id', 'weekday', 'date', 'time_slot_id', 'status']),
        'room_eligibility_exceptions': _rows(RoomEligibilityException.objects.all(), ['id', 'room_id', 'program_id', 'year', 'course_id', 'allow', 'active']),
        'fixed_room_rules_version': 'room-policies-v1:105/RBS5151;516/604;NCS4353-exception',
        'solver_config': config,
        'objective_config': config.get('soft_constraints', {}),
    }
    canonical = json.dumps(snapshot, sort_keys=True, separators=(',', ':'), default=str)
    normalized = json.loads(canonical)
    return normalized, hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def provenance_for_run(run):
    if not run.input_snapshot or not run.input_fingerprint:
        return {'generation_input_fingerprint': None, 'current_input_fingerprint': None, 'input_fingerprint_matches': None, 'provenance_status': 'LEGACY_NO_SNAPSHOT'}
    from scheduling.solver.service import fingerprint
    _, current = build_input_snapshot(run.source_version, run.input_config, fingerprint(run.source_version))
    matches = current == run.input_fingerprint
    return {'generation_input_fingerprint': run.input_fingerprint, 'current_input_fingerprint': current, 'input_fingerprint_matches': matches, 'provenance_status': 'EXACT_MATCH' if matches else 'CURRENT_INPUT_CHANGED'}
