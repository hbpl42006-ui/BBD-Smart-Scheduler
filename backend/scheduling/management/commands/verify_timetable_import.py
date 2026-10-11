"""Read-only verification of entries belonging to one timetable version."""

from collections import Counter, defaultdict
from itertools import combinations

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

from common.models import TimeSlot
from rooms.models import RoomEligibilityException
from rooms.services.eligibility import room_eligibility_error
from scheduling.models import ScheduleEntry, TimetableVersion


FIXED_ROOM_CODES = {
    'FIXED_ROOM_RESERVED', 'REQUIRED_ROOM_MISMATCH',
    'REQUIRED_ROOM_TYPE_MISMATCH', 'MTECH_RESERVED_ROOM_REQUIRED',
    'MTECH_RESERVED_ROOM_UNAVAILABLE',
}


def _pair_count(index):
    pairs = set()
    for entry_ids in index.values():
        for left, right in combinations(sorted(set(entry_ids)), 2):
            pairs.add((left, right))
    return len(pairs)


def verify(version, expected_rows=1692):
    entries = list(ScheduleEntry.objects.filter(version=version).select_related(
        'section', 'section__program', 'course_offering', 'course_offering__course',
        'room', 'start_slot__template',
    ).prefetch_related('faculty_assignments__faculty'))
    slots_by_template = defaultdict(list)
    for slot in TimeSlot.objects.order_by('template_id', 'order'):
        slots_by_template[slot.template_id].append(slot)

    windows = {}
    invalid_windows = 0
    for entry in entries:
        sequence = slots_by_template[entry.start_slot.template_id]
        window = [slot for slot in sequence
                  if entry.start_slot.order <= slot.order < entry.start_slot.order + entry.block_length]
        windows[entry.pk] = window
        if len(window) != entry.block_length or any(slot.is_break for slot in window):
            invalid_windows += 1

    section_index = defaultdict(list)
    room_index = defaultdict(list)
    faculty_index = defaultdict(list)
    faculty_ids = set()
    for entry in entries:
        assignments = list(entry.faculty_assignments.all())
        for slot in windows[entry.pk]:
            section_index[(entry.section_id, entry.weekday, slot.pk)].append(str(entry.pk))
            if entry.room_id:
                room_index[(entry.room_id, entry.weekday, slot.pk)].append(str(entry.pk))
            for assignment in assignments:
                faculty_ids.add(assignment.faculty_id)
                faculty_index[(assignment.faculty_id, entry.weekday, slot.pk)].append(str(entry.pk))

    duplicate_groups = ScheduleEntry.objects.filter(version=version).values(
        'section_id', 'course_offering_id', 'weekday', 'start_slot_id', 'block_length',
        'room_id', 'entry_type', 'delivery_mode',
    ).annotate(total=Count('id')).filter(total__gt=1)
    duplicate_allocations = sum(row['total'] - 1 for row in duplicate_groups)

    missing_relationships = 0
    for entry in entries:
        if not entry.faculty_assignments.exists():
            missing_relationships += 1
        if entry.delivery_mode == 'OFFLINE' and not entry.room_id:
            missing_relationships += 1
        if entry.delivery_mode == 'ONLINE' and entry.room_id:
            missing_relationships += 1
        if entry.course_offering.section_id != entry.section_id:
            missing_relationships += 1
        if entry.section.semester_id != version.timetable.semester_id:
            missing_relationships += 1
        if entry.course_offering.semester_id != version.timetable.semester_id:
            missing_relationships += 1

    exceptions = list(RoomEligibilityException.objects.filter(
        room_id__in={entry.room_id for entry in entries if entry.room_id}, active=True,
    ).select_related('course', 'program'))
    room_type_violations = 0
    fixed_room_violations = 0
    for entry in entries:
        if entry.delivery_mode != 'OFFLINE' or not entry.room_id:
            continue
        error = room_eligibility_error(
            entry.room, entry.course_offering, entry.section,
            activity_type=entry.entry_type,
            weekday=entry.weekday,
            slot_ids=[str(slot.pk) for slot in windows[entry.pk]],
            exceptions=exceptions,
        )
        if not error:
            continue
        if error['code'] == 'ROOM_TYPE_MISMATCH':
            room_type_violations += 1
        if error['code'] in FIXED_ROOM_CODES:
            fixed_room_violations += 1

    practical_periods = defaultdict(set)
    practical_entries = defaultdict(list)
    for entry in entries:
        if entry.entry_type != 'PRACTICAL' or entry.course_offering.required_block_size <= 1:
            continue
        faculty_key = tuple(sorted(str(item.faculty_id) for item in entry.faculty_assignments.all()))
        key = (entry.course_offering_id, entry.weekday, entry.room_id, faculty_key)
        practical_entries[key].append(entry)
        practical_periods[key].update(slot.order for slot in windows[entry.pk])
    practical_block_violations = 0
    practical_block_details = []
    for key, orders in practical_periods.items():
        offering = practical_entries[key][0].course_offering
        runs = []
        for order in sorted(orders):
            if not runs or order != runs[-1][-1] + 1:
                runs.append([order])
            else:
                runs[-1].append(order)
        if not offering.allow_remainder_period:
            invalid_runs = [run for run in runs if len(run) % offering.required_block_size]
            practical_block_violations += len(invalid_runs)
            for run in invalid_runs:
                sample = practical_entries[key][0]
                practical_block_details.append({
                    'section': sample.section.name,
                    'course': offering.course.code,
                    'weekday': sample.weekday,
                    'room': sample.room.code if sample.room_id else 'ONLINE',
                    'periods': len(run),
                    'required_block_size': offering.required_block_size,
                })

    report = {
        'expected_rows': expected_rows,
        'database_entries': len(entries),
        'sections': len({entry.section_id for entry in entries}),
        'faculty': len(faculty_ids),
        'rooms': len({entry.room_id for entry in entries if entry.room_id}),
        'course_offerings': len({entry.course_offering_id for entry in entries}),
        'missing_relationships': missing_relationships,
        'duplicate_allocations': duplicate_allocations,
        'faculty_clashes': _pair_count(faculty_index),
        'room_clashes': _pair_count(room_index),
        'section_clashes': _pair_count(section_index),
        'room_type_violations': room_type_violations,
        'fixed_room_violations': fixed_room_violations,
        'practical_block_violations': practical_block_violations,
        'practical_block_details': practical_block_details,
        'invalid_slot_windows': invalid_windows,
    }
    report['passed'] = (
        report['database_entries'] == expected_rows
        and not any(report[key] for key in (
            'missing_relationships', 'duplicate_allocations', 'faculty_clashes',
            'room_clashes', 'section_clashes', 'room_type_violations',
            'fixed_room_violations', 'practical_block_violations',
            'invalid_slot_windows',
        ))
    )
    return report


class Command(BaseCommand):
    help = 'Verify one timetable version without changing database records.'

    def add_arguments(self, parser):
        parser.add_argument('--version-id', required=True)
        parser.add_argument('--expected-rows', type=int, default=1692)
        parser.add_argument('--verbose-details', action='store_true')

    def handle(self, *args, **options):
        try:
            version = TimetableVersion.objects.select_related(
                'timetable__institution', 'timetable__academic_session',
                'timetable__semester',
            ).get(pk=options['version_id'])
        except (TimetableVersion.DoesNotExist, ValueError):
            raise CommandError(f'Timetable version {options["version_id"]} was not found.')
        report = verify(version, options['expected_rows'])
        status = 'VERIFIED' if report['passed'] else 'INCOMPLETE'
        result = 'PASS' if report['passed'] else 'FAIL'
        lines = [
            'BBD SMART SCHEDULER',
            'PRODUCTION TIMETABLE VERIFICATION',
            '',
            'Backend: Django',
            f'Target Version: {version.pk}',
            f'Version Status: {version.status}',
            f'Session: {version.timetable.academic_session.name}',
            f'Semester: {version.timetable.semester.name}',
            '',
            f'Expected Excel Rows: {report["expected_rows"]}',
            'Preview Valid Rows: 1692 (reported separately; not inferred from database)',
            'Preview Invalid Rows: 0 (reported separately; not inferred from database)',
            '',
            f'Database Entries: {report["database_entries"]}',
            f'Sections: {report["sections"]}',
            f'Faculty: {report["faculty"]}',
            f'Rooms: {report["rooms"]}',
            f'Course Offerings: {report["course_offerings"]}',
            f'Missing Relationships: {report["missing_relationships"]}',
            f'Duplicate Allocations: {report["duplicate_allocations"]}',
            '',
            f'Faculty Clashes: {report["faculty_clashes"]}',
            f'Room Clashes: {report["room_clashes"]}',
            f'Section Clashes: {report["section_clashes"]}',
            f'Room Type Violations: {report["room_type_violations"]}',
            f'Fixed Room Violations: {report["fixed_room_violations"]}',
            f'Practical Block Violations: {report["practical_block_violations"]}',
            f'Invalid Slot Windows: {report["invalid_slot_windows"]}',
            '',
            f'Import Status: {status}',
            f'Final Result: {result}',
        ]
        self.stdout.write('\n'.join(lines))
        if options['verbose_details'] and report['practical_block_details']:
            self.stdout.write('\nPractical Block Violation Details:')
            for item in report['practical_block_details']:
                self.stdout.write(
                    f'- {item["section"]} / {item["course"]} / weekday {item["weekday"]} '
                    f'/ {item["room"]}: {item["periods"]} contiguous period(s); '
                    f'required block {item["required_block_size"]}'
                )
