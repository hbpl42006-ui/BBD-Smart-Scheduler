import copy
import json
from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from django.core.management import call_command, CommandError

from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
from audit.models import AuditEvent
from scheduling.services.timetable_migration import (
    export_package, import_package, migration_json_dumps,
)
from scheduling.test_scheduling import data


def make_v6_v7(d):
    v5 = TimetableVersion.objects.create(
        timetable=d['timetable'], version_no=5, status='DRAFT', created_by=d['admin'])
    v6 = TimetableVersion.objects.create(
        timetable=d['timetable'], version_no=6, status='DRAFT', previous_version=v5,
        created_by=d['admin'])
    v7 = TimetableVersion.objects.create(
        timetable=d['timetable'], version_no=7, status='DRAFT', previous_version=v6,
        created_by=d['admin'])
    for version in (v6, v7):
        entry = ScheduleEntry.objects.create(
            version=version, section=d['section'], course_offering=d['offering'],
            weekday=0, start_slot=d['slots'][0], room=d['room'],
        )
        ScheduleEntryFaculty.objects.create(
            schedule_entry=entry, faculty=d['faculty'], role='PRIMARY')
    return v5, v6, v7


def remove_exported_versions(v6, v7):
    # Simulate the target database after exporting from a separate local DB.
    ids = (str(v6.pk), str(v7.pk))
    v7.delete()
    v6.delete()
    return ids


@pytest.mark.django_db(transaction=True)
def test_export_import_preserves_ids_assignments_drafts_and_legacy_versions(data):
    v5, v6, v7 = make_v6_v7(data)
    before = list(TimetableVersion.objects.filter(version_no__lte=5).values_list(
        'id', 'status', 'previous_version_id'))
    package = export_package([str(v6.pk), str(v7.pk)])

    report = import_package(package, apply=True)

    assert report['mode'] == 'APPLIED'
    assert list(TimetableVersion.objects.filter(version_no__lte=5).values_list(
        'id', 'status', 'previous_version_id')) == before
    for source in (v6, v7):
        imported = TimetableVersion.objects.get(pk=source.pk)
        assert imported.status == 'DRAFT'
        assert imported.entries.count() == 1
        assignment = imported.entries.get().faculty_assignments.get()
        assert assignment.pk == source.entries.get().faculty_assignments.get().pk
        assert assignment.faculty_id == data['faculty'].pk
    assert TimetableVersion.objects.get(pk=v7.pk).previous_version_id == v6.pk
    assert TimetableVersion.objects.get(pk=v6.pk).previous_version_id == v5.pk


@pytest.mark.django_db
def test_import_defaults_to_dry_run_and_repeat_apply_is_idempotent(data):
    _, v6, v7 = make_v6_v7(data)
    package = export_package([str(v6.pk), str(v7.pk)])
    v6_id, v7_id = remove_exported_versions(v6, v7)

    dry_run = import_package(package)
    assert dry_run['ready'] is True
    assert not TimetableVersion.objects.filter(pk=v6_id).exists()
    first = import_package(package, apply=True)
    second = import_package(package, apply=True)
    assert first['mode'] == 'APPLIED'
    assert all(item['already_imported'] for item in second['versions'])
    assert TimetableVersion.objects.filter(pk__in=[v6_id, v7_id]).count() == 2
    assert ScheduleEntry.objects.filter(version_id__in=[v6_id, v7_id]).count() == 2
    assert ScheduleEntryFaculty.objects.filter(schedule_entry__version_id__in=[v6_id, v7_id]).count() == 2


@pytest.mark.django_db
def test_missing_master_reference_is_rejected_without_writes(data):
    _, v6, v7 = make_v6_v7(data)
    package = copy.deepcopy(export_package([str(v6.pk), str(v7.pk)]))
    v6_id, v7_id = remove_exported_versions(v6, v7)
    package['versions'][0]['entries'][0]['room_id'] = '00000000-0000-0000-0000-000000000001'
    package['sha256'] = ''
    from scheduling.services.timetable_migration import _snapshot_hash
    package['sha256'] = _snapshot_hash({key: value for key, value in package.items() if key != 'sha256'})

    with pytest.raises(ValueError, match='identity snapshot|Missing target reference'):
        import_package(package, apply=True)
    assert not TimetableVersion.objects.filter(pk=v6_id).exists()
    assert not TimetableVersion.objects.filter(pk=v7_id).exists()


@pytest.mark.django_db(transaction=True)
def test_assignment_failure_rolls_back_versions_entries_and_assignments(data, monkeypatch):
    _, v6, v7 = make_v6_v7(data)
    package = export_package([str(v6.pk), str(v7.pk)])
    v6_id, v7_id = remove_exported_versions(v6, v7)
    original_save = ScheduleEntryFaculty.save
    calls = {'count': 0}

    def fail_second(instance, *args, **kwargs):
        calls['count'] += 1
        if calls['count'] == 2:
            raise RuntimeError('simulated assignment insert failure')
        return original_save(instance, *args, **kwargs)

    monkeypatch.setattr(ScheduleEntryFaculty, 'save', fail_second)
    with pytest.raises(RuntimeError, match='simulated'):
        import_package(package, apply=True)
    assert not TimetableVersion.objects.filter(pk__in=[v6_id, v7_id]).exists()
    assert not ScheduleEntry.objects.filter(version_id__in=[v6_id, v7_id]).exists()
    assert not ScheduleEntryFaculty.objects.filter(schedule_entry__version_id__in=[v6_id, v7_id]).exists()


@pytest.mark.django_db
def test_conflicting_version_number_is_rejected_and_existing_v1_v5_preserved(data):
    _, v6, v7 = make_v6_v7(data)
    package = copy.deepcopy(export_package([str(v6.pk), str(v7.pk)]))
    v6_id, v7_id = remove_exported_versions(v6, v7)
    package['versions'][0]['version_no'] = 8
    from scheduling.services.timetable_migration import _snapshot_hash
    package['sha256'] = _snapshot_hash({key: value for key, value in package.items() if key != 'sha256'})
    conflicting = TimetableVersion.objects.create(
        timetable=data['timetable'], version_no=8, status='DRAFT', created_by=data['admin'])
    before = list(TimetableVersion.objects.filter(version_no__lte=5).values_list('id', 'status'))

    with pytest.raises(ValueError, match='already belongs'):
        import_package(package)
    assert list(TimetableVersion.objects.filter(version_no__lte=5).values_list('id', 'status')) == before
    assert TimetableVersion.objects.filter(pk=conflicting.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_non_equivalent_v5_lineage_requires_note_and_is_audited(data):
    v5, v6, v7 = make_v6_v7(data)
    package = export_package([str(v6.pk), str(v7.pk)])
    remove_exported_versions(v6, v7)
    ScheduleEntry.objects.create(
        version=v5, section=data['section'], course_offering=data['offering'],
        weekday=0, start_slot=data['slots'][0], room=data['room'])

    with pytest.raises(ValueError, match='not academically equivalent'):
        import_package(package, apply=True)
    assert not TimetableVersion.objects.filter(pk=package['versions'][0]['id']).exists()

    report = import_package(
        package, apply=True, approve_non_equivalent_lineage=True,
        approval_note='Reviewed and approved v5 lineage difference for this migration.')
    assert report['warnings']
    event = AuditEvent.objects.get(event_type='TIMETABLE_VERSION_LINEAGE_OVERRIDE')
    assert event.metadata['package_sha256'] == package['sha256']
    assert 'Reviewed and approved' in event.metadata['approval_note']


@pytest.mark.django_db
def test_export_json_round_trip_preserves_canonical_uuid_references(data):
    _, v6, v7 = make_v6_v7(data)
    package = export_package([str(v6.pk), str(v7.pk)])

    encoded = migration_json_dumps(package, indent=2, sort_keys=True)
    decoded = json.loads(encoded)
    v6_id, v7_id = remove_exported_versions(v6, v7)
    report = import_package(decoded, apply=True)

    assert report['mode'] == 'APPLIED'
    assert decoded['versions'][0]['id'] == v6_id
    assert decoded['versions'][1]['id'] == v7_id
    for version_row in decoded['versions']:
        assert len(version_row['entries']) == 1
        entry = version_row['entries'][0]
        UUID(entry['id'])
        UUID(entry['section_id'])
        UUID(entry['course_offering_id'])
        UUID(entry['start_slot_id'])
        UUID(entry['assignments'][0]['id'])
        UUID(entry['assignments'][0]['faculty_id'])
        assert TimetableVersion.objects.get(pk=version_row['id']).entries.count() == 1


@pytest.mark.django_db
def test_identical_course_offering_identity_passes_dry_run(data):
    _, v6, v7 = make_v6_v7(data)
    package = export_package([str(v6.pk), str(v7.pk)])

    report = import_package(package)

    assert report['ready'] is True


@pytest.mark.django_db
def test_course_offering_identity_mismatch_reports_exact_fields_without_writes(data):
    _, v6, v7 = make_v6_v7(data)
    package = export_package([str(v6.pk), str(v7.pk)])
    v6_id, v7_id = remove_exported_versions(v6, v7)
    source_block_size = data['offering'].required_block_size
    data['offering'].required_block_size += 1
    data['offering'].save(update_fields=['required_block_size'])

    with pytest.raises(ValueError) as raised:
        import_package(package, apply=True)

    message = str(raised.value)
    assert 'Full mismatch report' in message
    assert 'academics.CourseOffering' in message
    assert str(data['offering'].pk) in message
    assert 'required_block_size' in message
    assert f'"source": {source_block_size}' in message
    assert f'"destination": {source_block_size + 1}' in message
    assert not TimetableVersion.objects.filter(pk__in=[v6_id, v7_id]).exists()


@pytest.mark.django_db
def test_import_command_writes_failure_report_for_dependency_mismatch(data, tmp_path):
    _, v6, v7 = make_v6_v7(data)
    package = export_package([str(v6.pk), str(v7.pk)])
    remove_exported_versions(v6, v7)
    data['offering'].required_block_size += 1
    data['offering'].save(update_fields=['required_block_size'])
    package_path = tmp_path / 'migration-package.json'
    report_path = tmp_path / 'dry-run-report.json'
    package_path.write_text(json.dumps(package), encoding='utf-8')

    with pytest.raises(CommandError, match='Full mismatch report'):
        call_command(
            'import_timetable_versions', package=str(package_path),
            report=str(report_path), expected_entries=1,
        )

    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['mode'] == 'DRY_RUN'
    assert report['ready'] is False
    assert 'academics.CourseOffering' in report['error']
    assert 'required_block_size' in report['error']


def test_migration_json_encoder_handles_supported_types_and_rejects_unknown():
    identifier = uuid4()
    value = {
        'uuid': identifier,
        'date': date(2026, 10, 11),
        'datetime': datetime(2026, 10, 11, 12, 30, tzinfo=timezone.utc),
        'time': time(12, 30),
        'decimal': Decimal('3.00'),
    }

    decoded = json.loads(migration_json_dumps(value))

    assert decoded == {
        'uuid': str(identifier), 'date': '2026-10-11',
        'datetime': '2026-10-11T12:30:00+00:00', 'time': '12:30:00',
        'decimal': '3.00',
    }
    with pytest.raises(TypeError, match='Unsupported JSON value type'):
        migration_json_dumps({'unsupported': object()})
