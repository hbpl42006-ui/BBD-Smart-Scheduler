MTECH_RESERVED_ROOM_CODES = {1: '516', 2: '604'}
FIXED_COURSE_ROOM_CODES = {'RBS5151': '105'}
ROOM_105_RESERVED_COURSE_NAME = 'Quantum Physics and Advanced Functional Materials Lab'
LAB_ROOM_TYPES = frozenset({
    'LAB', 'COMPUTER_LAB', 'ELECTRICAL_LAB', 'MECHANICS_LAB',
    'ENGINEERING_GRAPHICS_LAB', 'PHYSICS_LAB',
    'SUSTAINABLE_CHEMICAL_SCIENCES_LAB',
    'QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB',
})


def normalize_room_type_requirement(value):
    """Normalize stored enum values and their display labels for comparisons.

    PRACTICAL is an activity type, not a Room.room_type choice, so it does not
    impose a physical-room-type restriction. Unknown values remain a distinct
    token and therefore continue to match no room type.
    """
    text = str(value or '').strip()
    if not text:
        return ''
    token = ''.join(character for character in text.upper() if character.isalnum())
    if token == 'PRACTICAL':
        return ''
    from rooms.models import Room
    for code, label in Room.RoomType.choices:
        if token in {
            ''.join(character for character in code.upper() if character.isalnum()),
            ''.join(character for character in label.upper() if character.isalnum()),
        }:
            return code
    return text.upper().replace(' ', '_').replace('-', '_')


def room_satisfies_requirement(room, requirement):
    """Match a physical room against a specific type or generic LAB category."""
    normalized = normalize_room_type_requirement(requirement)
    if not normalized:
        return True
    if normalized == 'LAB':
        return room.room_type in LAB_ROOM_TYPES
    return room.room_type == normalized


def room_eligibility_exception(room, section, course=None, required_room_code=None):
    """Return the exact active scoped allow exception, if one exists."""
    if not room or not section or not section.program_id:
        return None
    if required_room_code and room.code != required_room_code:
        return None
    from rooms.models import RoomEligibilityException
    from django.db.models import Q
    queryset = RoomEligibilityException.objects.filter(
        room_id=room.pk,
        program_id=section.program_id,
        year=section.year,
        active=True,
        allow=True,
    )
    if course is not None:
        queryset = queryset.filter(Q(course__isnull=True) | Q(course=course))
    else:
        queryset = queryset.filter(course__isnull=True)
    return queryset.select_related('course', 'program', 'room').order_by('-course_id').first()


def room_pool_signature(room, exceptions=()):
    """Hard-policy signature: rooms may pool only when policy-equivalent."""
    exception_rows = tuple(sorted(
        (str(row.program_id), row.year, str(row.course_id) if row.course_id else None,
         bool(row.allow), bool(row.active))
        for row in exceptions if row.room_id == room.pk
    ))
    fixed_room = room.code if room.code in {'105', '516', '604'} else None
    return (
        room.room_type, room.capacity, bool(room.active), room.allowed_year,
        bool(room.exclusive_reservation), str(room.reserved_program_id) if room.reserved_program_id else None,
        room.reserved_year, fixed_room, exception_rows,
    )


def room_eligibility_error(
    room, offering, section, *, required_room_type=None, activity_type=None,
    blocked_slots=None, occupied_slots=None, weekday=None, slot_ids=(), exceptions=None,
):
    """Canonical hard eligibility decision used by generation and validation.

    Returns None when eligible, otherwise a structured reason. Slot sets use
    (room_id, weekday, slot_id) tuples and cover the complete block.
    """
    if room is None:
        return {'code': 'ROOM_REQUIRED', 'message': 'A physical room is required.'}
    course = getattr(offering, 'course', offering)
    if not room.active:
        return {'code': 'ROOM_INACTIVE', 'message': f'Room {room.code} is inactive.'}
    if section and room.capacity < section.student_strength:
        return {'code': 'ROOM_CAPACITY_EXCEEDED', 'message': f'Room {room.code} capacity is insufficient.'}

    if room.code == '105':
        course_code = str(getattr(course, 'code', '')).strip().upper()
        program_code = ''.join(character for character in str(getattr(getattr(section, 'program', None), 'code', '')).casefold() if character.isalnum())
        if course_code != 'RBS5151' or not section or section.year != 1 or program_code != 'btechcse':
            return {
                'code': 'FIXED_ROOM_RESERVED',
                'message': f"Room 105 is reserved for the Year-1 course '{ROOM_105_RESERVED_COURSE_NAME}'.",
            }

    mtech_code = required_mtech_room_code(section) if section else None
    activity = activity_type or getattr(offering, 'default_class_type', None)
    fixed_code = required_course_room_code(course) if activity in (None, 'PRACTICAL') else None
    if mtech_code and room.code != mtech_code:
        return {'code': 'MTECH_RESERVED_ROOM_REQUIRED', 'message': f'{section.program.name} Year {section.year} must use Room {mtech_code}.', 'required_room_code': mtech_code}
    if fixed_code and room.code != fixed_code:
        return {'code': 'REQUIRED_ROOM_MISMATCH', 'message': f'{course.code} must use Room {fixed_code}.', 'required_room_code': fixed_code}
    if room.code in {'105', '516', '604'}:
        expected = fixed_code if room.code == '105' else mtech_code
        if expected != room.code:
            return {'code': 'FIXED_ROOM_RESERVED', 'message': f'Room {room.code} is reserved for its configured fixed-room rule.'}
    if mtech_code:
        if room.reserved_year != section.year or room.reserved_program_id != section.program_id or not room.exclusive_reservation:
            return {'code': 'MTECH_RESERVED_ROOM_UNAVAILABLE', 'message': f'Reserved Room {mtech_code} is not configured for this program and year.'}
        if room.allowed_year is not None and room.allowed_year != section.year:
            return {'code': 'ROOM_YEAR_RESTRICTION', 'message': f'Room {room.code} is restricted to Year {room.allowed_year}.'}

    if exceptions is None:
        from rooms.models import RoomEligibilityException
        from django.db.models import Q
        rows = list(RoomEligibilityException.objects.filter(
            room_id=room.pk, program_id=section.program_id, year=section.year, active=True
        ).filter(Q(course__isnull=True) | Q(course=course)).order_by('-course_id')) if section else []
    else:
        rows = [row for row in exceptions if row.room_id == room.pk and section
                and row.program_id == section.program_id and row.year == section.year
                and (row.course_id is None or row.course_id == course.pk)]
        rows.sort(key=lambda row: row.course_id is not None, reverse=True)
    exception = rows[0] if rows else None
    if exception and not exception.allow:
        return {'code': 'ROOM_ELIGIBILITY_EXCEPTION_DENIED', 'message': f'Room {room.code} is explicitly denied for this section/course.'}
    allow_scope_exception = bool(exception and exception.allow)
    if section and room.allowed_year is not None and room.allowed_year != section.year and not allow_scope_exception:
        return {'code': 'ROOM_YEAR_RESTRICTION', 'message': f'Room {room.code} is restricted to Year {room.allowed_year}.', 'allowed_year': room.allowed_year}
    if section and room.exclusive_reservation and (
        room.reserved_program_id != section.program_id or room.reserved_year != section.year
    ) and not allow_scope_exception:
        reserved_program = room.reserved_program.name if room.reserved_program_id else 'its configured program'
        return {'code': 'ROOM_PROGRAM_YEAR_RESTRICTION', 'message': f'Room {room.code} is exclusively reserved for {reserved_program} Year {room.reserved_year}.', 'reserved_program': room.reserved_program.name if room.reserved_program_id else None, 'reserved_year': room.reserved_year}

    # Fixed-course policy supersedes a stale generic type field, but still
    # verifies that Room 105 has its mandated specialized type.
    if fixed_code:
        if room.room_type != 'QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB':
            return {'code': 'REQUIRED_ROOM_TYPE_MISMATCH', 'message': 'Room 105 must have the Quantum Physics and Advanced Functional Materials Lab room type.'}
    elif not mtech_code:
        requirement = required_room_type
        if requirement is None:
            requirement = getattr(offering, 'room_type_requirement', '') if offering else ''
        if not room_satisfies_requirement(room, requirement):
            return {'code': 'ROOM_TYPE_MISMATCH', 'message': 'Room type does not match course requirement.', 'required_room_type': normalize_room_type_requirement(requirement), 'actual_room_type': room.room_type}

    if weekday is not None:
        key_prefix = (str(room.pk), weekday)
        blocked_slots = blocked_slots or set()
        occupied_slots = occupied_slots or set()
        for slot_id in slot_ids:
            key = key_prefix + (str(slot_id),)
            if key in blocked_slots:
                return {'code': 'ROOM_UNAVAILABLE', 'message': f'Room {room.code} is blocked or under maintenance for part of the requested block.'}
            if key in occupied_slots:
                return {'code': 'ROOM_CLASH', 'message': f'Room {room.code} is already occupied for part of the requested block.'}
    return None


def room_section_error(room, section, course=None, required_room_code=None):
    """Backwards-compatible room policy check backed by canonical eligibility."""
    offering = course
    required = required_room_code or required_course_room_code(course)
    if required and getattr(room, 'code', None) != required:
        return {'code': 'REQUIRED_ROOM_MISMATCH', 'message': f'This class must use Room {required}.', 'required_room_code': required}
    return room_eligibility_error(room, offering, section)


def required_course_room_code(course_offering):
    """Return a course-specific physical room requirement, if any."""
    course = getattr(course_offering, 'course', course_offering)
    return FIXED_COURSE_ROOM_CODES.get(getattr(course, 'code', '').strip().upper())


def is_mtech_program(program):
    if not program:
        return False
    code = ''.join(character for character in program.code.casefold() if character.isalnum())
    name = ''.join(character for character in program.name.casefold() if character.isalnum())
    return code.startswith('mtech') or name.startswith('mtech') or 'masteroftechnology' in name


def required_mtech_room_code(section):
    if not is_mtech_program(getattr(section, 'program', None)):
        return None
    return MTECH_RESERVED_ROOM_CODES.get(section.year)


