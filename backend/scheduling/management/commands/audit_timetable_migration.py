import json

from django.core.management.base import BaseCommand, CommandError

from scheduling.models import TimetableVersion
from scheduling.services.timetable_migration import version_semantic_fingerprint, _validation_summary


class Command(BaseCommand):
    help = 'Read-only audit of timetable version lineage, entries, assignments, and academic fingerprints.'

    def add_arguments(self, parser):
        parser.add_argument('--version-id', action='append', required=True, dest='version_ids')
        parser.add_argument('--include-allocations', action='store_true', help='Include the full normalized allocation records.')

    def handle(self, *args, **options):
        report = {'read_only': True, 'versions': []}
        for version_id in options['version_ids']:
            try:
                version = TimetableVersion.objects.select_related(
                    'timetable', 'previous_version').get(pk=version_id)
            except (TimetableVersion.DoesNotExist, ValueError) as exc:
                raise CommandError(f'Version {version_id} was not found.') from exc
            fingerprint = version_semantic_fingerprint(version)
            item = {
                'id': str(version.pk), 'timetable_id': str(version.timetable_id),
                'version_no': version.version_no, 'status': version.status,
                'previous_version_id': str(version.previous_version_id) if version.previous_version_id else None,
                'entry_count': version.entries.count(),
                'assignment_count': sum(entry.faculty_assignments.count() for entry in version.entries.all()),
                'distinct_faculty_count': version.entries.values('faculty_assignments__faculty_id').distinct().count(),
                'normalized_allocation_count': fingerprint['count'],
                'normalized_allocation_sha256': fingerprint['sha256'],
                'validation': _validation_summary(version, expected_rows=version.entries.count()),
            }
            if options['include_allocations']:
                item['semantic_allocations'] = fingerprint['allocations']
            report['versions'].append(item)
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
