"""Dry-run or safely apply evidence-backed v6 CourseOffering room requirements."""

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from academics.models import CourseOffering
from common.imports import rows_from_upload
from scheduling.management.commands.audit_timetable_import import audit
from scheduling.models import TimetableVersion
from scheduling.services.room_requirement_repair import plan_room_requirement_repair, source_room_types


VERSION_ID = 'e5ba666e-b867-4d08-8960-7ede38c98222'


def backup_sqlite():
    database = settings.DATABASES['default']
    if database['ENGINE'] != 'django.db.backends.sqlite3':
        raise CommandError('Automatic backup is implemented only for SQLite; apply was refused.')
    source = Path(database['NAME']).resolve()
    if not source.is_file():
        raise CommandError(f'SQLite database does not exist: {source}')
    directory = (Path(settings.BASE_DIR) / 'backups').resolve()
    if not directory.is_relative_to(Path(settings.BASE_DIR).resolve()):
        raise CommandError('Backup path escaped the backend directory.')
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f_UTC')
    target = directory / f'pre_v6_room_requirement_repair_{timestamp}.sqlite3'
    if target.exists():
        raise CommandError(f'Backup target already exists: {target}')
    with sqlite3.connect(f'file:{source.as_posix()}?mode=ro', uri=True) as original:
        with sqlite3.connect(str(target)) as backup:
            original.backup(backup)
            if backup.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise CommandError(f'Backup integrity check failed: {target}')
    return target


class Command(BaseCommand):
    help = 'Dry-run by default; --apply backs up SQLite then repairs only conclusive v6 lab requirements.'

    def add_arguments(self, parser):
        parser.add_argument('--workbook', required=True)
        parser.add_argument('--audit', help='Baseline audit JSON to verify before first apply.')
        parser.add_argument('--report', help='Write dry-run/apply decision JSON to this path.')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--restore-mixed-from', help='Prior apply report; restore only offerings whose full source rooms are mixed.')

    def handle(self, *args, **options):
        version = TimetableVersion.objects.select_related('timetable__semester').get(pk=VERSION_ID)
        if version.status != TimetableVersion.Status.DRAFT:
            raise CommandError('v6 is not DRAFT; repair refused.')
        workbook = Path(options['workbook'])
        if not workbook.is_file():
            raise CommandError(f'Workbook not found: {workbook}')
        upload = SimpleUploadedFile(workbook.name, workbook.read_bytes())
        source_rows = rows_from_upload(upload)
        if options['restore_mixed_from']:
            if not options['apply']:
                raise CommandError('--restore-mixed-from requires --apply.')
            applied = json.loads(Path(options['restore_mixed_from']).read_text(encoding='utf-8'))
            if applied['version_id'] != VERSION_ID or applied['mode'] != 'apply':
                raise CommandError('Prior apply report does not match v6.')
            all_types = source_room_types(source_rows)
            mixed = [item for item in applied['decisions']
                     if item['classification'] == 'CLEAR_REQUIREMENT_ERROR'
                     and all_types.get((item['section'].casefold(), item['course_code'].casefold()), set())
                     != {item['proposed_requirement']}]
            backup_path = backup_sqlite() if mixed else None
            with transaction.atomic():
                for item in mixed:
                    offering = CourseOffering.objects.select_for_update().get(pk=item['offering_id'])
                    if offering.room_type_requirement != item['proposed_requirement']:
                        raise CommandError(f'CourseOffering {offering.pk} changed since apply; restore refused.')
                    offering.room_type_requirement = item['old_requirement']
                    offering.save(update_fields=['room_type_requirement'])
            output = {'mode': 'restore_mixed', 'version_id': VERSION_ID,
                      'backup_path': str(backup_path) if backup_path else None,
                      'offerings_restored': len(mixed),
                      'restored_offering_ids': [item['offering_id'] for item in mixed]}
            rendered = json.dumps(output, indent=2)
            if options['report']:
                target = Path(options['report'])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(rendered + '\n', encoding='utf-8')
                self.stdout.write(f'Restore report written to {target}')
            else:
                self.stdout.write(rendered)
            return
        report = audit(version, source_rows)
        if options['audit']:
            baseline = json.loads(Path(options['audit']).read_text(encoding='utf-8'))
            if baseline['version_id'] != VERSION_ID or baseline['summary'] != report['summary']:
                raise CommandError('Baseline audit does not match the current workbook/database preview.')
            before = {(item['offering_id'], item['room_id'], tuple(item['rows']))
                      for item in baseline['room_type_mismatches']}
            current = {(item['offering_id'], item['room_id'], tuple(item['rows']))
                       for item in report['room_type_mismatches']}
            if before != current:
                raise CommandError('Baseline room-type mismatch evidence changed; repair refused.')
        decisions = plan_room_requirement_repair(report, source_rows)
        candidates = [item for item in decisions if item['classification'] == 'CLEAR_REQUIREMENT_ERROR']
        backup_path = None
        changed = 0
        if options['apply'] and candidates:
            backup_path = backup_sqlite()
            with transaction.atomic():
                locked_version = TimetableVersion.objects.select_for_update().get(pk=VERSION_ID)
                if locked_version.status != TimetableVersion.Status.DRAFT:
                    raise CommandError('v6 status changed; repair refused.')
                for item in candidates:
                    offering = CourseOffering.objects.select_for_update().get(pk=item['offering_id'])
                    if (offering.room_type_requirement != item['old_requirement']
                            or offering.default_class_type != item['class_type']):
                        raise CommandError(f'CourseOffering {offering.pk} changed since audit; repair refused.')
                    offering.room_type_requirement = item['proposed_requirement']
                    offering.save(update_fields=['room_type_requirement'])
                    changed += 1
        output = {
            'mode': 'apply' if options['apply'] else 'dry_run',
            'version_id': VERSION_ID, 'workbook': str(workbook),
            'preview_before': report['summary'],
            'classifications': dict(sorted(Counter(item['classification'] for item in decisions).items())),
            'offerings_changed': changed,
            'backup_path': str(backup_path) if backup_path else None,
            'decisions': decisions,
        }
        rendered = json.dumps(output, indent=2)
        if options['report']:
            target = Path(options['report'])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered + '\n', encoding='utf-8')
            self.stdout.write(f'Repair report written to {target}')
        else:
            self.stdout.write(rendered)
