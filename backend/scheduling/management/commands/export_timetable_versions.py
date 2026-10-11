import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from scheduling.services.timetable_migration import export_package, migration_json_dumps


class Command(BaseCommand):
    help = 'Export selected DRAFT timetable versions and their assignment rows to a checksummed JSON package.'

    def add_arguments(self, parser):
        parser.add_argument('--version-id', action='append', required=True, dest='version_ids', help='May be repeated.')
        parser.add_argument('--output', required=True)
        parser.add_argument('--expected-entries', type=int, default=1692,
                            help='Require this many ScheduleEntry rows in every selected version (default: 1692).')

    def handle(self, *args, **options):
        try:
            package = export_package(options['version_ids'])
            for version in package['versions']:
                if len(version['entries']) != options['expected_entries']:
                    raise ValueError(
                        f'Version {version["id"]} has {len(version["entries"])} entries; '
                        f'expected {options["expected_entries"]}.')
            target = Path(options['output']).expanduser().resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(migration_json_dumps(package, indent=2, sort_keys=True), encoding='utf-8')
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f'Exported {len(package["versions"])} draft version(s) to {target}; '
            f'entries={sum(len(v["entries"]) for v in package["versions"])}; '
            f'assignments={sum(len(e["assignments"]) for v in package["versions"] for e in v["entries"])}; '
            f'sha256={package["sha256"]}'
        ))
