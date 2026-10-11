"""Read-only, row-level audit for a timetable import workbook."""

import json
from collections import Counter, defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand
from django.core.files.uploadedfile import SimpleUploadedFile

from academics.models import CourseOffering, Section
from common.course_offering_import import canonical_room_code
from common.imports import rows_from_upload
from rooms.models import Room
from rooms.services.eligibility import normalize_room_type_requirement, required_course_room_code, required_mtech_room_code, room_satisfies_requirement
from scheduling.models import TimetableVersion
from scheduling.services.timetable_import import _rows, parse


def audit(version, source_rows):
    result, _ = parse(version, source_rows)
    rows = _rows(source_rows)
    sections = {section.name.casefold(): section for section in Section.objects.filter(
        semester=version.timetable.semester).select_related('program')}
    offerings = {(str(offering.section_id), offering.course.code.casefold()): offering
                 for offering in CourseOffering.objects.filter(
                     semester=version.timetable.semester).select_related('course', 'section')}
    rooms = defaultdict(list)
    for room in Room.objects.all():
        rooms[canonical_room_code(room.code)].append(room)

    mismatches = {}
    for number, row in enumerate(rows, 2):
        section = sections.get(str(row.get('section') or '').strip().casefold())
        if not section:
            continue
        offering = offerings.get((str(section.pk), str(row.get('course_code') or '').strip().casefold()))
        room_matches = rooms.get(canonical_room_code(str(row.get('room') or '').strip()), [])
        if not offering or len(room_matches) != 1:
            continue
        room = room_matches[0]
        if required_course_room_code(offering) == room.code or required_mtech_room_code(section) == room.code:
            continue
        requirement = offering.room_type_requirement
        if room_satisfies_requirement(room, requirement):
            continue
        key = (str(offering.pk), str(room.pk))
        if key not in mismatches:
            siblings = CourseOffering.objects.filter(
                semester=version.timetable.semester, course=offering.course,
                section__program=section.program, section__year=section.year,
            ).values_list('room_type_requirement', flat=True)
            mismatches[key] = {
                'classification': 'NEEDS_REVIEW',
                'course_code': offering.course.code,
                'course_name': offering.course.name,
                'section': section.name,
                'program_code': section.program.code,
                'year': section.year,
                'offering_id': str(offering.pk),
                'offering_class_type': offering.default_class_type,
                'requirement': requirement,
                'normalized_requirement': normalize_room_type_requirement(requirement),
                'equivalent_offering_requirements': sorted(set(siblings)),
                'room_code': room.code,
                'room_type': room.room_type,
                'room_id': str(room.pk),
                'rows': [],
            }
        mismatches[key]['rows'].append(number)

    clashes = []
    def delivery_for(row):
        section = sections.get(str(row.get('section') or '').strip().casefold())
        room_code = str(row.get('room') or '').strip()
        if section and section.year == 4:
            return 'OFFLINE' if room_code and room_code.casefold() not in {'online', 'virtual'} else 'ONLINE'
        return 'ONLINE' if room_code.casefold() in {'online', 'virtual'} else 'OFFLINE'

    for error in result['errors']:
        if error.get('code') not in {'FACULTY_CLASH', 'ROOM_CLASH', 'SECTION_CLASH'}:
            continue
        number = error['row']
        source = rows[number - 2]
        other_number = error.get('conflicting_row')
        other = rows[other_number - 2] if other_number else {}
        clashes.append({
            'category': error['code'], 'row': number,
            'conflicting_row': other_number,
            'section': source.get('section'), 'course': source.get('course_code'),
            'faculty_code': source.get('employee_code'), 'room': source.get('room'),
            'day': source.get('day'), 'time_slot': source.get('time_slot'),
            'delivery_mode': delivery_for(source),
            'conflicting_section': other.get('section'),
            'conflicting_course': other.get('course_code'),
            'conflicting_faculty_code': other.get('employee_code'),
            'conflicting_room': other.get('room'),
            'conflicting_day': other.get('day'),
            'conflicting_time_slot': other.get('time_slot'),
            'conflicting_delivery_mode': delivery_for(other) if other else None,
        })

    categories = Counter(error.get('code') or error['message'] for error in result['errors'])
    invalid_by_category = defaultdict(list)
    for error in result['errors']:
        invalid_by_category[error.get('code') or error['message']].append(error)
    return {
        'version_id': str(version.pk),
        'summary': {key: result[key] for key in ('total', 'valid', 'invalid')},
        'error_categories': dict(sorted(categories.items())),
        'room_type_mismatches': sorted(mismatches.values(), key=lambda x: (x['course_code'], x['section'], x['room_code'])),
        'room_604_rows': [{'row': number, 'section': row.get('section'),
                           'course_code': row.get('course_code'), 'room': row.get('room')}
                          for number, row in enumerate(rows, 2) if str(row.get('room') or '').strip() == '604'],
        'clash_pairs': clashes,
        'invalid_by_category': dict(sorted(invalid_by_category.items())),
    }


class Command(BaseCommand):
    help = 'Audit a timetable workbook without changing timetable or master records.'

    def add_arguments(self, parser):
        parser.add_argument('--version-id', required=True)
        parser.add_argument('--workbook', required=True)
        parser.add_argument('--output', help='Optional JSON report path.')

    def handle(self, *args, **options):
        version = TimetableVersion.objects.select_related('timetable__semester').get(pk=options['version_id'])
        workbook = Path(options['workbook'])
        upload = SimpleUploadedFile(workbook.name, workbook.read_bytes())
        report = audit(version, rows_from_upload(upload))
        rendered = json.dumps(report, indent=2, default=str)
        if options['output']:
            target = Path(options['output'])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered + '\n', encoding='utf-8')
            self.stdout.write(f'Audit written to {target}')
        else:
            self.stdout.write(rendered)
