"""Create the final machine-readable and workbook review of v6 import errors."""

import json
from collections import Counter
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from openpyxl import Workbook

from common.imports import rows_from_upload
from faculty.models import Faculty
from scheduling.management.commands.audit_timetable_import import audit
from scheduling.models import TimetableVersion
from scheduling.services.room_requirement_repair import source_room_types
from scheduling.services.timetable_import import _rows


VERSION_ID = 'e5ba666e-b867-4d08-8960-7ede38c98222'


def _text(value):
    return str(value or '').strip()


def _same_assignment(left, right):
    fields = ('section', 'course_code', 'employee_code', 'day', 'time_slot', 'room')
    return all(_text(left.get(field)).casefold() == _text(right.get(field)).casefold()
               for field in fields)


def build_final_report(version, source_rows):
    base = audit(version, source_rows)
    normalized = _rows(source_rows)
    faculty_names = {faculty.employee_code.casefold(): faculty.name or faculty.initials
                     for faculty in Faculty.objects.all() if faculty.employee_code}
    all_types = source_room_types(source_rows)

    room_groups = []
    for group in base['room_type_mismatches']:
        used = sorted(all_types.get((group['section'].casefold(), group['course_code'].casefold()), set()))
        requirement = group['normalized_requirement']
        if requirement in {'LAB', 'COMPUTER_LAB', 'ELECTRICAL_LAB', 'MECHANICS_LAB',
                           'ENGINEERING_GRAPHICS_LAB', 'PHYSICS_LAB',
                           'SUSTAINABLE_CHEMICAL_SCIENCES_LAB'} and group['room_type'] == 'CLASSROOM':
            classification = 'CLEAR_WRONG_ROOM_ASSIGNMENT'
            reason = 'The canonical offering requires a lab type but the workbook assigns a classroom.'
        elif len(used) > 1:
            classification = 'MIXED_USE_NEEDS_REVIEW'
            reason = 'The same offering uses multiple canonical room types in the source workbook.'
        elif (group['requirement'] == 'CLASSROOM' and group['offering_class_type'] == 'PRACTICAL'
              and 'lab' in group['course_name'].casefold() and used == [group['room_type']]):
            classification = 'CLEAR_MASTER_REQUIREMENT_ERROR'
            reason = 'All source usage is one matching lab type for a practical lab course.'
        else:
            classification = 'MIXED_USE_NEEDS_REVIEW'
            reason = 'Canonical evidence is insufficient for an automatic master-data change.'
        room_groups.append({**group, 'source_room_types': used,
                            'classification': classification, 'reason': reason})

    faculty_pairs, room_pairs = [], []
    seen_pairs = set()
    for pair in base['clash_pairs']:
        left_number, right_number = pair['row'], pair['conflicting_row']
        if not right_number:
            continue
        key = (pair['category'], min(left_number, right_number), max(left_number, right_number))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        left, right = normalized[left_number - 2], normalized[right_number - 2]
        exact = _same_assignment(left, right)
        if exact:
            classification = 'EXACT_DUPLICATE'
        elif pair['category'] == 'FACULTY_CLASH':
            classification = ('SAME_FACULTY_ASSIGNED_TO_TWO_DIFFERENT_SECTIONS'
                              if _text(left.get('section')).casefold() != _text(right.get('section')).casefold()
                              else 'NEEDS_MANUAL_TIMETABLE_CHANGE')
        else:
            classification = ('TRUE_DOUBLE_BOOKING'
                              if pair['delivery_mode'] == pair['conflicting_delivery_mode'] == 'OFFLINE'
                              else 'IMPORT_ORDER_FALSE_POSITIVE')
        record = {
            'classification': classification,
            'excel_row_a': right_number, 'excel_row_b': left_number,
            'faculty': faculty_names.get(_text(left.get('employee_code')).casefold(), ''),
            'employee_code': _text(left.get('employee_code')),
            'room': _text(left.get('room')), 'day': _text(left.get('day')),
            'time': _text(left.get('time_slot')),
            'section_a': _text(right.get('section')), 'course_a': _text(right.get('course_code')),
            'delivery_a': pair['conflicting_delivery_mode'],
            'section_b': _text(left.get('section')), 'course_b': _text(left.get('course_code')),
            'delivery_b': pair['delivery_mode'],
            'recommended_action': ('Remove the duplicate workbook row.' if exact
                                   else 'Correct the source timetable assignment; do not weaken collision validation.'),
        }
        (faculty_pairs if pair['category'] == 'FACULTY_CLASH' else room_pairs).append(record)

    fixed_errors = base['invalid_by_category'].get('FIXED_ROOM_RESERVED', [])
    fixed_rows = []
    for error in fixed_errors:
        row = normalized[error['row'] - 2]
        fixed_rows.append({
            'excel_row': error['row'], 'section': _text(row.get('section')),
            'course': _text(row.get('course_code')), 'day': _text(row.get('day')),
            'time': _text(row.get('time_slot')), 'room': _text(row.get('room')),
            'classification': 'MANUAL_SOURCE_TIMETABLE_CORRECTION_REQUIRED',
            'reason': error['message'],
        })

    auto_fixable = [group for group in room_groups if group['classification'] == 'CLEAR_MASTER_REQUIREMENT_ERROR']
    needs_review = [group for group in room_groups if group['classification'] == 'MIXED_USE_NEEDS_REVIEW']
    manual = ([group for group in room_groups if group['classification'] == 'CLEAR_WRONG_ROOM_ASSIGNMENT']
              + fixed_rows)
    classifications = Counter(pair['classification'] for pair in faculty_pairs + room_pairs)
    return {
        'summary': base['summary'], 'error_categories': base['error_categories'],
        'snapshot_note': 'Generated from the current local workbook and database; counts are not overridden by an earlier browser snapshot.',
        'room_type_groups': room_groups,
        'faculty_clash_pairs': faculty_pairs,
        'room_clash_pairs': room_pairs,
        'fixed_room_rows': fixed_rows,
        'auto_fixable': auto_fixable,
        'manual_source_corrections': manual,
        'needs_review': needs_review,
        'pair_classification_counts': dict(sorted(classifications.items())),
        'import_order_false_positives': sum(pair['classification'] == 'IMPORT_ORDER_FALSE_POSITIVE'
                                            for pair in faculty_pairs + room_pairs),
    }


def write_review_workbook(report, target):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Invalid Rows Review'
    headers = ['Excel Row', 'Category', 'Section', 'Course', 'Faculty', 'Employee Code',
               'Day', 'Time', 'Room', 'Delivery', 'Conflicts With Row',
               'Recommended Action', 'Reason']
    sheet.append(headers)
    for category, pairs in (('FACULTY_CLASH', report['faculty_clash_pairs']),
                            ('ROOM_CLASH', report['room_clash_pairs'])):
        for pair in pairs:
            sheet.append([pair['excel_row_b'], category, pair['section_b'], pair['course_b'],
                          pair['faculty'], pair['employee_code'], pair['day'], pair['time'],
                          pair['room'], pair['delivery_b'], pair['excel_row_a'],
                          pair['recommended_action'], pair['classification']])
    for group in report['room_type_groups']:
        for row in group['rows']:
            sheet.append([row, 'ROOM_TYPE_MISMATCH', group['section'], group['course_code'],
                          '', '', '', '', group['room_code'], 'OFFLINE', '',
                          ('Repair CourseOffering requirement after approval.' if group['classification'] == 'CLEAR_MASTER_REQUIREMENT_ERROR'
                           else 'Correct/review source timetable; no automatic master change.'),
                          f"{group['classification']}: {group['reason']}"])
    for row in report['fixed_room_rows']:
        sheet.append([row['excel_row'], 'FIXED_ROOM_RESERVED', row['section'], row['course'],
                      '', '', row['day'], row['time'], row['room'], 'OFFLINE', '',
                      'Correct the source timetable room.', row['reason']])
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    widths = [12, 24, 18, 16, 28, 18, 14, 18, 14, 14, 20, 42, 70]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[chr(64 + index)].width = width
    workbook.save(target)


class Command(BaseCommand):
    help = 'Create final v6 invalid-row JSON and XLSX reports without modifying data.'

    def add_arguments(self, parser):
        parser.add_argument('--workbook', required=True)
        parser.add_argument('--json-output', required=True)
        parser.add_argument('--xlsx-output', required=True)

    def handle(self, *args, **options):
        version = TimetableVersion.objects.select_related('timetable__semester').get(pk=VERSION_ID)
        workbook_path = Path(options['workbook'])
        if not workbook_path.is_file():
            raise CommandError(f'Workbook not found: {workbook_path}')
        source_rows = rows_from_upload(SimpleUploadedFile(workbook_path.name, workbook_path.read_bytes()))
        report = build_final_report(version, source_rows)
        json_target, xlsx_target = Path(options['json_output']), Path(options['xlsx_output'])
        json_target.parent.mkdir(parents=True, exist_ok=True)
        xlsx_target.parent.mkdir(parents=True, exist_ok=True)
        json_target.write_text(json.dumps(report, indent=2, default=str) + '\n', encoding='utf-8')
        write_review_workbook(report, xlsx_target)
        self.stdout.write(f'Final audit written to {json_target} and {xlsx_target}')
