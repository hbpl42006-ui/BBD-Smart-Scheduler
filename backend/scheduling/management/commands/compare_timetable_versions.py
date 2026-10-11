import json

from django.core.management.base import BaseCommand, CommandError

from scheduling.models import TimetableVersion
from scheduling.services.timetable_migration import version_semantic_fingerprint


class Command(BaseCommand):
    help = 'Compare timetable allocations by normalized academic identities rather than UUIDs.'

    def add_arguments(self, parser):
        parser.add_argument('--left-version-id', required=True)
        parser.add_argument('--right-version-id', required=True)

    def handle(self, *args, **options):
        try:
            left = TimetableVersion.objects.get(pk=options['left_version_id'])
            right = TimetableVersion.objects.get(pk=options['right_version_id'])
            left_fp = version_semantic_fingerprint(left)
            right_fp = version_semantic_fingerprint(right)
        except Exception as exc:
            raise CommandError(str(exc)) from exc
        report = {
            'left': {'id': str(left.pk), 'version_no': left.version_no, **{k: v for k, v in left_fp.items() if k != 'allocations'}},
            'right': {'id': str(right.pk), 'version_no': right.version_no, **{k: v for k, v in right_fp.items() if k != 'allocations'}},
            'academically_equivalent': left_fp['count'] == right_fp['count'] and left_fp['sha256'] == right_fp['sha256'],
            'note': 'Identity-normalized allocations include section, offering, weekday, time slot, block, room, delivery/type, lock/note, faculty identity, and faculty role.'
        }
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))
