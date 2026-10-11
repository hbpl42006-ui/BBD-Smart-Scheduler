import copy

import pytest

from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
from audit.models import AuditEvent
from scheduling.services.timetable_migration import export_package, import_package
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
