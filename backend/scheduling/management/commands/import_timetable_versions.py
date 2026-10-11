import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from accounts.models import User

from scheduling.services.timetable_migration import import_package


class Command(BaseCommand):
    help = 'Validate a timetable migration package (dry-run by default); --apply performs an atomic draft-only import.'

    def add_arguments(self, parser):
        parser.add_argument('--package', required=True)
        parser.add_argument('--mapping-file', help='Explicit source-ID to target-ID mappings in JSON form.')
        parser.add_argument('--apply', action='store_true', help='Commit import; omitted means dry-run.')
        parser.add_argument('--approve-non-equivalent-lineage', action='store_true')
        parser.add_argument('--approval-note', default='')
        parser.add_argument('--operator', help='Production user UUID/email recorded as actor in the audit log.')
        parser.add_argument('--report', help='Write the validation/apply report to this JSON path.')
        parser.add_argument('--expected-entries', type=int, default=1692,
                            help='Require this many rows per imported version (default: 1692).')

    def handle(self, *args, **options):
        try:
            if options['apply'] and not options['operator']:
                raise ValueError('--operator is required with --apply so the migration is attributable in the audit log.')
            if options['approve_non_equivalent_lineage'] and not options['approval_note'].strip():
                raise ValueError('--approval-note is required for non-equivalent lineage approval.')
            if options['approve_non_equivalent_lineage'] and not options['operator']:
                raise ValueError('--operator is required for non-equivalent lineage approval.')
            actor_id = None
            if options['operator']:
                try:
                    actor = User.objects.get(pk=options['operator'])
                except (User.DoesNotExist, ValueError):
                    actor = User.objects.get(email__iexact=options['operator'])
                if not actor.is_active:
                    raise ValueError('The audit operator account is inactive.')
                actor_id = str(actor.pk)
            package = json.loads(Path(options['package']).expanduser().read_text(encoding='utf-8'))
            for version in package.get('versions', []):
                if len(version.get('entries', [])) != options['expected_entries']:
                    raise ValueError(
                        f'Version {version.get("id")} has {len(version.get("entries", []))} entries; '
                        f'expected {options["expected_entries"]}.')
            mappings = {}
            if options['mapping_file']:
                mappings = json.loads(Path(options['mapping_file']).expanduser().read_text(encoding='utf-8'))
            report = import_package(
                package, apply=options['apply'], explicit_mapping=mappings,
                approve_non_equivalent_lineage=options['approve_non_equivalent_lineage'],
                approval_note=options['approval_note'],
                actor_id=actor_id,
            )
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
        if options['report']:
            Path(options['report']).expanduser().write_text(
                json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
