"""Portable, fail-closed migration packages for draft timetable versions.

Packages contain only selected versions, their entries/assignments, and
semantic snapshots of referenced master records. They never carry Django
settings, credentials, or database files.
"""
import hashlib
import json
from functools import lru_cache
from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from uuid import UUID

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction

from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, TimetableVersion

PACKAGE_FORMAT = 'bbd-timetable-version-migration'
PACKAGE_VERSION = 1


def _validation_summary(version, expected_rows):
    from scheduling.management.commands.verify_timetable_import import verify
    from scheduling.services.validation import validate_version

    integrity = verify(version, expected_rows=expected_rows)
    academic = validate_version(version)
    conflicts = academic.get('conflicts', [])
    return {
        'integrity': integrity,
        'academic': {
            'valid': academic.get('valid'),
            'conflict_count': len(conflicts),
            'conflict_types': dict(Counter(item.get('type', 'UNKNOWN') for item in conflicts)),
            'conflicts': conflicts,
        },
    }


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def canonical_json_value(value):
    """Convert supported values to a deterministic JSON tree; reject unknown types."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return canonical_json_value(value.value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if isinstance(key, UUID):
                normalized_key = str(key)
            elif isinstance(key, Enum):
                normalized_key = str(key.value)
            elif isinstance(key, (str, int, float, bool)):
                normalized_key = str(key)
            else:
                raise TypeError(f'Unsupported JSON object key type: {type(key).__name__}.')
            normalized[normalized_key] = canonical_json_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [canonical_json_value(item) for item in value]

    # Retain Django's explicit support for values such as lazy strings and
    # timedeltas; unsupported objects still raise instead of being stringified.
    try:
        encoded = DjangoJSONEncoder().default(value)
    except TypeError as exc:
        raise TypeError(f'Unsupported JSON value type: {type(value).__name__}.') from exc
    if encoded is value:
        raise TypeError(f'Unsupported JSON value type: {type(value).__name__}.')
    return canonical_json_value(encoded)


def migration_json_dumps(value, **kwargs):
    """Serialize a canonical tree; no fallback-to-string for unknown values."""
    kwargs.setdefault('allow_nan', False)
    return json.dumps(canonical_json_value(value), cls=DjangoJSONEncoder, **kwargs)


def _model_snapshot(instance):
    """Stable, explicit semantic identity; exclude mutable timestamps."""
    return _model_snapshot_cached(type(instance), str(instance.pk))


@lru_cache(maxsize=100000)
def _model_snapshot_cached(model, pk):
    instance = model.objects.get(pk=pk)
    fk = {
        'Institution': [], 'Department': ['institution'],
        'Program': ['department'], 'AcademicSession': ['institution'],
        'Semester': ['session'], 'Section': ['program', 'semester', 'coordinator'],
        'Course': [], 'CourseOffering': ['semester', 'section', 'course', 'preferred_room'],
        'TimeSlotTemplate': ['institution'], 'TimeSlot': ['template'],
        'Room': ['reserved_program'], 'Faculty': ['department', 'user'],
        'User': [], 'Timetable': ['institution', 'academic_session', 'semester', 'department', 'created_by'],
    }.get(model.__name__, [])
    fields = {}
    for field in model._meta.concrete_fields:
        name = field.name
        if name in {'created_at', 'updated_at', 'password', 'last_login'}:
            continue
        if name in fk:
            related = getattr(instance, name)
            fields[name] = None if related is None else {
                'id': str(related.pk), 'identity': _model_snapshot(related)
            }
        else:
            value = getattr(instance, name)
            if field.get_internal_type() == 'FileField' or field.get_internal_type() == 'ImageField':
                value = value.name if value else ''
            fields[name] = _json_value(value)
    if hasattr(instance, 'facilities'):
        fields['facilities'] = instance.facilities
    if model.__name__ == 'User':
        # A source user's password/permissions are not migration data.
        fields = {'email': instance.email.casefold(), 'employee_id': instance.employee_id,
                  'first_name': instance.first_name, 'last_name': instance.last_name}
    return fields


def _snapshot_hash(value):
    return hashlib.sha256(migration_json_dumps(
        value, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _semantic_identity(value):
    """Remove nested record IDs while retaining all descriptive field values."""
    if isinstance(value, dict):
        return {key: _semantic_identity(item) for key, item in value.items() if key != 'id'}
    if isinstance(value, list):
        return [_semantic_identity(item) for item in value]
    return value


def _entry_semantics(entry):
    assignments = []
    for assignment in entry.faculty_assignments.select_related('faculty', 'faculty__department').order_by('faculty_id'):
        assignments.append({'faculty': _semantic_identity(_model_snapshot(assignment.faculty)), 'role': assignment.role})
    assignments.sort(key=lambda row: migration_json_dumps(row, sort_keys=True, separators=(',', ':')))
    return {
        'section': _semantic_identity(_model_snapshot(entry.section)),
        'offering': _semantic_identity(_model_snapshot(entry.course_offering)),
        'weekday': entry.weekday, 'slot': _semantic_identity(_model_snapshot(entry.start_slot)),
        'block_length': entry.block_length,
        'room': _semantic_identity(_model_snapshot(entry.room)) if entry.room_id else None,
        'entry_type': entry.entry_type, 'delivery_mode': entry.delivery_mode,
        'locked': entry.locked, 'note': entry.note, 'faculty': assignments,
    }


def version_semantic_fingerprint(version):
    rows = [_entry_semantics(entry) for entry in version.entries.select_related(
        'section__program__department', 'section__semester__session',
        'course_offering__course', 'course_offering__section__program',
        'course_offering__semester__session', 'start_slot__template__institution',
        'room__reserved_program',
    ).prefetch_related('faculty_assignments__faculty__department')]
    rows.sort(key=lambda row: migration_json_dumps(row, sort_keys=True, separators=(',', ':')))
    return {'count': len(rows), 'sha256': _snapshot_hash(rows), 'allocations': rows}


def _reference_manifest(version):
    """Capture IDs + semantics for every FK needed by entries and lineage."""
    entries = list(version.entries.select_related(
        'section__program__department', 'section__semester__session',
        'course_offering__course', 'course_offering__section__program',
        'course_offering__semester__session', 'course_offering__preferred_room',
        'start_slot__template__institution', 'room__reserved_program',
    ).prefetch_related('faculty_assignments__faculty__department', 'faculty_assignments__faculty__user'))
    refs = {}

    def add(instance):
        if instance is None:
            return
        label = instance._meta.label
        item = {'model': label, 'id': str(instance.pk), 'identity': _model_snapshot(instance)}
        refs.setdefault(label, {})[str(instance.pk)] = item

    add(version.timetable)
    add(version.timetable.institution); add(version.timetable.academic_session)
    add(version.timetable.semester); add(version.timetable.department); add(version.timetable.created_by)
    for entry in entries:
        for obj in (entry.section, entry.section.program, entry.section.program.department,
                    entry.section.semester, entry.section.semester.session,
                    entry.course_offering, entry.course_offering.course,
                    entry.course_offering.section, entry.course_offering.semester,
                    entry.course_offering.preferred_room, entry.start_slot,
                    entry.start_slot.template, entry.start_slot.template.institution,
                    entry.room, entry.room.reserved_program if entry.room_id else None):
            add(obj)
        for assignment in entry.faculty_assignments.all():
            add(assignment.faculty); add(assignment.faculty.department); add(assignment.faculty.user)
    return refs


def export_package(version_ids, include_lineage=True, using='default'):
    versions = list(TimetableVersion.objects.using(using).filter(pk__in=version_ids).select_related(
        'timetable__institution', 'timetable__academic_session', 'timetable__semester',
        'timetable__department', 'timetable__created_by', 'previous_version', 'created_by',
    ).order_by('version_no'))
    if len(versions) != len(set(map(str, version_ids))):
        missing = sorted(set(map(str, version_ids)) - {str(v.pk) for v in versions})
        raise ValueError(f'Requested timetable version(s) missing: {missing}')
    selected_ids = {str(v.pk) for v in versions}
    output_versions = []
    for version in versions:
        if version.status != TimetableVersion.Status.DRAFT:
            raise ValueError(f'{version.pk} is {version.status}; only draft versions can be exported.')
        if version.previous_version_id and version.previous_version_id not in selected_ids and not include_lineage:
            raise ValueError(f'{version.pk} has an external predecessor; lineage snapshot is required.')
        entries = list(version.entries.order_by('id'))
        entry_rows = []
        for entry in entries:
            entry_rows.append({
                'id': str(entry.pk), 'section_id': str(entry.section_id),
                'course_offering_id': str(entry.course_offering_id), 'weekday': entry.weekday,
                'start_slot_id': str(entry.start_slot_id), 'block_length': entry.block_length,
                'room_id': str(entry.room_id) if entry.room_id else None,
                'entry_type': entry.entry_type, 'delivery_mode': entry.delivery_mode,
                'locked': entry.locked, 'note': entry.note,
                'created_at': entry.created_at.isoformat(), 'updated_at': entry.updated_at.isoformat(),
                'assignments': [
                    {'id': str(a.pk), 'faculty_id': str(a.faculty_id), 'role': a.role}
                    for a in entry.faculty_assignments.order_by('id')
                ],
            })
        row = {
            'id': str(version.pk), 'timetable_id': str(version.timetable_id),
            'version_no': version.version_no,
            'previous_version_id': str(version.previous_version_id) if version.previous_version_id else None,
            'created_by_id': str(version.created_by_id), 'notes': version.notes,
            'created_at': version.created_at.isoformat(),
            'entries': entry_rows,
            'references': _reference_manifest(version),
            'semantic_fingerprint': {
                key: value for key, value in version_semantic_fingerprint(version).items()
                if key != 'allocations'
            },
        }
        row['source_validation'] = _validation_summary(version, expected_rows=len(entry_rows))
        if version.previous_version_id and version.previous_version_id not in selected_ids:
            predecessor = TimetableVersion.objects.using(using).select_related('timetable').get(pk=version.previous_version_id)
            predecessor_fingerprint = version_semantic_fingerprint(predecessor)
            row['external_predecessor'] = {
                'id': str(predecessor.pk), 'timetable_id': str(predecessor.timetable_id),
                'version_no': predecessor.version_no, 'status': predecessor.status,
                'semantic_fingerprint': {
                    'count': predecessor_fingerprint['count'],
                    'sha256': predecessor_fingerprint['sha256'],
                },
                'row_fingerprint': _snapshot_hash({
                    'id': str(predecessor.pk), 'timetable_id': str(predecessor.timetable_id),
                    'version_no': predecessor.version_no, 'status': predecessor.status,
                    'entries': predecessor.entries.count(),
                }),
            }
        output_versions.append(row)
    payload = {
        'format': PACKAGE_FORMAT, 'format_version': PACKAGE_VERSION,
        'source_database_vendor': __import__('django.db', fromlist=['connections']).connections[using].vendor,
        'versions': output_versions,
    }
    payload = canonical_json_value(payload)
    payload['sha256'] = _snapshot_hash(payload)
    return payload


def _verify_package(payload):
    if payload.get('format') != PACKAGE_FORMAT or payload.get('format_version') != PACKAGE_VERSION:
        raise ValueError('Unsupported migration package format/version.')
    expected = payload.get('sha256')
    unsigned = dict(payload); unsigned.pop('sha256', None)
    if not expected or expected != _snapshot_hash(unsigned):
        raise ValueError('Migration package checksum mismatch.')
    ids = [v.get('id') for v in payload.get('versions', [])]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Package must contain uniquely identified version records.')


def _resolve_mapping(model_label, source_id, refs, explicit):
    explicit_id = explicit.get(model_label, {}).get(source_id)
    target_id = explicit_id or source_id
    try:
        model = __import__(model_label.split('.')[0] + '.models', fromlist=['*'])
        klass = model
        for part in model_label.split('.')[1:]:
            klass = getattr(klass, part)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f'Unsupported referenced model {model_label}.') from exc
    source_ref = refs.get(model_label, {}).get(source_id)
    if not source_ref:
        raise ValueError(f'Package is missing identity snapshot for {model_label} {source_id}.')
    try:
        target = klass.objects.get(pk=target_id)
    except (klass.DoesNotExist, ValueError, TypeError):
        raise ValueError(f'Missing target reference for {model_label}: source {source_id}, target {target_id}.')
    target_identity = _model_snapshot(target)
    if _semantic_identity(target_identity) != _semantic_identity(source_ref['identity']):
        raise ValueError(f'Identity mismatch for {model_label}: source {source_id} mapped to target {target_id}.')
    return str(target.pk), target


def _production_v5_fingerprint(version_id):
    version = TimetableVersion.objects.select_related('timetable').get(pk=version_id)
    if version.version_no != 5:
        raise ValueError(f'Lineage target {version_id} is not version 5.')
    return version_semantic_fingerprint(version), version


def _legacy_snapshot():
    rows = []
    for version in TimetableVersion.objects.filter(version_no__lte=5).order_by('timetable_id', 'version_no'):
        rows.append({
            'version_data': TimetableVersion.objects.filter(pk=version.pk).values().get(),
            'entries': list(version.entries.order_by('id').values()),
            'assignments': list(ScheduleEntryFaculty.objects.filter(
                schedule_entry__version=version).order_by('id').values()),
        })
    return _snapshot_hash(rows)


def import_package(payload, *, apply=False, explicit_mapping=None,
                   approve_non_equivalent_lineage=False, approval_note='',
                   actor_id=None, using='default'):
    """Validate by default; apply all requested draft rows in one transaction."""
    _verify_package(payload)
    explicit_mapping = explicit_mapping or {}
    versions_payload = payload['versions']
    all_ids = {row['id'] for row in versions_payload}
    all_entry_ids = [entry['id'] for version in versions_payload for entry in version['entries']]
    all_assignment_ids = [assignment['id'] for version in versions_payload for entry in version['entries'] for assignment in entry['assignments']]
    if len(all_entry_ids) != len(set(all_entry_ids)):
        raise ValueError('ScheduleEntry UUIDs must be unique across the whole package.')
    if len(all_assignment_ids) != len(set(all_assignment_ids)):
        raise ValueError('ScheduleEntryFaculty UUIDs must be unique across the whole package.')
    if any(row.get('status') for row in versions_payload):
        raise ValueError('Package must not carry statuses; imported versions are forced to DRAFT.')
    mapped = {}
    warnings = []
    lineage_evidence = []
    counts = []
    legacy_before = _legacy_snapshot()
    for row in versions_payload:
        if len(row['entries']) != len({entry['id'] for entry in row['entries']}):
            raise ValueError(f'Duplicate ScheduleEntry UUID in version {row["id"]}.')
        if row.get('previous_version_id') and row['previous_version_id'] not in all_ids:
            predecessor = row.get('external_predecessor')
            if not predecessor:
                raise ValueError(f'Missing predecessor identity snapshot for {row["id"]}.')
            target_parent = explicit_mapping.get('scheduling.TimetableVersion', {}).get(predecessor['id'], predecessor['id'])
            target_fp, target = _production_v5_fingerprint(target_parent)
            target_timetable_id = explicit_mapping.get('scheduling.Timetable', {}).get(
                row['timetable_id'], row['timetable_id'])
            if str(target.timetable_id) != str(target_timetable_id):
                raise ValueError(f'Lineage target {target_parent} belongs to timetable {target.timetable_id}, not mapped timetable {target_timetable_id}.')
            equivalent = target_fp['sha256'] == predecessor['semantic_fingerprint']['sha256'] and target_fp['count'] == predecessor['semantic_fingerprint']['count']
            if not equivalent:
                if not (approve_non_equivalent_lineage and approval_note.strip()):
                    raise ValueError(
                        f'Local predecessor {predecessor["id"]} is not academically equivalent to target {target_parent}; '
                        f'local count/hash={predecessor["semantic_fingerprint"]["count"]}/'
                        f'{predecessor["semantic_fingerprint"]["sha256"]}, production count/hash='
                        f'{target_fp["count"]}/{target_fp["sha256"]}. Explicit informed approval and note required.'
                    )
                warnings.append(f'NON-EQUIVALENT LINEAGE APPROVED: {predecessor["id"]} -> {target.pk}: {approval_note.strip()}')
            lineage_evidence.append({
                'source_version_id': predecessor['id'], 'target_version_id': str(target.pk),
                'source_allocation_count': predecessor['semantic_fingerprint']['count'],
                'source_sha256': predecessor['semantic_fingerprint']['sha256'],
                'target_allocation_count': target_fp['count'], 'target_sha256': target_fp['sha256'],
                'academically_equivalent': equivalent,
                'approval_note': approval_note.strip() if not equivalent else '',
            })
            mapped[predecessor['id']] = str(target.pk)

        refs = row['references']
        timetable_source = row['timetable_id']
        mapped_timetable, timetable = _resolve_mapping('scheduling.Timetable', timetable_source, refs, explicit_mapping)
        mapped_creator, creator = _resolve_mapping('accounts.User', row['created_by_id'], refs, explicit_mapping)
        resolved_targets = {}
        for label, values in refs.items():
            for source_id in values:
                if label == 'scheduling.Timetable':
                    continue
                target_id, _ = _resolve_mapping(label, source_id, refs, explicit_mapping)
                prior_source = resolved_targets.setdefault(label, {}).get(target_id)
                if prior_source is not None and prior_source != source_id:
                    raise ValueError(f'Ambiguous {label} mapping: source IDs {prior_source} and {source_id} both map to {target_id}.')
                resolved_targets[label][target_id] = source_id
        for entry in row['entries']:
            faculty_ids = [assignment['faculty_id'] for assignment in entry['assignments']]
            if len(faculty_ids) != len(set(faculty_ids)):
                raise ValueError(f'Duplicate faculty assignment in entry {entry["id"]}.')
            direct_refs = [
                ('academics.Section', entry['section_id']),
                ('academics.CourseOffering', entry['course_offering_id']),
                ('common.TimeSlot', entry['start_slot_id']),
            ]
            if entry.get('room_id'):
                direct_refs.append(('rooms.Room', entry['room_id']))
            for assignment in entry['assignments']:
                direct_refs.append(('faculty.Faculty', assignment['faculty_id']))
            for label, source_id in direct_refs:
                _resolve_mapping(label, source_id, refs, explicit_mapping)
            conflicting_entry = ScheduleEntry.objects.filter(pk=entry['id']).exclude(version_id=row['id']).first()
            if conflicting_entry:
                raise ValueError(f'ScheduleEntry UUID {entry["id"]} already belongs to version {conflicting_entry.version_id}.')
            for assignment in entry['assignments']:
                conflicting_assignment = ScheduleEntryFaculty.objects.filter(pk=assignment['id']).exclude(schedule_entry_id=entry['id']).first()
                if conflicting_assignment:
                    raise ValueError(f'ScheduleEntryFaculty UUID {assignment["id"]} is already in use.')
        mapped[row['id']] = row['id']
        mapped[f'timetable:{row["id"]}'] = mapped_timetable
        mapped[f'creator:{row["id"]}'] = mapped_creator
        target_prev = row.get('previous_version_id')
        if target_prev:
            target_prev = mapped.get(target_prev, target_prev)
            if target_prev == row['id']:
                raise ValueError(f'Self-referential predecessor for {row["id"]}.')
            if target_prev not in all_ids and not TimetableVersion.objects.filter(pk=target_prev).exists():
                raise ValueError(f'Unresolved predecessor target {target_prev}.')
        existing = TimetableVersion.objects.filter(pk=row['id']).first()
        expected_previous = mapped.get(row.get('previous_version_id'), row.get('previous_version_id'))
        version_no_conflict = TimetableVersion.objects.filter(timetable_id=mapped_timetable, version_no=row['version_no']).first()
        if version_no_conflict and str(version_no_conflict.pk) != row['id']:
            raise ValueError(f'Version number {row["version_no"]} already belongs to {version_no_conflict.pk}; refusing overwrite.')
        if existing:
            if (str(existing.timetable_id) != mapped_timetable or existing.version_no != row['version_no']
                    or existing.status != TimetableVersion.Status.DRAFT
                    or str(existing.previous_version_id) != str(expected_previous)
                    or str(existing.created_by_id) != mapped_creator
                    or existing.notes != row.get('notes', '')
                    or existing.submitted_by_id is not None or existing.submitted_at is not None
                    or existing.approved_by_id is not None or existing.approved_at is not None
                    or existing.rejected_by_id is not None or existing.rejected_at is not None
                    or existing.rejection_reason or existing.published_by_id is not None
                    or existing.published_at is not None):
                raise ValueError(f'Existing version UUID conflict: {row["id"]}.')
            expected_entry_ids = {e['id'] for e in row['entries']}
            actual_entry_ids = {str(value) for value in existing.entries.values_list('id', flat=True)}
            if actual_entry_ids != expected_entry_ids:
                raise ValueError(f'Existing version {row["id"]} is partial/different; no mutation will be attempted.')
            existing_fp = version_semantic_fingerprint(existing)
            expected_fp = row['semantic_fingerprint']
            if existing_fp['count'] != expected_fp['count'] or existing_fp['sha256'] != expected_fp['sha256']:
                raise ValueError(f'Existing version {row["id"]} is not semantically identical to the package.')
            for entry in row['entries']:
                actual = ScheduleEntry.objects.filter(pk=entry['id'], version=existing).first()
                if not actual:
                    raise ValueError(f'Entry conflict under existing version {row["id"]}.')
                field_targets = {
                    'section_id': explicit_mapping.get('academics.Section', {}).get(entry['section_id'], entry['section_id']),
                    'course_offering_id': explicit_mapping.get('academics.CourseOffering', {}).get(entry['course_offering_id'], entry['course_offering_id']),
                    'start_slot_id': explicit_mapping.get('common.TimeSlot', {}).get(entry['start_slot_id'], entry['start_slot_id']),
                    'room_id': explicit_mapping.get('rooms.Room', {}).get(entry['room_id'], entry['room_id']) if entry['room_id'] else None,
                }
                if any(str(getattr(actual, key)) != str(value) if value is not None else getattr(actual, key) is not None
                       for key, value in field_targets.items()) or any(
                    getattr(actual, key) != entry[key] for key in (
                        'weekday', 'block_length', 'entry_type', 'delivery_mode', 'locked', 'note')
                ):
                    raise ValueError(f'Entry data mismatch for existing entry {entry["id"]}.')
                expected_fac = {(a['id'], explicit_mapping.get('faculty.Faculty', {}).get(a['faculty_id'], a['faculty_id']), a['role']) for a in entry['assignments']}
                actual_fac = set(actual.faculty_assignments.values_list('id', 'faculty_id', 'role'))
                if {(str(a), str(b), c) for a, b, c in actual_fac} != expected_fac:
                    raise ValueError(f'Assignment mismatch for existing entry {entry["id"]}.')
        counts.append({'version_id': row['id'], 'version_no': row['version_no'],
                       'entry_count': len(row['entries']), 'assignment_count': sum(len(e['assignments']) for e in row['entries']),
                       'already_imported': bool(existing)})

    if not apply:
        return {'mode': 'DRY RUN — no database changes made', 'versions': counts,
                'legacy_v1_v5_sha256_before': legacy_before, 'warnings': warnings,
                'source_validation': [
                    {'version_id': row['id'], 'report': row['source_validation']}
                    for row in versions_payload
                ],
                'ready': True}

    with transaction.atomic(using=using):
        # Recheck baseline inside the write transaction to close the audit/apply race.
        if _legacy_snapshot() != legacy_before:
            raise ValueError('Existing v1-v5 changed during migration preflight; transaction aborted.')
        for row in versions_payload:
            for label, values in row['references'].items():
                for source_id in values:
                    _resolve_mapping(label, source_id, row['references'], explicit_mapping)
        for evidence in lineage_evidence:
            current_fp, current_parent = _production_v5_fingerprint(evidence['target_version_id'])
            if (current_fp['count'] != evidence['target_allocation_count']
                    or current_fp['sha256'] != evidence['target_sha256']):
                raise ValueError(f'Lineage target {current_parent.pk} changed after preflight; transaction aborted.')
            if not current_fp['sha256'] == evidence['source_sha256'] and not approval_note.strip():
                raise ValueError('Lineage equivalence changed after preflight; transaction aborted.')
        has_new_versions = any(not item['already_imported'] for item in counts)
        if lineage_evidence and has_new_versions:
            from audit.models import AuditEvent
            for evidence in lineage_evidence:
                AuditEvent.objects.create(
                    event_type=('TIMETABLE_VERSION_LINEAGE_MAPPED' if evidence['academically_equivalent']
                                else 'TIMETABLE_VERSION_LINEAGE_OVERRIDE'),
                    actor_id=actor_id,
                    timetable_id=versions_payload[0]['timetable_id'],
                    version_id=versions_payload[0]['id'],
                    entity_type='TimetableVersionLineage', entity_id=versions_payload[0]['id'],
                    metadata={'package_sha256': payload['sha256'], **evidence},
                )
        for row in versions_payload:
            mapped_timetable = mapped[f'timetable:{row["id"]}']
            creator_id = mapped[f'creator:{row["id"]}']
            prev = row.get('previous_version_id')
            if prev:
                prev = mapped.get(prev, prev)
            version, created = TimetableVersion.objects.get_or_create(
                pk=row['id'], defaults={
                    'timetable_id': mapped_timetable, 'version_no': row['version_no'],
                    'status': TimetableVersion.Status.DRAFT, 'previous_version_id': prev,
                    'created_by_id': creator_id, 'notes': row.get('notes', ''),
                    'submitted_by_id': None, 'submitted_at': None, 'approved_by_id': None,
                    'approved_at': None, 'rejected_by_id': None, 'rejected_at': None,
                    'rejection_reason': '', 'published_by_id': None, 'published_at': None,
                })
            if not created:
                continue
            entries = row['entries']
            for item in entries:
                ScheduleEntry.objects.create(
                    pk=item['id'], version=version,
                    section_id=explicit_mapping.get('academics.Section', {}).get(item['section_id'], item['section_id']),
                    course_offering_id=explicit_mapping.get('academics.CourseOffering', {}).get(item['course_offering_id'], item['course_offering_id']),
                    weekday=item['weekday'], start_slot_id=explicit_mapping.get('common.TimeSlot', {}).get(item['start_slot_id'], item['start_slot_id']),
                    block_length=item['block_length'],
                    room_id=explicit_mapping.get('rooms.Room', {}).get(item['room_id'], item['room_id']) if item['room_id'] else None,
                    entry_type=item['entry_type'], delivery_mode=item['delivery_mode'],
                    locked=item['locked'], note=item.get('note', ''),
                )
                for assignment in item['assignments']:
                    ScheduleEntryFaculty.objects.create(
                        pk=assignment['id'], schedule_entry_id=item['id'],
                        faculty_id=explicit_mapping.get('faculty.Faculty', {}).get(assignment['faculty_id'], assignment['faculty_id']),
                        role=assignment['role'],
                    )
        target_validation = []
        for row in versions_payload:
            imported = TimetableVersion.objects.get(pk=row['id'])
            actual_fp = version_semantic_fingerprint(imported)
            expected_fp = row['semantic_fingerprint']
            if actual_fp['count'] != expected_fp['count'] or actual_fp['sha256'] != expected_fp['sha256']:
                raise ValueError(f'Post-import semantic verification failed for version {row["id"]}; rolling back.')
            target_validation.append({'version_id': row['id'], 'report': _validation_summary(imported, expected_rows=len(row['entries']))})
        if _legacy_snapshot() != legacy_before:
            raise ValueError('v1-v5 preservation check failed; rolling back the entire import.')
    return {'mode': 'APPLIED', 'versions': counts,
            'legacy_v1_v5_sha256_before': legacy_before,
            'legacy_v1_v5_sha256_after': _legacy_snapshot(), 'warnings': warnings,
            'target_validation': target_validation,
            'ready': True}
