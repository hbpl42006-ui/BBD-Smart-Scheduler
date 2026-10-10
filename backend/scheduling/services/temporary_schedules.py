"""Published date overlays. Precedence: specific date, plan priority, recurrence, base.

Equal priority resource collisions are publication errors. Filters apply after
resolution so a displaced regular class releases its faculty and room.
"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from common.models import TimeSlot
from faculty.models import FacultyAvailability
from rooms.models import RoomAvailability
from scheduling.models import ScheduleEntry, TemporaryScheduleBlock, TemporarySchedulePlan


def parse_schedule_scope(request):
    mode = (request.query_params.get('schedule_mode') or 'regular').lower()
    if mode not in ('regular', 'effective', 'special'):
        raise ValueError('schedule_mode must be regular, effective or special.')
    try:
        day = date.fromisoformat(request.query_params['date']) if request.query_params.get('date') else academic_today()
    except ValueError:
        raise ValueError('date must be YYYY-MM-DD.') from None
    return mode, day


def academic_today():
    return timezone.now().astimezone(ZoneInfo(getattr(settings, 'ACADEMIC_TIME_ZONE', 'Asia/Kolkata'))).date()


def dates(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def slots(start, length):
    calendar = list(TimeSlot.objects.filter(template_id=start.template_id).order_by('order', 'pk'))
    index = next((i for i, item in enumerate(calendar) if item.pk == start.pk), None)
    if index is None or length < 1 or index + length > len(calendar):
        return []
    window = calendar[index:index + length]
    return [] if any(item.is_break for item in window) else window


def rank(block):
    return (int(block.specific_date is not None), block.plan.priority)


def base_entries(version, day):
    return list(ScheduleEntry.objects.filter(version=version, weekday=day.weekday()).select_related(
        'section__program', 'section__semester__session', 'course_offering__course', 'start_slot__template', 'room'
    ).prefetch_related('faculty_assignments__faculty__user', 'start_slot__template__slots'))


def active_blocks(version, day, include_plan=None):
    plans = TemporarySchedulePlan.objects.filter(base_timetable_version=version, status='PUBLISHED',
        start_date__lte=day, end_date__gte=day)
    if include_plan and include_plan.start_date <= day <= include_plan.end_date:
        plans = TemporarySchedulePlan.objects.filter(Q(pk__in=plans.values('pk')) | Q(pk=include_plan.pk))
    rows = TemporaryScheduleBlock.objects.filter(plan__in=plans).filter(
        Q(specific_date=day) | Q(specific_date__isnull=True, weekday=day.weekday())
    ).select_related('plan', 'section__program', 'section__semester__session', 'start_slot__template', 'room').prefetch_related('faculty__user')
    return sorted(rows, key=lambda block: (rank(block), str(block.pk)), reverse=True)


def resolved_objects(version, day, mode='effective', include_plan=None):
    base = base_entries(version, day)
    if mode == 'regular':
        return base, []
    chosen = []
    for block in active_blocks(version, day, include_plan):
        occupied = {slot.pk for slot in slots(block.start_slot, block.block_length)}
        if any(other.section_id == block.section_id and occupied & ids and rank(other) > rank(block)
               for other, ids in chosen):
            continue
        chosen.append((block, occupied))
    if mode == 'special':
        return [], chosen
    kept = [entry for entry in base if not any(block.section_id == entry.section_id and block.override_regular_class
        and {slot.pk for slot in slots(entry.start_slot, entry.block_length)} & ids for block, ids in chosen)]
    return kept, chosen


def base_row(entry):
    from reports.services.reporting import entry_row
    row = entry_row(entry)
    row.update(source='REGULAR', is_temporary=False, start_slot_id=str(entry.start_slot_id),
        template_id=str(entry.start_slot.template_id),
        occupied_slot_ids=[str(slot.pk) for slot in slots(entry.start_slot, entry.block_length)])
    return row


def block_row(block, day):
    from reports.services.reporting import DAYS, faculty_name
    occupied = slots(block.start_slot, block.block_length)
    faculty = list(block.faculty.all())
    return {'entry_id': f'temporary-{block.pk}', 'temporary_block_id': str(block.pk),
        'temporary_plan_id': str(block.plan_id), 'section_id': str(block.section_id),
        'section': str(block.section), 'program': block.section.program.name, 'year': block.section.year,
        'semester': block.section.semester.name, 'session': block.section.semester.session.name,
        'day': DAYS.get(day.weekday(), str(day.weekday())), 'weekday': day.weekday(), 'date': day.isoformat(),
        'time': block.start_slot.label, 'start_slot_id': str(block.start_slot_id),
        'template_id': str(block.start_slot.template_id), 'start_time': block.start_slot.start_time.strftime('%H:%M'),
        'end_time': occupied[-1].end_time.strftime('%H:%M') if occupied else block.start_slot.end_time.strftime('%H:%M'),
        'occupied_slot_ids': [str(slot.pk) for slot in occupied], 'block_length': block.block_length,
        'course_offering_id': None, 'course_code': block.event_type, 'course_name': block.title,
        'event_type': block.event_type, 'title': block.title,
        'faculty': [{'id': str(f.pk), 'name': faculty_name(f), 'role': 'TRAINER'} for f in faculty],
        'faculty_names': ', '.join(faculty_name(f) for f in faculty) or block.trainer_name,
        'room': block.room.code if block.room_id else '', 'room_id': str(block.room_id) if block.room_id else None,
        'entry_type': block.event_type, 'delivery_mode': block.delivery_mode, 'source': 'TEMPORARY',
        'is_temporary': True, 'override_regular_class': block.override_regular_class,
        'plan_title': block.plan.title, 'plan_start_date': block.plan.start_date.isoformat(),
        'plan_end_date': block.plan.end_date.isoformat()}


def resolve_effective_schedule(version, on_date, *, mode='effective', section_id=None, faculty_id=None, room_id=None):
    if not version:
        return []
    day = date.fromisoformat(on_date) if isinstance(on_date, str) else on_date
    base, blocks = resolved_objects(version, day, mode)
    rows = [base_row(entry) for entry in base] + [block_row(block, day) for block, _ in blocks]
    if section_id:
        rows = [row for row in rows if row['section_id'] == str(section_id)]
    if faculty_id:
        rows = [row for row in rows if any(f['id'] == str(faculty_id) for f in row['faculty'])]
    if room_id:
        rows = [row for row in rows if row['room_id'] == str(room_id)]
    return sorted(rows, key=lambda row: (row['start_time'], row['section'].casefold(), row['entry_id']))


def issue(code, message, day=None, block=None, **extra):
    return {'severity': 'ERROR', 'code': code, 'message': message,
        'date': day.isoformat() if day else None, 'weekday': day.weekday() if day else None,
        'temporary_block': str(block.pk) if block else None, 'block_id': str(block.pk) if block else None,
        'section': str(block.section_id) if block else None, **extra}


def validate_temporary_plan(plan):
    errors = []
    if plan.base_timetable_version.status != 'PUBLISHED':
        errors.append(issue('BASE_NOT_PUBLISHED', 'Base timetable version must be published.'))
    if plan.start_date > plan.end_date:
        return errors + [issue('INVALID_DATE_RANGE', 'Start date must be on or before end date.')]
    section_ids = set(plan.sections.values_list('pk', flat=True))
    blocks = list(plan.blocks.select_related('section', 'start_slot__template', 'room').prefetch_related('faculty'))
    if not blocks:
        errors.append(issue('NO_BLOCKS', 'Add at least one temporary block.'))
    for block in blocks:
        if block.section_id not in section_ids:
            errors.append(issue('SECTION_NOT_IN_PLAN', 'Block section is not selected.', block=block))
        if (block.specific_date is None) == (block.weekday is None):
            errors.append(issue('INVALID_RECURRENCE', 'Choose a date or a recurring weekday.', block=block))
        if block.specific_date and not plan.start_date <= block.specific_date <= plan.end_date:
            errors.append(issue('DATE_OUTSIDE_PLAN', 'Block date is outside plan range.', block=block))
        if block.weekday is not None and block.weekday not in range(5):
            errors.append(issue('INVALID_WEEKDAY', 'Recurring weekday must be Monday to Friday.', block=block))
        if not slots(block.start_slot, block.block_length):
            errors.append(issue('INVALID_SLOT_WINDOW', 'Block crosses a break or exceeds the slot calendar.', block=block))
        if block.weekday is not None and not any(d.weekday() == block.weekday for d in dates(plan.start_date, plan.end_date)):
            errors.append(issue('NO_OCCURRENCE', 'Recurring weekday does not occur in date range.', block=block))
    for day in dates(plan.start_date, plan.end_date):
        base, selected = resolved_objects(plan.base_timetable_version, day, include_plan=plan)
        for block, ids in [(b, ids) for b, ids in selected if b.plan_id == plan.pk and ids]:
            for entry in base:
                overlap = ids & {slot.pk for slot in slots(entry.start_slot, entry.block_length)}
                if not overlap:
                    continue
                if entry.section_id == block.section_id and not block.override_regular_class:
                    errors.append(issue('SECTION_CONFLICT', 'Regular class overlaps; enable override.', day, block,
                        entry_id=str(entry.pk), slot=str(next(iter(overlap)))))
                if block.room_id and entry.room_id == block.room_id:
                    errors.append(issue('ROOM_CONFLICT', 'Room is occupied by an effective regular class.', day, block,
                        room=str(block.room_id), slot=str(next(iter(overlap)))))
                common = set(block.faculty.values_list('pk', flat=True)) & set(entry.faculty_assignments.values_list('faculty_id', flat=True))
                if common:
                    errors.append(issue('FACULTY_CONFLICT', 'Trainer teaches an effective regular class.', day, block,
                        faculty=str(next(iter(common))), slot=str(next(iter(overlap)))))
            for other, other_ids in selected:
                overlap = ids & other_ids
                if other.pk == block.pk or not overlap:
                    continue
                if other.plan_id == plan.pk and other.pk > block.pk:
                    continue  # one issue per same-plan pair and date
                if other.section_id == block.section_id:
                    errors.append(issue('SAME_PRIORITY_CONFLICT' if rank(other) == rank(block) else 'SECTION_CONFLICT',
                        'Temporary activities overlap for a section.', day, block, slot=str(next(iter(overlap))), other_block=str(other.pk)))
                if block.room_id and block.room_id == other.room_id:
                    errors.append(issue('ROOM_CONFLICT', 'Room is occupied by another temporary activity.', day, block,
                        room=str(block.room_id), slot=str(next(iter(overlap))), other_block=str(other.pk)))
                common = set(block.faculty.values_list('pk', flat=True)) & set(other.faculty.values_list('pk', flat=True))
                if common:
                    errors.append(issue('FACULTY_CONFLICT', 'Trainer has another temporary activity.', day, block,
                        faculty=str(next(iter(common))), slot=str(next(iter(overlap))), other_block=str(other.pk)))
            if block.room_id:
                rules = RoomAvailability.objects.filter(room_id=block.room_id, time_slot_id__in=ids,
                    status__in=('BLOCKED', 'MAINTENANCE')).filter(
                    Q(date=day) | Q(date__isnull=True, weekday__isnull=True) | Q(date__isnull=True, weekday=day.weekday()))
                for rule in rules:
                    errors.append(issue('ROOM_UNAVAILABLE', rule.reason or f'Room is {rule.status.lower()}.', day, block,
                        room=str(block.room_id), slot=str(rule.time_slot_id)))
            for rule in FacultyAvailability.objects.filter(faculty__in=block.faculty.all(), weekday=day.weekday(),
                    time_slot_id__in=ids, is_available=False):
                errors.append(issue('FACULTY_UNAVAILABLE', getattr(rule, 'reason', '') or 'Trainer is unavailable.', day, block,
                    faculty=str(rule.faculty_id), slot=str(rule.time_slot_id)))
    return list({(e['code'], e['date'], e['block_id'], e.get('other_block'), e.get('slot')): e for e in errors}.values())


def plan_impact(plan):
    errors = validate_temporary_plan(plan)
    events, overridden, rooms, faculty, warnings = [], {}, set(), set(), []
    expected_by_course = {}
    included_sections = set(plan.sections.values_list('pk', flat=True))
    for day in dates(plan.start_date, plan.end_date):
        for entry in base_entries(plan.base_timetable_version, day):
            if entry.section_id in included_sections:
                key = (entry.section_id, entry.course_offering_id)
                bucket = expected_by_course.setdefault(key, {'section': str(entry.section),
                    'course': entry.course_offering.course.code, 'expected_regular_periods': 0,
                    'overridden_regular_periods': 0})
                bucket['expected_regular_periods'] += entry.block_length
        _, chosen = resolved_objects(plan.base_timetable_version, day, include_plan=plan)
        chosen_ids = {block.pk for block, _ in chosen}
        for candidate in active_blocks(plan.base_timetable_version, day, include_plan=plan):
            if candidate.plan_id != plan.pk:
                continue
            event = block_row(candidate, day)
            event['effective'] = candidate.pk in chosen_ids
            events.append(event)
            if candidate.pk not in chosen_ids:
                warning = issue('LOWER_PRIORITY_SUPPRESSED', 'A higher priority special activity replaces this block.', day, candidate)
                warning['severity'] = 'WARNING'
                warnings.append(warning)
        for block, ids in chosen:
            if block.plan_id != plan.pk:
                continue
            if block.room_id:
                rooms.add(block.room_id)
            faculty.update(block.faculty.values_list('pk', flat=True))
            if block.override_regular_class:
                for entry in base_entries(plan.base_timetable_version, day):
                    if entry.section_id == block.section_id and ids & {s.pk for s in slots(entry.start_slot, entry.block_length)}:
                        overridden[(day, entry.pk)] = {'date': day.isoformat(), 'section': str(entry.section),
                            'entry_id': str(entry.pk), 'course': entry.course_offering.course.code,
                            'faculty': ', '.join(str(a.faculty) for a in entry.faculty_assignments.all()),
                            'room': entry.room.code if entry.room_id else '', 'time': entry.start_slot.label,
                            'periods': entry.block_length}
    affected = list(overridden.values())
    for day, entry_pk in overridden:
        entry = ScheduleEntry.objects.get(pk=entry_pk)
        expected_by_course[(entry.section_id, entry.course_offering_id)]['overridden_regular_periods'] += entry.block_length
    event_days={event['date'] for event in events}
    temporary_period_instances=sum(len(event.get('occupied_slot_ids',[])) for event in events)
    return {'valid': not errors, 'plan': plan.title,
        'date_range': {'start': plan.start_date.isoformat(), 'end': plan.end_date.isoformat()},
        'summary': {'affected_sections': plan.sections.count(), 'training_days':len(event_days),
            'temporary_event_instances': len(events), 'temporary_section_period_instances':temporary_period_instances,
            'overridden_regular_classes': len(affected), 'overridden_regular_periods': sum(x['periods'] for x in affected),
            'rooms_used': len(rooms), 'faculty_trainers': len(faculty), 'blocking_errors': len(errors), 'warnings': len(warnings)},
        'affected_regular_periods': sum(x['periods'] for x in affected),
        'regular_periods_by_course': list(expected_by_course.values()),
        'affected_sections': [{'id': str(s.pk), 'name': s.name} for s in plan.sections.all()],
        'temporary_blocks': plan.blocks.count(), 'temporary_events': events,
        'overridden_classes': affected, 'conflicts': errors, 'warnings': warnings}
