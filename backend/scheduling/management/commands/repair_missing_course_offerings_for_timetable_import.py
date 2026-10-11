"""Repair missing CourseOfferings for an existing draft timetable import."""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from common.imports import rows_from_upload
from scheduling.models import TimetableVersion
from scheduling.services.course_offering_repair import apply_repair, plan_repair, preview_counts


class Command(BaseCommand):
    help = 'Audit or repair canonical CourseOfferings from a timetable CSV/XLSX for one DRAFT version.'

    def add_arguments(self, parser):
        parser.add_argument('file', type=Path, help='The exact timetable workbook used in import preview.')
        parser.add_argument('--version-id', required=True, help='TimetableVersion UUID; determines the semester/session.')
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--dry-run', action='store_true', help='Show the plan without changing data (default).')
        mode.add_argument('--apply', action='store_true', help='Apply only offerings/mappings with safe configurations.')

    def handle(self, *args, **options):
        path = options['file'].expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {'.csv', '.xlsx'}:
            raise CommandError('Provide an existing timetable CSV or XLSX file.')
        try:
            version = TimetableVersion.objects.select_related('timetable__semester__session').get(pk=options['version_id'])
        except (TimetableVersion.DoesNotExist, ValueError) as exc:
            raise CommandError('Timetable version was not found.') from exc
        try:
            with path.open('rb') as upload:
                rows = rows_from_upload(upload)
            plan = plan_repair(version, rows)
            before = preview_counts(version, rows)
        except (ValueError, OSError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(f'Version: v{version.version_no} {version.status}; semester: {plan["semester"].name} / {plan["semester"].session.name}')
        self.stdout.write(f'Before preview: {before}')
        self.stdout.write(f'Missing CourseOfferings found: {plan["missing_found"]}')
        self.stdout.write(f'Would create: {plan["would_create"]}')
        self.stdout.write(f'Already existing: {plan["already_existing"]}')
        self.stdout.write(f'Faculty assignments to create: {plan["faculty_assignments_to_create"]}')
        self.stdout.write(f'Existing-offering faculty mappings to create: {plan["existing_offering_mappings_to_create"]}')
        self.stdout.write(f'Offerings needing manual review: {plan["missing_found"] - plan["would_create"]}')
        self.stdout.write(f'Problems requiring review: {len(plan["problems"])}')
        for problem in plan['problems']:
            self.stdout.write(f'NEEDS_REVIEW: {problem}')
        for warning in plan['warnings']:
            self.stdout.write(f'WARNING: {warning}')
        for item in plan['plans']:
            if not item['existing']:
                config = item['configuration']
                self.stdout.write(
                    f'CREATE: {item["section"].name} / {item["course"].code}: '
                    f'{config["weekly_periods"]} periods, {config["default_class_type"]}, '
                    f'block {config["required_block_size"]}; faculty: '
                    + ', '.join(f'{faculty.employee_code} ({role})' for faculty, role in item['faculty_assignments'])
                )
        if not options['apply']:
            self.stdout.write('DRY RUN: no database changes. Rerun with --apply to create safe records.')
            return

        try:
            counts = apply_repair(version, plan)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f'Applied: {counts}')
        self.stdout.write(f'After preview: {preview_counts(version, rows)}')
