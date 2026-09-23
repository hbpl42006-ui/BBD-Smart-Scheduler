from io import BytesIO

from django.db import transaction
from openpyxl import Workbook

from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from faculty.models import CourseOfferingFaculty, Faculty
from rooms.models import Room

ALIASES = {
    'session': 'session', 'academic_session': 'session', 'session_name': 'session',
    'semester': 'semester', 'semester_name': 'semester',
    'course_code': 'course_code', 'course': 'course_code',
    'section': 'section', 'section_name': 'section',
    'weekly_periods': 'weekly_periods', 'periods': 'weekly_periods', 'periods_per_week': 'weekly_periods',
    'employee_code': 'employee_code', 'faculty_code': 'employee_code',
    'room_no': 'room', 'room_number': 'room', 'room': 'room', 'room_code': 'room', 'preferred_room': 'room', 'preferred_room_no': 'room', 'preferred_room_code': 'room',
    'class_type': 'class_type', 'default_class_type': 'class_type', 'block_size': 'block_size', 'required_block_size': 'block_size',
    'room_type': 'room_type', 'room_type_requirement': 'room_type', 'active': 'active', 'is_active': 'active',
    'faculty_role': 'role', 'role': 'role', 'priority': 'priority', 'faculty_priority': 'priority',
}
REQUIRED = {'session', 'semester', 'course_code', 'section', 'weekly_periods', 'employee_code', 'room'}
CLASS_TYPES = {value.lower(): value for value, _ in CourseOffering.ClassType.choices}


def _key(value):
    return str(value or '').strip().lower().replace('-', '_').replace(' ', '_').replace('.', '')


def _normalize(rows):
    return [{ALIASES.get(_key(key), _key(key)): value for key, value in row.items()} for row in rows]


def _text(value):
    return '' if value is None else str(value).strip()


def _integer(value, label, positive=True):
    text = _text(value)
    try:
        number = float(text)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a positive integer.' if positive else f'{label} must be an integer.')
    if not number.is_integer() or (number <= 0 if positive else number < 0):
        raise ValueError(f'{label} must be a positive integer.' if positive else f'{label} must be an integer.')
    return int(number)


def _boolean(value):
    text = _text(value).lower()
    if not text:
        return True
    if text in {'yes', 'true', '1'}:
        return True
    if text in {'no', 'false', '0'}:
        return False
    raise ValueError('Active must be Yes, No, True, False, 1, or 0.')


def _resolve(row):
    session_name = _text(row.get('session'))
    session_qs = AcademicSession.objects.filter(name__iexact=session_name)
    if not session_name or session_qs.count() == 0:
        raise ValueError(f'Academic Session "{session_name}" not found.')
    if session_qs.count() > 1:
        raise ValueError(f'Academic Session "{session_name}" is ambiguous.')
    session = session_qs.get()
    semester_name = _text(row.get('semester'))
    semesters = Semester.objects.filter(session=session, name__iexact=semester_name)
    if not semester_name or semesters.count() == 0:
        raise ValueError(f'Semester "{semester_name}" not found in Session "{session_name}".')
    if semesters.count() > 1:
        raise ValueError(f'Semester "{semester_name}" is ambiguous in Session "{session_name}".')
    semester = semesters.get()
    course_code = _text(row.get('course_code'))
    course = Course.objects.filter(code__iexact=course_code).first()
    if not course:
        raise ValueError(f'Course "{course_code}" not found.')
    section_name = _text(row.get('section'))
    sections = Section.objects.filter(semester=semester, name__iexact=section_name)
    if sections.count() == 0:
        raise ValueError(f'Section "{section_name}" not found.')
    if sections.count() > 1:
        raise ValueError(f'Section "{section_name}" is ambiguous.')
    section = sections.get()
    employee = _text(row.get('employee_code'))
    faculty = Faculty.objects.filter(employee_code__iexact=employee).first()
    if not faculty:
        raise ValueError(f'Faculty employee code "{employee}" not found.')
    room_code = _text(row.get('room'))
    if not room_code:
        raise ValueError('Room No. is required.')
    room = Room.objects.filter(code__iexact=room_code).first()
    if not room:
        raise ValueError(f'Room "{room_code}" not found.')
    if not _text(row.get('weekly_periods')):
        raise ValueError('Weekly Periods is required.')
    values = {
        'weekly_periods': _integer(row.get('weekly_periods'), 'Weekly Periods'),
        'default_class_type': CLASS_TYPES.get(_text(row.get('class_type')).lower(), 'LECTURE'),
        'required_block_size': _integer(row.get('block_size') or 1, 'Block Size'),
        'room_type_requirement': _text(row.get('room_type')),
        'active': _boolean(row.get('active')),
        'role': _text(row.get('role')) or 'PRIMARY',
        'priority': _integer(row.get('priority') or 1, 'Priority', positive=False),
    }
    if _text(row.get('class_type')) and _text(row.get('class_type')).lower() not in CLASS_TYPES:
        raise ValueError(f'Invalid Class Type "{row.get("class_type")}".')
    return session, semester, course, section, faculty, room, values


def parse(rows):
    normalized = _normalize(rows)
    result = {'total': len(normalized), 'valid': 0, 'invalid': 0, 'warnings': 0, 'rows': [], 'errors': []}
    if not normalized or REQUIRED - set(normalized[0]):
        missing = sorted(REQUIRED - set(normalized[0] if normalized else []))
        result['invalid'] = 1
        result['errors'] = [{'row': 1, 'message': 'Missing required columns: ' + ', '.join(missing)}]
        return result, []
    prepared = []
    identities = {}
    for number, row in enumerate(normalized, 2):
        try:
            session, semester, course, section, faculty, room, values = _resolve(row)
            identity = (semester.pk, section.pk, course.pk)
            previous = identities.get(identity)
            comparable = {key: values[key] for key in ('weekly_periods', 'default_class_type', 'required_block_size', 'room_type_requirement', 'active')}
            comparable['room'] = room.code
            if previous and previous != comparable:
                raise ValueError(f'Conflicting Course Offering values for {course.code} / {section.name}; repeated rows must be consistent.')
            identities[identity] = comparable
            offering = CourseOffering.objects.filter(semester=semester, section=section, course=course).first()
            mapped = CourseOfferingFaculty.objects.filter(course_offering=offering, faculty=faculty).exists() if offering else False
            action = ('UPDATE OFFERING' if offering and previous else 'EXISTING OFFERING') if offering else 'CREATE OFFERING'
            action += ' + UPDATE FACULTY' if mapped else ' + ADD FACULTY'
            prepared.append((identity, session, semester, course, section, faculty, room, values, number))
            result['rows'].append({'row': number, 'status': 'VALID', 'session': session.name, 'semester': semester.name, 'course': course.code, 'section': section.name, 'weekly_periods': values['weekly_periods'], 'employee_code': faculty.employee_code, 'room': room.code, 'action': action})
        except Exception as exc:
            result['invalid'] += 1; result['errors'].append({'row': number, 'message': str(exc)})
    result['valid'] = len(prepared)
    return result, prepared


@transaction.atomic
def commit(prepared):
    counts = {'offerings_created': 0, 'offerings_updated': 0, 'faculty_assignments_created': 0, 'faculty_assignments_updated': 0, 'skipped': 0, 'failed': 0, 'warnings': 0, 'errors': []}
    for identity, session, semester, course, section, faculty, room, values, _ in prepared:
        offering, created = CourseOffering.objects.get_or_create(semester=semester, section=section, course=course, defaults={key: values[key] for key in ('weekly_periods', 'default_class_type', 'required_block_size', 'room_type_requirement', 'active')})
        if created: counts['offerings_created'] += 1
        else:
            for key in ('weekly_periods', 'default_class_type', 'required_block_size', 'room_type_requirement', 'active'):
                setattr(offering, key, values[key])
            offering.preferred_room = room; offering.save(); counts['offerings_updated'] += 1
        if created:
            offering.preferred_room = room; offering.save(update_fields=['preferred_room'])
        mapping, mapping_created = CourseOfferingFaculty.objects.get_or_create(course_offering=offering, faculty=faculty, defaults={'role': values['role'], 'priority': values['priority']})
        if mapping_created: counts['faculty_assignments_created'] += 1
        else:
            mapping.role = values['role']; mapping.priority = values['priority']; mapping.save(update_fields=['role', 'priority', 'updated_at']); counts['faculty_assignments_updated'] += 1
    return counts


def template():
    workbook = Workbook(); sheet = workbook.active; sheet.title = 'Course Offerings'
    headers = ['Session', 'Semester', 'Course Code', 'Section', 'Weekly Periods', 'Employee Code', 'Room No.', 'Class Type', 'Block Size', 'Room Type', 'Active', 'Faculty Role', 'Priority']
    sheet.append(headers); sheet.append(['2026-27', 'Even Semester', 'CS101', 'CS 1A', 3, 'BBD-FAC001', '1.GF001', 'LECTURE', 1, 'CLASSROOM', 'Yes', 'PRIMARY', 1])
    notes = workbook.create_sheet('Instructions'); notes.append(['Required columns']); notes.append(['Session, Semester, Course Code, Section, Weekly Periods, Employee Code, Room No.']); notes.append(['Referenced records must already exist in Sessions, Semesters, Courses, Sections, Faculty, and Rooms & Labs.'])
    stream = BytesIO(); workbook.save(stream); return stream.getvalue()
