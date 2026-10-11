"""Evidence-gated CourseOffering room-type repair for the v6 import."""

from collections import defaultdict

from academics.models import CourseOffering
from common.course_offering_import import canonical_room_code
from rooms.models import Room
from rooms.services.eligibility import LAB_ROOM_TYPES, normalize_room_type_requirement
from scheduling.services.timetable_import import _rows


def source_room_types(source_rows):
    """All physical room types by section/course, including formerly valid rows."""
    rooms = defaultdict(list)
    for room in Room.objects.all():
        rooms[canonical_room_code(room.code)].append(room)
    used = defaultdict(set)
    for row in _rows(source_rows):
        key = (str(row.get('section') or '').strip().casefold(),
               str(row.get('course_code') or '').strip().casefold())
        room_code = str(row.get('room') or '').strip()
        if not room_code or room_code.casefold() in {'online', 'virtual'}:
            continue
        matches = rooms.get(canonical_room_code(room_code), [])
        used[key].add(matches[0].room_type if len(matches) == 1 else 'UNKNOWN_ROOM')
    return used


def plan_room_requirement_repair(audit_report, source_rows):
    """Classify every mismatched offering; propose only repeated lab evidence."""
    grouped = defaultdict(list)
    for item in audit_report['room_type_mismatches']:
        grouped[item['offering_id']].append(item)

    by_course_and_type = defaultdict(set)
    for items in grouped.values():
        first = items[0]
        if (first['offering_class_type'] == 'PRACTICAL'
                and first['requirement'] == 'CLASSROOM'):
            for item in items:
                by_course_and_type[(item['course_code'], item['room_type'])].add(item['section'])

    offerings = {str(pk): offering for pk, offering in
                 CourseOffering.objects.select_related('course', 'section').in_bulk(grouped.keys()).items()}
    source_types = source_room_types(source_rows)
    decisions = []
    for offering_id, items in grouped.items():
        first = items[0]
        offering = offerings.get(offering_id)
        if not offering:
            raise ValueError(f'CourseOffering {offering_id} no longer exists.')
        room_types = {item['room_type'] for item in items}
        all_source_types = source_types.get((offering.section.name.casefold(), offering.course.code.casefold()), set())
        source_rooms = sorted({item['room_code'] for item in items})
        rows = sorted({row for item in items for row in item['rows']})
        old_requirement = offering.room_type_requirement
        lab_named = 'lab' in offering.course.name.casefold() or 'practical' in offering.course.name.casefold()
        matching_practical = (offering.default_class_type == 'PRACTICAL'
                              and first['offering_class_type'] == 'PRACTICAL')
        repeated = len(by_course_and_type.get((offering.course.code, next(iter(room_types))), set())) >= 3 if len(room_types) == 1 else False
        if (old_requirement == 'CLASSROOM' and matching_practical and lab_named
                and len(room_types) == 1 and next(iter(room_types)) in LAB_ROOM_TYPES
                and next(iter(room_types)) != 'LAB' and repeated
                and all_source_types == room_types):
            classification = 'CLEAR_REQUIREMENT_ERROR'
            proposed = next(iter(room_types))
            reason = ('All physical source rows for this section/course use the same canonical lab '
                      'room type, across at least three sections; CLASSROOM contradicts that evidence.')
        elif (normalize_room_type_requirement(old_requirement) in LAB_ROOM_TYPES
              and 'CLASSROOM' in room_types):
            classification = 'GENUINE_ROOM_MISMATCH'
            proposed = None
            reason = 'The lab requirement is supported by the offering, but the source uses a classroom.'
        else:
            classification = 'NEEDS_REVIEW'
            proposed = None
            reason = 'Source and master evidence do not justify an automatic requirement change.'
        decisions.append({
            'offering_id': str(offering.pk), 'section': offering.section.name,
            'course_code': offering.course.code, 'course_name': offering.course.name,
            'old_requirement': old_requirement, 'proposed_requirement': proposed,
            'class_type': offering.default_class_type, 'source_rooms': source_rooms,
            'source_room_types': sorted(room_types), 'source_rows': rows,
            'all_source_room_types': sorted(all_source_types),
            'equivalent_offering_requirements': first['equivalent_offering_requirements'],
            'classification': classification, 'reason': reason,
        })
    decisions.sort(key=lambda item: (item['course_code'], item['section'], item['offering_id']))
    return decisions
