from io import BytesIO

from django.db import transaction
from openpyxl import Workbook

from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from faculty.models import CourseOfferingFaculty, Faculty
from rooms.models import Room

ONLINE_ROOM_VALUES = {'online', 'virtual'}
EMPTY_FACULTY_VALUES = {'', 'nf', 'n/a', 'na', 'not assigned', 'unassigned', 'none', 'null'}

ALIASES = {
    'session': 'session', 'academic_session': 'session', 'session_name': 'session',
    'semester': 'semester', 'semester_name': 'semester',
    'course_code': 'course_code', 'course': 'course_code',
    'section': 'section', 'section_name': 'section',
    'weekly_periods': 'weekly_periods', 'periods': 'weekly_periods', 'periods_per_week': 'weekly_periods',
    'employee_code': 'employee_code', 'faculty_code': 'employee_code',
    'faculty_name': 'faculty_name', 'faculty': 'faculty_name',
    'allow_remainder_period': 'allow_remainder_period', 'allow_remainder': 'allow_remainder_period',
    'room_no': 'room', 'room_number': 'room', 'room': 'room', 'room_code': 'room', 'preferred_room': 'room', 'preferred_room_no': 'room', 'preferred_room_code': 'room',
    'class_type': 'class_type', 'activity_type': 'class_type', 'activity': 'class_type', 'default_class_type': 'class_type', 'block_size': 'block_size', 'block_size_periods': 'block_size', 'required_block_size': 'block_size',
    'room_type': 'room_type', 'room_type_requirement': 'room_type', 'active': 'active', 'is_active': 'active',
    'faculty_role': 'role', 'role': 'role', 'priority': 'priority', 'faculty_priority': 'priority',
}
REQUIRED = {'session', 'semester', 'course_code', 'section', 'weekly_periods', 'room'}
CLASS_TYPES = {value.lower(): value for value, _ in CourseOffering.ClassType.choices}


def _key(value):
    return str(value or '').strip().lower().replace('-', '_').replace(' ', '_').replace('.', '')


def _normalize(rows):
    return [{ALIASES.get(_key(key), _key(key)): value for key, value in row.items()} for row in rows]


def _text(value):
    if value is None:
        return ''
    text = str(value).strip()
    return '' if text.lower() in {'nan', 'none', 'null'} else text


def _faculty_key(value):
    return ''.join(character for character in _text(value).casefold() if character.isalnum())


def canonical_room_code(value):
    """Normalize room identifiers without inventing aliases for most rooms."""
    code = ''.join(character for character in _text(value).upper() if character.isalnum())
    for prefix in ('LGF', 'UGF'):
        if code.startswith(prefix) and code[len(prefix):].isdigit():
            return prefix + code[len(prefix):].zfill(3)
    return code


def _room_key(value):
    # Kept as a private alias for diagnostics and existing callers.
    return canonical_room_code(value)


def _resolve_room(room_code, rooms_by_key):
    """Resolve only when a normalized code identifies exactly one Room."""
    room_code = _text(room_code)
    if room_code.casefold() in ONLINE_ROOM_VALUES:
        return None
    matches = rooms_by_key.get(canonical_room_code(room_code), [])
    if len(matches) > 1:
        candidates = ', '.join(sorted(room.code for room in matches))
        raise ValueError(f'Room "{room_code}" is ambiguous; canonical match found multiple rooms: {candidates}.')
    return matches[0] if matches else None


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


def _resolve(row, rooms_by_key=None):
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
    sections = Section.objects.filter(semester=semester, program__department__institution=session.institution, name__iexact=section_name)
    if not sections.exists():
        section_key = _faculty_key(section_name)
        sections = [section for section in Section.objects.filter(semester=semester, program__department__institution=session.institution).select_related('program') if _faculty_key(section.name) == section_key]
    else:
        sections = list(sections.select_related('program'))
    if len(sections) == 0:
        raise ValueError(f'Section "{section_name}" not found.')
    if len(sections) > 1:
        raise ValueError(f'Section "{section_name}" is ambiguous.')
    section = sections[0]
    employee = _text(row.get('employee_code'))
    faculty = None if employee.casefold() in EMPTY_FACULTY_VALUES else Faculty.objects.filter(employee_code__iexact=employee).first()
    if employee.casefold() not in EMPTY_FACULTY_VALUES and not faculty:
        faculty = next((item for item in Faculty.objects.all() if _faculty_key(item.employee_code) == _faculty_key(employee)), None)
    if employee.casefold() not in EMPTY_FACULTY_VALUES and not faculty:
        raise ValueError(f'Faculty employee code "{employee}" not found.')
    room_code = _text(row.get('room'))
    online = room_code.casefold() in ONLINE_ROOM_VALUES
    room = None if online else _resolve_room(room_code, rooms_by_key or {})
    from rooms.services.eligibility import FIXED_COURSE_ROOM_CODES
    required_room_code = FIXED_COURSE_ROOM_CODES.get(course.code.strip().upper())
    if required_room_code and room_code and not online and room_code != required_room_code:
        raise ValueError('Quantum Physics and Advanced Functional Materials Lab must use Room 105.')
    if required_room_code and online:
        raise ValueError('Quantum Physics and Advanced Functional Materials Lab must use Room 105.')
    if room_code and not online and not room:
        raise ValueError(f'Room "{room_code}" not found. Source value: "{room_code}"; normalized value: "{_room_key(room_code)}"; exact match: none; formatting-equivalent match: none; final resolution: unresolved.')
    if not _text(row.get('weekly_periods')):
        raise ValueError('Weekly Periods is required.')
    values = {
        'weekly_periods': _integer(row.get('weekly_periods'), 'Weekly Periods'),
        'default_class_type': CLASS_TYPES.get(_text(row.get('class_type')).lower(), 'LECTURE'),
        'required_block_size': _integer(row.get('block_size') or 1, 'Block Size'),
        'allow_remainder_period': _boolean(row.get('allow_remainder_period')) if _text(row.get('allow_remainder_period')) else False,
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
    result = {'total': len(normalized), 'valid': 0, 'invalid': 0, 'warnings': 0, 'warning_details': [], 'rows': [], 'errors': []}
    if not normalized or REQUIRED - set(normalized[0]):
        missing = sorted(REQUIRED - set(normalized[0] if normalized else []))
        result['invalid'] = 1
        result['errors'] = [{'row': 1, 'message': 'Missing required columns: ' + ', '.join(missing)}]
        return result, []
    prepared = []
    groups = {}
    from rooms.services.eligibility import required_mtech_room_code
    room_index = {}
    for room_record in Room.objects.all():
        room_index.setdefault(canonical_room_code(room_record.code), []).append(room_record)
    for number, row in enumerate(normalized, 2):
        try:
            session, semester, course, section, faculty, room, values = _resolve(row, room_index)
            identity = (semester.pk, section.pk, course.pk, values['default_class_type'])
            group = groups.setdefault(identity, {'session':session,'semester':semester,'course':course,'section':section,'rows':[],'faculties':{},'rooms':set(),'online_only':True,'types':set(),'values':[]})
            group['rows'].append(number); group['values'].append(values)
            group['types'].add(values['default_class_type'])
            if room: group['rooms'].add(room)
            if _text(row.get('room')).casefold() not in ONLINE_ROOM_VALUES: group['online_only'] = False
            if faculty: group['faculties'][faculty.pk] = (faculty, values['role'], values['priority'])
            else: result['warnings'] += 1; result['warning_details'].append({'row': number, 'message': 'Faculty is not assigned for this course offering; it will be imported without a faculty.'})
        except Exception as exc:
            result['invalid'] += 1; result['errors'].append({'row': number, 'message': str(exc)})
    for identity, group in groups.items():
        values = group['values'][0].copy(); values['weekly_periods'] = sum(item['weekly_periods'] for item in group['values'])
        values['allow_remainder_period'] = False
        values['activity_type'] = values['default_class_type']
        values['block_size'] = values['required_block_size']
        values['faculty_assignments'] = list(group['faculties'].values())
        values['employee_codes'] = [faculty.employee_code for faculty, _role, _priority in group['faculties'].values()]
        values['preferred_room'] = None
        values['online_only'] = group['online_only']
        values['source_rows'] = list(group['rows'])
        values['warnings'] = [warning for warning in result['warning_details'] if warning['row'] in group['rows']]
        if values['default_class_type'] == 'PRACTICAL':
            values['required_block_size'] = max(item['required_block_size'] for item in group['values'])
            values['allow_remainder_period'] = any(item['allow_remainder_period'] for item in group['values']) or values['weekly_periods'] % values['required_block_size'] != 0
        fixed_code = '105' if group['course'].code.upper() == 'RBS5151' and values['default_class_type'] == 'PRACTICAL' else None
        preferred = next(iter(group['rooms'])) if len(group['rooms']) == 1 else None
        if fixed_code: preferred = Room.objects.filter(code=fixed_code).first()
        elif required_mtech_room_code(group['section']):
            preferred = Room.objects.filter(code=required_mtech_room_code(group['section'])).first()
        if fixed_code and preferred is None:
            result['invalid'] += 1
            result['errors'].append({'row': group['rows'][0], 'code':'REQUIRED_ROOM_UNAVAILABLE','message':'Room 105 is required for Quantum Physics and Advanced Functional Materials Lab.'})
            continue
        if fixed_code and preferred and not preferred.active:
            result['invalid'] += 1
            result['errors'].append({'row': group['rows'][0], 'code':'REQUIRED_ROOM_UNAVAILABLE','message':'Room 105 is required for Quantum Physics and Advanced Functional Materials Lab and is inactive.'})
            continue
        values['preferred_room'] = preferred
        offering = CourseOffering.objects.filter(semester=group['semester'], section=group['section'], course=group['course'], default_class_type=values['default_class_type']).first()
        action = 'UPDATE OFFERING' if offering else 'CREATE OFFERING'
        # Preserve the long-standing positional import contract while storing
        # the complete normalized offering used by both preview and commit.
        primary_faculty = next(iter(group['faculties'].values()), (None, None, None))[0]
        prepared.append((identity, group['session'], group['semester'], group['course'], group['section'], primary_faculty, preferred, values, group))
        faculty_labels = [f'{faculty.employee_code} — {faculty.name or faculty.initials or "Faculty"}' for faculty, _role, _priority in group['faculties'].values()]
        result['rows'].append({'row':group['rows'][0],'status':'VALID','source_rows':len(group['rows']),'session':group['session'].name,'semester':group['semester'].name,'course':group['course'].code,'section':group['section'].name,'activity_type':values['default_class_type'],'weekly_periods':values['weekly_periods'],'employee_code':', '.join(faculty.employee_code for faculty, _role, _priority in group['faculties'].values()),'faculty':', '.join(faculty_labels) if faculty_labels else 'Not assigned','room':preferred.code if preferred else ('ONLINE' if group['online_only'] else None),'preferred_room':preferred.code if preferred else None,'multiple_source_rooms':sorted(room.code for room in group['rooms']) if len(group['rooms']) > 1 else [],'delivery_mode':'ONLINE' if group['online_only'] else 'OFFLINE','action':action})
    result['valid'] = len(prepared); result['normalized_groups'] = len(groups)
    return result, prepared


@transaction.atomic
def commit(prepared):
    counts = {'offerings_created': 0, 'offerings_updated': 0, 'faculty_assignments_created': 0, 'faculty_assignments_updated': 0, 'skipped': 0, 'failed': 0, 'warnings': 0, 'errors': []}
    for identity, session, semester, course, section, _primary_faculty, room, values, group in prepared:
        offering = CourseOffering.objects.filter(semester=semester, section=section, course=course, default_class_type=values['default_class_type']).first()
        created = offering is None
        if created:
            offering = CourseOffering.objects.create(semester=semester, section=section, course=course, **{key: values[key] for key in ('weekly_periods', 'default_class_type', 'required_block_size', 'allow_remainder_period', 'room_type_requirement', 'active')})
        if created: counts['offerings_created'] += 1
        else:
            for key in ('weekly_periods', 'default_class_type', 'required_block_size', 'allow_remainder_period', 'room_type_requirement', 'active'):
                setattr(offering, key, values[key])
        offering.preferred_room = room
        offering.save()
        counts['offerings_updated'] += 1
        if created:
            offering.preferred_room = room; offering.save(update_fields=['preferred_room'])
        for faculty, role, priority in group['faculties'].values():
            mapping, mapping_created = CourseOfferingFaculty.objects.get_or_create(course_offering=offering, faculty=faculty, defaults={'role': role, 'priority': priority})
            if mapping_created: counts['faculty_assignments_created'] += 1
            else:
                mapping.role = role; mapping.priority = priority; mapping.save(update_fields=['role', 'priority', 'updated_at']); counts['faculty_assignments_updated'] += 1
    return counts


def template():
    workbook = Workbook(); sheet = workbook.active; sheet.title = 'Course Offerings'
    headers = ['Session', 'Semester', 'Course Code', 'Section', 'Weekly Periods', 'Employee Code', 'Room No.', 'Class Type', 'Block Size', 'Room Type', 'Active', 'Faculty Role', 'Priority']
    sheet.append(headers); sheet.append(['2026-27', 'Even Semester', 'CS101', 'CS 1A', 3, 'BBD-FAC001', '1.GF001', 'LECTURE', 1, 'CLASSROOM', 'Yes', 'PRIMARY', 1])
    notes = workbook.create_sheet('Instructions'); notes.append(['Required columns']); notes.append(['Session, Semester, Course Code, Section, Weekly Periods, Employee Code, Room No.']); notes.append(['Referenced records must already exist in Sessions, Semesters, Courses, Sections, Faculty, and Rooms & Labs.'])
    stream = BytesIO(); workbook.save(stream); return stream.getvalue()
