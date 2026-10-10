from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace

from django.db.models import Count, Sum, Value, Q, Prefetch
from django.db.models.functions import Coalesce

from academics.models import CourseOffering, Program, Section, SectionDeliveryPolicy, SectionWeeklyOffPolicy
from academics.weekly_off import get_section_weekly_off_policy
from audit.models import AuditEvent
from common.models import TimeSlot, TimeSlotTemplate
from faculty.models import Faculty, FacultyAvailability
from rooms.models import Room, RoomAvailability
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, Timetable, TimetableVersion
from scheduling.weekdays import WORKING_DAYS
from faculty.models import FacultyArrangement
from scheduling.services.validation import validate_entry


DAYS = dict(ScheduleEntry.Weekday.choices)


def _version(request):
    faculty_user = request.user.role == 'FACULTY'
    requested = request.query_params.get('version') or request.query_params.get('version_id')
    if requested:
        qs = TimetableVersion.objects.select_related('timetable__academic_session', 'timetable__semester', 'timetable__department').filter(pk=requested)
        if faculty_user: qs = qs.filter(status='PUBLISHED')
        return qs.first()
    timetable = Timetable.objects.filter(pk=request.query_params.get('timetable')).first() if request.query_params.get('timetable') else None
    qs = TimetableVersion.objects.select_related('timetable__academic_session', 'timetable__semester', 'timetable__department')
    if timetable: qs = qs.filter(timetable=timetable)
    return qs.filter(status='PUBLISHED').order_by('-published_at', '-version_no').first()


def _entries(request, version=None):
    qs = ScheduleEntry.objects.filter(version=version).select_related(
        'section__program__department', 'section__semester__session',
        'course_offering__course', 'start_slot__template', 'room',
    ).prefetch_related('faculty_assignments__faculty__user', 'start_slot__template__slots')
    for field, key in (('section_id', 'section'), ('room_id', 'room'), ('course_offering__semester_id', 'semester')):
        if request.query_params.get(key): qs = qs.filter(**{field: request.query_params[key]})
    if request.query_params.get('program'): qs = qs.filter(section__program_id=request.query_params['program'])
    if request.query_params.get('department'): qs = qs.filter(section__program__department_id=request.query_params['department'])
    if request.query_params.get('session') or request.query_params.get('academic_session'): qs = qs.filter(section__semester__session_id=request.query_params.get('session') or request.query_params.get('academic_session'))
    if request.query_params.get('course'): qs = qs.filter(course_offering__course_id=request.query_params['course'])
    if request.query_params.get('faculty'): qs = qs.filter(faculty_assignments__faculty_id=request.query_params['faculty']).distinct()
    if request.query_params.get('year'): qs = qs.filter(section__year=request.query_params['year'])
    if request.query_params.get('weekday') not in (None, ''): qs = qs.filter(weekday=request.query_params['weekday'])
    return qs


def faculty_name(faculty):
    linked = f'{faculty.user.first_name} {faculty.user.last_name}'.strip() if faculty.user else ''
    return linked or faculty.name or faculty.initials or faculty.employee_code


def entry_row(entry):
    assignments = list(entry.faculty_assignments.all())
    faculties = [{'id': str(a.faculty_id), 'name': faculty_name(a.faculty), 'role': a.role} for a in assignments]
    template_slots = list(entry.start_slot.template.slots.all())
    usable_slots = [slot for slot in template_slots if not slot.is_break]
    start_index = next((index for index, slot in enumerate(usable_slots) if slot.pk == entry.start_slot_id), None)
    end_slot = usable_slots[min(start_index + entry.block_length, len(usable_slots)) - 1] if start_index is not None and usable_slots else entry.start_slot
    return {'entry_id': str(entry.pk), 'section_id': str(entry.section_id), 'section': str(entry.section), 'program': entry.section.program.name,
            'year': entry.section.year, 'semester': entry.section.semester.name, 'session': entry.section.semester.session.name, 'day': DAYS.get(entry.weekday, str(entry.weekday)),
            'weekday': entry.weekday, 'time': entry.start_slot.label, 'course_offering_id': str(entry.course_offering_id),
            'course_code': entry.course_offering.course.code, 'course_name': entry.course_offering.course.name,
            'faculty': faculties, 'faculty_names': ', '.join(x['name'] for x in faculties), 'room': entry.room.code if entry.room else '',
            'room_id': str(entry.room_id) if entry.room_id else None, 'entry_type': entry.entry_type, 'delivery_mode': entry.delivery_mode,
            'start_time': entry.start_slot.start_time.strftime('%H:%M'), 'end_time': end_slot.end_time.strftime('%H:%M'), 'block_length': entry.block_length}


def faculty_workload(request):
    if request.user.role == 'FACULTY' and getattr(request.user, 'faculty_profile', None): request = _with_query(request, faculty=str(request.user.faculty_profile.pk))
    version = _version(request); grouped = defaultdict(lambda: {'assigned_courses': set(), 'sections': set(), 'lecture_periods': 0, 'practical_periods': 0, 'total_scheduled_periods': 0})
    if version:
        for entry in _entries(request, version):
            for assignment in entry.faculty_assignments.all():
                row = grouped[assignment.faculty_id]; row['assigned_courses'].add(entry.course_offering.course.code); row['sections'].add(str(entry.section)); row['total_scheduled_periods'] += entry.block_length
                if entry.entry_type == 'PRACTICAL': row['practical_periods'] += entry.block_length
                else: row['lecture_periods'] += entry.block_length
    # Keep the report scoped to active faculty in the same academic/filter scope,
    # including faculty with no published entries so workload can be balanced.
    faculty_qs = Faculty.objects.select_related('user', 'department').filter(active=True)
    if request.query_params.get('faculty'): faculty_qs = faculty_qs.filter(pk=request.query_params['faculty'])
    if request.query_params.get('department'): faculty_qs = faculty_qs.filter(department_id=request.query_params['department'])
    rows = [{'faculty_id': str(f.pk), 'faculty_name': faculty_name(f), 'employee_code': f.employee_code, 'department': f.department.name,
             'assigned_courses': sorted(grouped[f.pk]['assigned_courses']), 'sections': sorted(grouped[f.pk]['sections']),
             'lecture_periods': grouped[f.pk]['lecture_periods'], 'practical_periods': grouped[f.pk]['practical_periods'],
             'total_scheduled_periods': grouped[f.pk]['total_scheduled_periods'], 'total_weekly_teaching_hours': grouped[f.pk]['total_scheduled_periods'],
             'distinct_courses': len(grouped[f.pk]['assigned_courses']), 'distinct_sections': len(grouped[f.pk]['sections'])} for f in faculty_qs]
    return sorted(rows, key=lambda row: (-row['total_scheduled_periods'], row['faculty_name'].lower()))

def _with_query(request, **values):
    from django.http import QueryDict
    query=QueryDict(mutable=True);query.update(request.query_params);query.update(values);request.query_params=query;return request


def room_utilization(request):
    version = _version(request)
    rooms = Room.objects.filter(active=True).order_by('code')
    if request.query_params.get('room'):
        rooms = rooms.filter(pk=request.query_params['room'])
    room_rows = list(rooms)
    room_ids = {room.pk for room in room_rows}
    entries = list(_entries(request, version)) if version else []

    # A version has no direct template FK; its schedule entries identify the
    # institution's actual templates. Resolve templates from the whole version
    # so section/faculty filters do not change the timetable's slot calendar.
    if version:
        template_ids = set(ScheduleEntry.objects.filter(version=version).values_list('start_slot__template_id', flat=True))
    else:
        template_ids = set()
    if not template_ids and version:
        template_ids = set(TimeSlotTemplate.objects.filter(
            institution_id=version.timetable.institution_id
        ).values_list('pk', flat=True))

    usable_by_template = defaultdict(list)
    for slot in TimeSlot.objects.filter(template_id__in=template_ids, is_break=False).order_by('template_id', 'order', 'pk'):
        usable_by_template[slot.template_id].append(slot)
    usable_slots = {slot.pk for slots in usable_by_template.values() for slot in slots}
    scoped_days = (int(request.query_params['weekday']),) if request.query_params.get('weekday') not in (None, '') else WORKING_DAYS
    base_room_periods = len(usable_slots) * len(scoped_days)

    blocked_periods = set()
    if room_ids and usable_slots:
        blocked_rows = RoomAvailability.objects.filter(
            room_id__in=room_ids,
            time_slot_id__in=usable_slots,
            status__in=(RoomAvailability.Status.BLOCKED, RoomAvailability.Status.MAINTENANCE),
            date__isnull=True,
        ).filter(Q(weekday__isnull=True) | Q(weekday__in=WORKING_DAYS))
        for block in blocked_rows:
            days = scoped_days if block.weekday is None else (block.weekday,) if block.weekday in scoped_days else ()
            blocked_periods.update((block.room_id, day, block.time_slot_id) for day in days)

    occupied_periods = set()
    used_entries = defaultdict(list)
    for entry in entries:
        if entry.room_id not in room_ids:
            continue
        used_entries[entry.room_id].append(entry)
        slots = usable_by_template.get(entry.start_slot.template_id, [])
        start_index = next((index for index, slot in enumerate(slots) if slot.pk == entry.start_slot_id), None)
        if start_index is None:
            continue
        occupied_periods.update(
            (entry.room_id, entry.weekday, slot.pk)
            for slot in slots[start_index:start_index + entry.block_length]
        )

    rows=[]
    for room in room_rows:
        blocked = sum(1 for room_id, _, _ in blocked_periods if room_id == room.pk)
        total = max(base_room_periods - blocked, 0)
        occupied = sum(1 for room_id, _, _ in occupied_periods if room_id == room.pk)
        room_entries = used_entries[room.pk]
        rows.append({'room_id':str(room.pk),'room_no':room.code,'building':room.building or 'Unknown','room_type':room.get_room_type_display(),'capacity':room.capacity,'total_available_slots':total,'occupied_slots':occupied,'free_slots':max(total-occupied,0),'utilization_percentage':round(occupied*100/total,2) if total else 0,'courses':sorted({e.course_offering.course.code for e in room_entries}),'sections':sorted({str(e.section) for e in room_entries})})
    return rows


def section_timetable(request):
    version=_version(request); return [entry_row(e) for e in _entries(request,version)] if version else []


def _section_candidates(version):
    if not version:
        return Section.objects.none()
    scheduled_ids = ScheduleEntry.objects.filter(version=version).values('section_id')
    return Section.objects.filter(
        Q(pk__in=scheduled_ids) |
        Q(semester_id=version.timetable.semester_id, program__department_id=version.timetable.department_id)
    ).select_related('program__department', 'semester__session').prefetch_related(
        Prefetch('weekly_off_policies', queryset=SectionWeeklyOffPolicy.objects.filter(is_active=True))
    ).distinct()


def _filtered_section_candidates(request, version, include_section=True):
    sections = _section_candidates(version)
    params = request.query_params
    if params.get('program'):
        sections = sections.filter(program_id=params['program'])
    if params.get('year'):
        sections = sections.filter(year=params['year'])
    if params.get('semester'):
        sections = sections.filter(semester_id=params['semester'])
    session_id = params.get('session') or params.get('academic_session')
    if session_id:
        sections = sections.filter(semester__session_id=session_id)
    if include_section and params.get('section'):
        sections = sections.filter(pk=params['section'])
    return sections.order_by('program__name', 'year', 'name', 'pk')


def section_timetable_options(request):
    version = _version(request)
    base = _section_candidates(version)
    filtered = _filtered_section_candidates(request, version, include_section=False)
    section_rows = [{'id': str(section.pk), 'name': section.name,
                     'label': f'{section.name} — {section.program.name} — Year {section.year}'}
                    for section in filtered]
    programs = base.order_by('program__name').values('program_id', 'program__name').distinct()
    semesters = base.order_by('semester__number', 'semester__name').values('semester_id', 'semester__name').distinct()
    sessions = base.order_by('semester__session__name').values('semester__session_id', 'semester__session__name').distinct()
    return {'sections': section_rows,
            'programs': [{'id': str(row['program_id']), 'name': row['program__name']} for row in programs],
            'years': sorted({section.year for section in base}),
            'semesters': [{'id': str(row['semester_id']), 'name': row['semester__name']} for row in semesters],
            'sessions': [{'id': str(row['semester__session_id']), 'name': row['semester__session__name']} for row in sessions]}


def section_timetable_visual(request):
    version = _version(request)
    if not version:
        return {'version': None, 'selected_section': None, 'summary': None, 'entries': [], 'sections': [],
                'slots': [], 'course_load': [], 'faculty_load': [], 'room_load': [], 'day_load': [], 'weekly_off': None}

    candidates = _filtered_section_candidates(request, version)
    section_objects = list(candidates)
    section_by_id = {str(section.pk): section for section in section_objects}
    selected_section = section_by_id.get(str(request.query_params.get('section', '')))
    entries_qs = _entries(request, version)
    entry_objects = list(entries_qs)
    # Include any section present in the version even when it falls outside the
    # timetable's department/semester master-data defaults.
    for entry in entry_objects:
        section_by_id.setdefault(str(entry.section_id), entry.section)
    if not selected_section and request.query_params.get('section'):
        selected_section = section_by_id.get(str(request.query_params['section']))
    if selected_section:
        entry_objects = [entry for entry in entry_objects if entry.section_id == selected_section.pk]
        section_by_id = {str(selected_section.pk): selected_section}

    offerings = list(CourseOffering.objects.filter(section_id__in=[section.pk for section in section_by_id.values()],
        semester_id=version.timetable.semester_id, active=True).select_related('course', 'section'))
    offerings_by_section = defaultdict(list)
    for offering in offerings:
        offerings_by_section[str(offering.section_id)].append(offering)
    delivery_policies = {str(policy.section_id): policy for policy in SectionDeliveryPolicy.objects.filter(
        section_id__in=[section.pk for section in section_by_id.values()],
        academic_session=version.timetable.academic_session,
        semester=version.timetable.semester,
        active=True,
    )}

    entries_by_section = defaultdict(list)
    for entry in entry_objects:
        entries_by_section[str(entry.section_id)].append(entry)

    template_ids = set(ScheduleEntry.objects.filter(version=version).values_list('start_slot__template_id', flat=True))
    if not template_ids:
        template_ids = set(TimeSlotTemplate.objects.filter(institution_id=version.timetable.institution_id).values_list('pk', flat=True))
    slots = list(TimeSlot.objects.filter(template_id__in=template_ids).order_by('template_id', 'order', 'pk'))
    selected_entries = entries_by_section.get(str(selected_section.pk), []) if selected_section else []
    selected_template = selected_entries[0].start_slot.template_id if selected_entries else (str(slots[0].template_id) if slots else None)
    section_slots = [slot for slot in slots if str(slot.template_id) == str(selected_template)]
    usable_slots = [slot for slot in section_slots if not slot.is_break]
    break_slots = [slot for slot in section_slots if slot.is_break]

    selected_policy = get_section_weekly_off_policy(selected_section, version.timetable.academic_session, version.timetable.semester) if selected_section else None
    weekly_off = None
    off_weekday = None
    if selected_policy:
        weekly_off = {'policy_type': selected_policy.policy_type,
                      'day': selected_policy.get_weekday_display() if selected_policy.weekday is not None else None,
                      'label': selected_policy.get_weekday_display() if selected_policy.policy_type == SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF else 'No weekly off'}
        if selected_policy.policy_type == SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF:
            off_weekday = selected_policy.weekday

    serialized_entries = []
    for entry in selected_entries:
        row = entry_row(entry)
        calendar = [slot for slot in entry.start_slot.template.slots.all() if not slot.is_break]
        start_index = next((index for index, slot in enumerate(calendar) if slot.pk == entry.start_slot_id), None)
        occupied = calendar[start_index:start_index + entry.block_length] if start_index is not None else [entry.start_slot]
        end_slot = occupied[-1] if occupied else entry.start_slot
        row.update({'start_slot_id': str(entry.start_slot_id), 'template_id': str(entry.start_slot.template_id),
                    'start_time': entry.start_slot.start_time.strftime('%H:%M'), 'end_time': end_slot.end_time.strftime('%H:%M'),
                    'year': entry.section.year, 'delivery_mode': entry.delivery_mode,
                    'occupied_slot_ids': [str(slot.pk) for slot in occupied]})
        serialized_entries.append(row)

    scoped_days = (int(request.query_params['weekday']),) if request.query_params.get('weekday') not in (None, '') else WORKING_DAYS
    schedule_days = [day for day in scoped_days if day != off_weekday]
    day_load = []
    for day in scoped_days:
        periods = sum(entry.block_length for entry in selected_entries if entry.weekday == day)
        day_load.append({'weekday': day, 'day': DAYS.get(day, str(day)), 'periods': periods,
                         'weekly_off': day == off_weekday})

    selected_course_load = []
    scheduled_by_offering = defaultdict(int)
    for entry in selected_entries:
        scheduled_by_offering[entry.course_offering_id] += entry.block_length
    for offering in offerings_by_section.get(str(selected_section.pk), []) if selected_section else []:
        scheduled = scheduled_by_offering[offering.pk]
        required = offering.weekly_periods
        selected_course_load.append({'course_id': str(offering.course_id), 'course_code': offering.course.code,
            'course_name': offering.course.name, 'required_periods': required, 'scheduled_periods': scheduled,
            'difference': required - scheduled, 'status': 'Complete' if scheduled >= required else f'{required - scheduled} period(s) remaining'})
    selected_course_load.sort(key=lambda row: row['course_code'])

    faculty_periods = defaultdict(int)
    faculty_names = {}
    room_periods = defaultdict(int)
    room_names = {}
    distinct_faculty = set()
    distinct_courses = set()
    offline_periods = online_periods = scheduled_periods = 0
    for entry in selected_entries:
        scheduled_periods += entry.block_length
        distinct_courses.add(entry.course_offering.course_id)
        if entry.delivery_mode == ScheduleEntry.DeliveryMode.ONLINE:
            online_periods += entry.block_length
        else:
            offline_periods += entry.block_length
        if entry.room_id:
            room_periods[entry.room_id] += entry.block_length
            room_names[entry.room_id] = entry.room.code
        for assignment in entry.faculty_assignments.all():
            faculty_id = str(assignment.faculty_id)
            distinct_faculty.add(faculty_id)
            faculty_periods[faculty_id] += entry.block_length
            faculty_names[faculty_id] = faculty_name(assignment.faculty)

    faculty_load = [{'faculty_id': faculty_id, 'faculty_name': faculty_names[faculty_id], 'periods': periods}
                    for faculty_id, periods in sorted(faculty_periods.items(), key=lambda item: (-item[1], faculty_names[item[0]].casefold()))]
    room_load = [{'room_id': str(room_id), 'room': room_names[room_id], 'periods': periods}
                 for room_id, periods in sorted(room_periods.items(), key=lambda item: (-item[1], room_names[item[0]].casefold()))]
    teaching_capacity = len(usable_slots) * len(schedule_days)
    weekly_off_periods = len(usable_slots) if off_weekday is not None and off_weekday in scoped_days else 0
    free_periods = max(teaching_capacity - scheduled_periods, 0)
    break_period_count = len(break_slots) * len(scoped_days)
    summary = None
    if selected_section:
        summary = {'scheduled_classes': len(selected_entries), 'scheduled_periods': scheduled_periods,
            'distinct_courses': len(distinct_courses), 'distinct_faculty': len(distinct_faculty),
            'rooms_used': len(room_periods), 'weekly_off': weekly_off['label'] if weekly_off else 'Not specified',
            'online_periods': online_periods, 'offline_periods': offline_periods,
            'free_periods': free_periods, 'break_periods': break_period_count,
            'weekly_off_periods': weekly_off_periods, 'available_teaching_periods': teaching_capacity}

    section_rows = []
    all_faculty_ids = set()
    all_course_ids = set()
    all_periods = all_classes = 0
    sections_complete = sections_with_gaps = 0
    aggregate_day = defaultdict(int)
    for section_id, section in section_by_id.items():
        section_entries = entries_by_section.get(section_id, [])
        periods = sum(entry.block_length for entry in section_entries)
        courses = {entry.course_offering.course_id for entry in section_entries}
        faculty_ids = {assignment.faculty_id for entry in section_entries for assignment in entry.faculty_assignments.all()}
        required = sum(offering.weekly_periods for offering in offerings_by_section.get(section_id, []))
        gaps = max(required - periods, 0)
        complete = required > 0 and periods >= required
        sections_complete += int(complete)
        sections_with_gaps += int(gaps > 0)
        all_classes += len(section_entries)
        all_periods += periods
        all_course_ids.update(courses)
        all_faculty_ids.update(faculty_ids)
        for entry in section_entries:
            aggregate_day[entry.weekday] += entry.block_length
        policy = get_section_weekly_off_policy(section, version.timetable.academic_session, version.timetable.semester)
        section_off = policy.weekday if policy and policy.policy_type == SectionWeeklyOffPolicy.PolicyType.WEEKLY_OFF else None
        available_days = [day for day in scoped_days if day != section_off]
        capacity = len(usable_slots) * len(available_days)
        section_rows.append({'section_id': section_id, 'section': section.name,
            'program': section.program.name, 'program_code': section.program.code, 'year': section.year,
            'semester': section.semester.name, 'semester_id': str(section.semester_id),
            'session': section.semester.session.name, 'session_id': str(section.semester.session_id),
            'delivery_policy': delivery_policies[section_id].mode if section_id in delivery_policies else section.delivery_policy,
            'offline_weekday': delivery_policies[section_id].offline_weekday if section_id in delivery_policies else section.offline_weekday,
            'student_strength': section.student_strength, 'scheduled_classes': len(section_entries),
            'scheduled_periods': periods, 'required_periods': required,
            'distinct_courses': len(courses), 'distinct_faculty': len(faculty_ids),
            'free_periods': max(capacity - periods, 0), 'weekly_off': policy.get_weekday_display() if policy and policy.weekday is not None else None,
            'utilization_percentage': round(periods * 100 / capacity, 2) if capacity else 0,
            'scheduling_gap': gaps, 'complete': complete})
    section_rows.sort(key=lambda row: (-row['scheduled_periods'], row['program'].casefold(), row['section'].casefold()))

    if not selected_section:
        all_faculty_ids = {assignment.faculty_id for entry in entry_objects for assignment in entry.faculty_assignments.all()}
        all_course_ids = {entry.course_offering.course_id for entry in entry_objects}
        all_classes = len(entry_objects)
        all_periods = sum(entry.block_length for entry in entry_objects)

    return {'version': {'id': str(version.pk), 'title': version.timetable.title,
            'session': version.timetable.academic_session.name, 'semester': version.timetable.semester.name},
        'selected_section': ({'id': str(selected_section.pk), 'name': selected_section.name,
            'program': selected_section.program.name, 'program_code': selected_section.program.code,
            'year': selected_section.year, 'semester': selected_section.semester.name,
            'semester_number': selected_section.semester.number, 'session': selected_section.semester.session.name,
            'student_strength': selected_section.student_strength,
            'delivery_policy': delivery_policies[str(selected_section.pk)].mode if str(selected_section.pk) in delivery_policies else selected_section.delivery_policy,
            'offline_weekday': delivery_policies[str(selected_section.pk)].offline_weekday if str(selected_section.pk) in delivery_policies else selected_section.offline_weekday} if selected_section else None),
        'summary': summary if selected_section else {'total_sections': len(section_rows), 'scheduled_classes': all_classes,
            'scheduled_periods': all_periods, 'distinct_courses': len(all_course_ids), 'distinct_faculty': len(all_faculty_ids),
            'sections_complete': sections_complete, 'sections_with_gaps': sections_with_gaps},
        'entries': serialized_entries if selected_section else [], 'sections': section_rows,
        'slots': [{'id': str(slot.pk), 'template_id': str(slot.template_id), 'label': slot.label,
                   'start_time': slot.start_time.strftime('%H:%M'), 'end_time': slot.end_time.strftime('%H:%M'),
                   'order': slot.order, 'is_break': slot.is_break} for slot in section_slots],
        'course_load': selected_course_load,
        'faculty_load': faculty_load,
        'room_load': room_load,
        'day_load': day_load if selected_section else [{'weekday': day, 'day': DAYS.get(day, str(day)),
            'periods': aggregate_day.get(day, 0)} for day in scoped_days],
        'weekly_off': weekly_off,
        'delivery_mode_load': {'OFFLINE': offline_periods, 'ONLINE': online_periods},
        'calendar': {'working_days': list(WORKING_DAYS), 'teaching_slots_per_day': len(usable_slots),
                     'break_slots_per_day': len(break_slots), 'weekly_off_weekday': off_weekday}}


def faculty_timetable(request):
    """Return audit-friendly rows for the selected faculty from the canonical scope."""
    faculty_id = request.query_params.get('faculty')
    if request.user.role == 'FACULTY':
        faculty_id = getattr(getattr(request.user, 'faculty_profile', None), 'pk', None)
    if not faculty_id:
        return []
    version = _version(request)
    faculty = Faculty.objects.select_related('department').filter(pk=faculty_id).first()
    if not version or not faculty:
        return []
    rows = [entry_row(entry) for entry in _entries(request, version).filter(
        faculty_assignments__faculty_id=faculty_id
    ).distinct()]
    for row in rows:
        row.update({'employee_code': faculty.employee_code or '', 'department': faculty.department.name})
    return rows


def faculty_timetable_visual(request):
    """Build all-faculty analytics or one faculty's detail from one filtered entry set."""
    version = _version(request)
    requested_faculty = request.query_params.get('faculty')
    if request.user.role == 'FACULTY':
        requested_faculty = getattr(getattr(request.user, 'faculty_profile', None), 'pk', None)

    empty = {'version': None, 'selected_faculty': None, 'summary': None, 'entries': [], 'faculties': [],
             'slots': [], 'day_load': [], 'course_load': [], 'section_load': [], 'room_load': [],
             'workload_distribution': [], 'availability': {'configured': False, 'message': 'No published timetable is available.'}}
    if not version:
        return empty

    # The faculty filter is applied here, after collecting assignment groups,
    # so selecting a faculty does not exclude their team-taught classes.
    params = request.query_params.copy()
    if request.query_params.get('faculty'):
        params.pop('faculty', None)
    scoped_request = SimpleNamespace(user=request.user, query_params=params)
    scoped_entries = list(_entries(scoped_request, version))
    assignment_rows = ScheduleEntryFaculty.objects.filter(
        schedule_entry__in=[entry.pk for entry in scoped_entries]
    ).select_related('faculty__user', 'faculty__department', 'schedule_entry__course_offering__course',
                     'schedule_entry__section__program', 'schedule_entry__section__semester__session',
                     'schedule_entry__start_slot__template', 'schedule_entry__room')

    # A version has no direct slot-template relation. Resolve its teaching
    # calendar from the full version, regardless of report filters.
    template_ids = set(ScheduleEntry.objects.filter(version=version).values_list('start_slot__template_id', flat=True))
    if not template_ids:
        template_ids = set(TimeSlotTemplate.objects.filter(institution_id=version.timetable.institution_id).values_list('pk', flat=True))
    templates = list(TimeSlotTemplate.objects.filter(pk__in=template_ids).order_by('name', 'pk'))
    slots_by_template = {template.pk: list(TimeSlot.objects.filter(template=template).order_by('order', 'pk')) for template in templates}

    by_faculty = defaultdict(list)
    assignments_by_entry = defaultdict(list)
    faculty_objects = {}
    for assignment in assignment_rows:
        faculty = assignment.faculty
        faculty_objects[faculty.pk] = faculty
        if all(item.pk != assignment.schedule_entry_id for item in by_faculty[faculty.pk]):
            by_faculty[faculty.pk].append(assignment.schedule_entry)
        assignments_by_entry[assignment.schedule_entry_id].append(assignment)

    if requested_faculty:
        selected = Faculty.objects.select_related('user', 'department').filter(pk=requested_faculty).first()
        selected_entries = list({entry.pk: entry for entry in by_faculty.get(selected.pk, [])}.values()) if selected else []
        faculty_scope = [selected] if selected else []
    else:
        selected = None
        selected_entries = []
        # Keep overview zero-load counts scoped to the current timetable's institution
        # and selected department/program/session/semester filters.
        faculty_scope_qs = Faculty.objects.filter(department__institution=version.timetable.institution).filter(
            Q(active=True) | Q(pk__in=by_faculty.keys())
        )
        for key, field in (('department', 'department_id'),):
            if request.query_params.get(key):
                faculty_scope_qs = faculty_scope_qs.filter(**{field: request.query_params[key]})
        program_id = request.query_params.get('program')
        if program_id:
            department_ids = Program.objects.filter(pk=program_id).values_list('department_id', flat=True)
            faculty_scope_qs = faculty_scope_qs.filter(department_id__in=department_ids)
        faculty_scope = list(faculty_scope_qs.select_related('user', 'department').order_by('name', 'employee_code', 'pk'))
        faculty_objects.update({faculty.pk: faculty for faculty in faculty_scope})

    selected_ids = {entry.pk for entry in selected_entries}
    selected_rows = [entry for entry in scoped_entries if entry.pk in selected_ids]
    scoped_days = (int(request.query_params['weekday']),) if request.query_params.get('weekday') not in (None, '') else WORKING_DAYS
    day_names = [DAYS.get(day, str(day)) for day in scoped_days]

    def faculty_label(faculty):
        return faculty_name(faculty) or 'Unnamed faculty'

    def entry_payload(entry, include_faculty=True):
        row = entry_row(entry)
        calendar = slots_by_template.get(entry.start_slot.template_id, [])
        usable = [slot for slot in calendar if not slot.is_break]
        start = next((index for index, slot in enumerate(usable) if slot.pk == entry.start_slot_id), None)
        occupied = usable[start:start + entry.block_length] if start is not None else [entry.start_slot]
        end = occupied[-1] if occupied else entry.start_slot
        assigned = assignments_by_entry.get(entry.pk, [])
        faculty_conflict = False
        if selected:
            occupied_ids = {slot.pk for slot in occupied}
            faculty_conflict = FacultyAvailability.objects.filter(
                faculty=selected, weekday=entry.weekday, time_slot_id__in=occupied_ids, is_available=False
            ).exists()
        row.update({'start_slot_id': str(entry.start_slot_id), 'template_id': str(entry.start_slot.template_id),
                    'occupied_slot_ids': [str(slot.pk) for slot in occupied],
                    'availability_conflict': faculty_conflict,
                    'faculty_names': ', '.join(faculty_label(item.faculty) for item in assigned) if include_faculty else '',
                    'end_time': end.end_time.strftime('%H:%M')})
        return row

    def summarize(entries):
        courses, sections, physical_rooms, days = set(), set(), set(), set()
        online_periods = offline_periods = periods = 0
        daily = defaultdict(int)
        course_data, section_data, room_data = {}, {}, {}
        for entry in entries:
            course = entry.course_offering.course
            section = entry.section
            length = entry.block_length
            periods += length
            days.add(entry.weekday)
            daily[entry.weekday] += length
            courses.add(course.pk)
            sections.add(section.pk)
            if entry.delivery_mode == ScheduleEntry.DeliveryMode.ONLINE:
                online_periods += length
                room_key, room_label = 'online', 'ONLINE'
            else:
                offline_periods += length
                room_key, room_label = (entry.room_id or 'unassigned'), entry.room.code if entry.room_id else 'Room not assigned'
                if entry.room_id:
                    physical_rooms.add(entry.room_id)
            c = course_data.setdefault(course.pk, {'course_id': str(course.pk), 'course_code': course.code, 'course_name': course.name, 'periods': 0, 'sections': set()})
            c['periods'] += length
            c['sections'].add(section.pk)
            s = section_data.setdefault(section.pk, {'section_id': str(section.pk), 'section': section.name, 'program': section.program.name, 'year': section.year, 'periods': 0})
            s['periods'] += length
            room = room_data.setdefault(room_key, {'room_id': str(room_key) if entry.room_id else None, 'room': room_label, 'periods': 0, 'delivery_mode': 'ONLINE' if room_key == 'online' else 'OFFLINE'})
            room['periods'] += length
        days_with_teaching = [day for day in scoped_days if daily.get(day, 0)]
        busiest = max(days_with_teaching, key=lambda day: (daily[day], -scoped_days.index(day))) if days_with_teaching else None
        lightest = min(days_with_teaching, key=lambda day: (daily[day], scoped_days.index(day))) if days_with_teaching else None
        return {'scheduled_classes': len(entries), 'teaching_periods': periods, 'distinct_courses': len(courses),
                'distinct_sections': len(sections), 'rooms_used': len(physical_rooms), 'teaching_days': len(days),
                'online_periods': online_periods, 'offline_periods': offline_periods,
                'busiest_day': {'weekday': busiest, 'day': DAYS.get(busiest) if busiest is not None else None, 'periods': daily[busiest]} if busiest is not None else None,
                'lightest_teaching_day': {'weekday': lightest, 'day': DAYS.get(lightest) if lightest is not None else None, 'periods': daily[lightest]} if lightest is not None else None,
                'day_load': [{'weekday': day, 'day': DAYS.get(day, str(day)), 'periods': daily.get(day, 0)} for day in scoped_days],
                'course_load': [{'course_id': row['course_id'], 'course_code': row['course_code'], 'course_name': row['course_name'], 'periods': row['periods'], 'sections': len(row['sections'])} for row in sorted(course_data.values(), key=lambda x: x['course_code'].casefold())],
                'section_load': sorted(section_data.values(), key=lambda x: (-x['periods'], x['section'].casefold())),
                'room_load': sorted(room_data.values(), key=lambda x: (-x['periods'], x['room'].casefold()))}

    faculty_rows = []
    all_periods = all_courses = 0
    distribution = [0, 0, 0, 0, 0, 0]
    for faculty in faculty_scope:
        entries = list({entry.pk: entry for entry in by_faculty.get(faculty.pk, [])}.values())
        metric = summarize(entries)
        all_periods += metric['teaching_periods']
        all_courses += metric['distinct_courses']
        count = metric['teaching_periods']
        bucket = 0 if count == 0 else 1 if count <= 5 else 2 if count <= 10 else 3 if count <= 15 else 4 if count <= 20 else 5
        distribution[bucket] += 1
        faculty_rows.append({'faculty_id': str(faculty.pk), 'faculty_name': faculty_label(faculty),
                             'employee_code': faculty.employee_code or '', 'department': faculty.department.name,
                             'teaching_periods': count, 'scheduled_classes': metric['scheduled_classes'],
                             'distinct_courses': metric['distinct_courses'], 'distinct_sections': metric['distinct_sections']})
    faculty_rows.sort(key=lambda row: (-row['teaching_periods'], row['faculty_name'].casefold(), row['faculty_id']))
    week_keys = ['0 periods', '1-5 periods', '6-10 periods', '11-15 periods', '16-20 periods', '21+ periods']
    overview = {'faculty_with_published_classes': sum(row['teaching_periods'] > 0 for row in faculty_rows),
                'total_teaching_periods': all_periods, 'average_teaching_load': round(all_periods / len(faculty_rows), 2) if faculty_rows else 0,
                'maximum_teaching_load': max((row['teaching_periods'] for row in faculty_rows), default=0),
                'faculty_with_zero_published_load': sum(row['teaching_periods'] == 0 for row in faculty_rows),
                'total_distinct_courses': len({entry.course_offering.course_id for entry in scoped_entries}),
                'workload_distribution': [{'range': label, 'faculty_count': count} for label, count in zip(week_keys, distribution)],
                'day_load': [{'weekday': day, 'day': DAYS.get(day, str(day)), 'periods': sum(entry.block_length for entry in scoped_entries if entry.weekday == day)} for day in scoped_days]}

    if not selected:
        return {'version': {'id': str(version.pk), 'title': version.timetable.title,
                'session': version.timetable.academic_session.name, 'semester': version.timetable.semester.name},
                'selected_faculty': None, 'summary': overview, 'entries': [], 'faculties': faculty_rows,
                'slots': [], 'day_load': overview['day_load'], 'course_load': [], 'section_load': [], 'room_load': [],
                'workload_distribution': overview['workload_distribution'],
                'availability': {'configured': False, 'message': 'Select a faculty member to see configured availability.'}}

    metrics = summarize(selected_rows)
    selected_slots = next((slots_by_template.get(entry.start_slot.template_id, []) for entry in selected_rows), None)
    if selected_slots is None:
        selected_slots = slots_by_template.get(templates[0].pk, []) if templates else []
    availability_rows = list(FacultyAvailability.objects.filter(faculty=selected, weekday__in=scoped_days,
        time_slot__template_id__in=template_ids).select_related('time_slot'))
    availability_map = {(row.weekday, row.time_slot_id): row.is_available for row in availability_rows}
    teaching_slots = [slot for slot in selected_slots if not slot.is_break]
    occupied = {(entry.weekday, slot_id) for entry in selected_rows for slot_id in entry_payload(entry)['occupied_slot_ids']}
    occupied = {(day, slot_id) for day, slot_id in occupied}
    availability_states = []
    for day in scoped_days:
        for slot in selected_slots:
            if slot.is_break:
                state = 'BREAK'
            elif (day, str(slot.pk)) in occupied:
                state = 'SCHEDULED'
            elif availability_map.get((day, slot.pk)) is False:
                state = 'UNAVAILABLE'
            elif availability_map.get((day, slot.pk)) is True:
                state = 'FREE'
            else:
                state = 'NOT_CONFIGURED'
            availability_states.append({'weekday': day, 'slot_id': str(slot.pk), 'state': state})
    availability_configured_count = len(availability_rows)
    total_grid_slots = len(scoped_days) * len(teaching_slots)
    availability = {'configured': availability_configured_count > 0,
                    'configuration_complete': availability_configured_count >= total_grid_slots if total_grid_slots else False,
                    'configured_slots': availability_configured_count, 'teaching_slots': total_grid_slots,
                    'free_periods': sum(state['state'] == 'FREE' for state in availability_states),
                    'unavailable_periods': sum(state['state'] == 'UNAVAILABLE' for state in availability_states),
                    'break_periods': sum(slot.is_break for slot in selected_slots) * len(scoped_days),
                    'message': 'Availability is incomplete; unconfigured slots are shown as Not configured.' if availability_configured_count < total_grid_slots else ''}
    metrics.update({'free_available_periods': availability['free_periods'],
                    'unavailable_periods': availability['unavailable_periods'],
                    'break_periods': availability['break_periods']})
    detail = {'id': str(selected.pk), 'name': faculty_label(selected), 'employee_code': selected.employee_code or '',
              'department': selected.department.name}
    return {'version': {'id': str(version.pk), 'title': version.timetable.title,
            'session': version.timetable.academic_session.name, 'semester': version.timetable.semester.name},
            'selected_faculty': detail, 'summary': metrics,
            'entries': [entry_payload(entry) for entry in selected_rows],
            'faculties': faculty_rows, 'slots': [{'id': str(slot.pk), 'template_id': str(slot.template_id),
                'label': slot.label, 'start_time': slot.start_time.strftime('%H:%M'), 'end_time': slot.end_time.strftime('%H:%M'),
                'order': slot.order, 'is_break': slot.is_break} for slot in selected_slots],
            'availability_states': availability_states, 'availability': availability,
            'day_load': metrics['day_load'], 'course_load': metrics['course_load'],
            'section_load': metrics['section_load'], 'room_load': metrics['room_load'],
            'workload_distribution': overview['workload_distribution']}


def room_timetable(request):
    """Room-only, human-readable row data used by the audit table and exports."""
    version = _version(request)
    entries = _entries(request, version).filter(room__isnull=False) if version else []
    return [entry_row(entry) for entry in entries]


def room_timetable_visual(request):
    """One canonical filtered payload for the room timetable UI and its analytics."""
    version = _version(request)
    rooms = list(Room.objects.filter(active=True).order_by('code'))
    utilization = room_utilization(request)
    utilization_by_id = {row['room_id']: row for row in utilization}
    if not version:
        return {'version': None, 'rooms': [], 'slots': [], 'entries': [], 'availability': [], 'summary': None}

    # Resolve the calendar from the entire published version, not only from the
    # filtered room/session subset, so filters never alter configured slot axes.
    template_ids = set(ScheduleEntry.objects.filter(version=version).values_list('start_slot__template_id', flat=True))
    if not template_ids:
        template_ids = set(TimeSlotTemplate.objects.filter(institution_id=version.timetable.institution_id).values_list('pk', flat=True))
    slots = list(TimeSlot.objects.filter(template_id__in=template_ids).order_by('template_id', 'order', 'pk'))
    slot_rows = [{'id': str(slot.pk), 'template_id': str(slot.template_id), 'label': slot.label,
                  'start_time': slot.start_time.strftime('%H:%M'), 'end_time': slot.end_time.strftime('%H:%M'),
                  'order': slot.order, 'is_break': slot.is_break} for slot in slots]
    usable_by_template = defaultdict(list)
    for slot in slots:
        if not slot.is_break:
            usable_by_template[slot.template_id].append(slot)

    entry_rows = []
    entries = _entries(request, version).filter(room__isnull=False)
    for entry in entries:
        calendar = usable_by_template.get(entry.start_slot.template_id, [])
        start_index = next((i for i, slot in enumerate(calendar) if slot.pk == entry.start_slot_id), None)
        occupied_ids = [str(slot.pk) for slot in calendar[start_index:start_index + entry.block_length]] if start_index is not None else [str(entry.start_slot_id)]
        row = entry_row(entry)
        row.update({'start_slot_id': str(entry.start_slot_id), 'start_time': entry.start_slot.start_time.strftime('%H:%M'),
                    'template_id': str(entry.start_slot.template_id),
                    'end_time': calendar[min((start_index or 0) + entry.block_length, len(calendar)) - 1].end_time.strftime('%H:%M') if calendar and start_index is not None else entry.start_slot.end_time.strftime('%H:%M'),
                    'year': entry.section.year, 'delivery_mode': entry.delivery_mode,
                    'occupied_slot_ids': occupied_ids})
        entry_rows.append(row)

    room_rows = []
    for room in rooms:
        usage = utilization_by_id.get(str(room.pk), {})
        room_rows.append({'room_id': str(room.pk), 'room_no': room.code, 'room_type': room.get_room_type_display(),
                          'building': room.building or 'Unknown', **usage})

    room_ids = [room.pk for room in rooms]
    usable_slot_ids = [slot.pk for slot in slots if not slot.is_break]
    blocked_rows = RoomAvailability.objects.filter(room_id__in=room_ids, time_slot_id__in=usable_slot_ids,
        status__in=(RoomAvailability.Status.BLOCKED, RoomAvailability.Status.MAINTENANCE), date__isnull=True
    ).filter(Q(weekday__isnull=True) | Q(weekday__in=WORKING_DAYS)).select_related('time_slot')
    availability = []
    for block in blocked_rows:
        scoped_days = (int(request.query_params['weekday']),) if request.query_params.get('weekday') not in (None, '') else WORKING_DAYS
        days = scoped_days if block.weekday is None else (block.weekday,) if block.weekday in scoped_days else ()
        availability.extend({'room_id': str(block.room_id), 'weekday': day, 'slot_id': str(block.time_slot_id),
                             'status': block.status, 'reason': block.reason} for day in days)

    selected = next((room for room in room_rows if request.query_params.get('room') == room['room_id']), None)
    selected_entries = [row for row in entry_rows if not selected or row['room_id'] == selected['room_id']]
    occupied = selected.get('occupied_slots', 0) if selected else sum(row.get('occupied_slots', 0) for row in room_rows)
    available = selected.get('total_available_slots', 0) if selected else sum(row.get('total_available_slots', 0) for row in room_rows)
    summary = {'room_id': selected['room_id'] if selected else None, 'room_no': selected['room_no'] if selected else None,
               'scheduled_classes': len(selected_entries), 'occupied_periods': occupied,
               'available_periods': available, 'utilization_percentage': selected.get('utilization_percentage', 0) if selected else (round(occupied * 100 / available, 2) if available else 0),
               'sections': len({row['section'] for row in selected_entries}), 'courses': len({row['course_code'] for row in selected_entries}),
               'total_rooms': len(room_rows), 'rooms_used': sum(bool(row.get('occupied_slots')) for row in room_rows),
               'busiest_room': max(room_rows, key=lambda row: row.get('occupied_slots', 0))['room_no'] if room_rows else None}
    return {'version': {'id': str(version.pk), 'title': version.timetable.title,
                        'session': version.timetable.academic_session.name, 'semester': version.timetable.semester.name},
            'rooms': room_rows, 'slots': slot_rows, 'entries': entry_rows,
            'availability': availability, 'summary': summary}


def scheduled_entry_analytics(request):
    """Aggregate the same canonical scheduled-entry queryset used by the report."""
    version = _version(request)
    entries = _entries(request, version) if version else ScheduleEntry.objects.none()
    weekday_counts = dict(entries.values('weekday').annotate(count=Count('pk')).values_list('weekday', 'count'))
    time_slot_rows = entries.values('start_slot_id', 'start_slot__label', 'start_slot__order').annotate(count=Count('pk')).order_by('start_slot__order', 'start_slot_id')
    time_slot_counts = {row['start_slot__label']: row['count'] for row in time_slot_rows}
    return entries, weekday_counts, time_slot_counts


def published_faculty_count(entries):
    """Count distinct faculty assigned to the report's scoped scheduled entries."""
    return ScheduleEntryFaculty.objects.filter(schedule_entry__in=entries).values(
        'faculty_id'
    ).distinct().count()


def students_by_year(request):
    """Return enrollment totals from the scoped Section rows, grouped by year."""
    sections = Section.objects.all()
    session_id = request.query_params.get('session') or request.query_params.get('academic_session')
    if session_id:
        sections = sections.filter(semester__session_id=session_id)
    if request.query_params.get('semester'):
        sections = sections.filter(semester_id=request.query_params['semester'])
    if request.query_params.get('program'):
        sections = sections.filter(program_id=request.query_params['program'])
    if request.query_params.get('department'):
        sections = sections.filter(program__department_id=request.query_params['department'])
    return sections.values('year').annotate(
        section_count=Count('pk'),
        student_count=Coalesce(Sum('student_strength'), Value(0)),
    ).order_by('year')


def course_allocation(request):
    """Course-offering requirements joined to placements in the canonical published version."""
    version = _version(request)
    params = request.query_params
    offerings = CourseOffering.objects.filter(active=True).select_related(
        'course', 'section__program__department', 'section__semester__session', 'semester__session', 'preferred_room'
    ).prefetch_related('faculties__faculty__user')

    # Default requirement scope matches the published timetable selected by
    # the report resolver; explicit academic filters may narrow/change it.
    if version:
        offerings = offerings.filter(semester_id=version.timetable.semester_id,
                                     semester__session_id=version.timetable.academic_session_id)
    if params.get('session') or params.get('academic_session'):
        offerings = offerings.filter(semester__session_id=params.get('session') or params.get('academic_session'))
    if params.get('semester'):
        offerings = offerings.filter(semester_id=params['semester'])
    if params.get('program'):
        offerings = offerings.filter(section__program_id=params['program'])
    if params.get('department'):
        offerings = offerings.filter(section__program__department_id=params['department'])
    if params.get('year'):
        offerings = offerings.filter(section__year=params['year'])
    if params.get('section'):
        offerings = offerings.filter(section_id=params['section'])
    if params.get('course'):
        offerings = offerings.filter(course_id=params['course'])
    if params.get('faculty'):
        offerings = offerings.filter(faculties__faculty_id=params['faculty'])
    if params.get('activity_type'):
        offerings = offerings.filter(default_class_type=params['activity_type'])

    offering_objects = list(offerings.distinct().order_by(
        'section__program__name', 'section__year', 'section__name', 'course__code', 'pk'
    ))
    offering_ids = [offering.pk for offering in offering_objects]
    # Faculty filtering chooses relevant offerings; it must not truncate the
    # actual placement periods for a team-taught offering.
    entry_params = params.copy()
    entry_params.pop('faculty', None)
    entry_params.pop('allocation_status', None)
    entry_params.pop('activity_type', None)
    entry_params.pop('search', None)
    scoped_request = SimpleNamespace(user=request.user, query_params=entry_params)
    entries = _entries(scoped_request, version).filter(course_offering_id__in=offering_ids) if version and offering_ids else ScheduleEntry.objects.none()
    actual_by_offering = defaultdict(lambda: {'periods': 0, 'classes': 0, 'rooms': set(), 'delivery_modes': set()})
    for entry in entries.select_related('room'):
        data = actual_by_offering[entry.course_offering_id]
        data['periods'] += entry.block_length
        data['classes'] += 1
        if entry.room_id:
            data['rooms'].add(entry.room.code)
        data['delivery_modes'].add(entry.delivery_mode)

    rows = []
    for offering in offering_objects:
        actual = actual_by_offering[offering.pk]
        scheduled = actual['periods']
        required = offering.weekly_periods
        difference = scheduled - required
        status = ('COMPLETE' if scheduled == required else 'OVER_SCHEDULED' if scheduled > required
                  else 'UNSCHEDULED' if scheduled == 0 and required > 0 else 'UNDER_SCHEDULED')
        assigned_faculty = sorted({faculty_name(assignment.faculty) for assignment in offering.faculties.all() if faculty_name(assignment.faculty)})
        completion = round(scheduled * 100 / required, 2) if required else None
        issues = []
        if not assigned_faculty:
            issues.append('Needs Faculty Assignment')
        if status == 'UNSCHEDULED':
            issues.append('No scheduled periods')
        elif status == 'UNDER_SCHEDULED':
            issues.append(f'{required - scheduled} period(s) remaining')
        elif status == 'OVER_SCHEDULED':
            issues.append(f'{difference} period(s) over requirement')
        rows.append({
            'course_offering_id': str(offering.pk), 'course_code': offering.course.code,
            'course_name': offering.course.name, 'section_id': str(offering.section_id),
            'section': offering.section.name, 'program_id': str(offering.section.program_id),
            'program': offering.section.program.name, 'department': offering.section.program.department.name,
            'year': offering.section.year, 'semester_id': str(offering.semester_id),
            'semester': offering.semester.name, 'session_id': str(offering.semester.session_id),
            'session': offering.semester.session.name, 'activity_type': offering.default_class_type,
            'activity_type_label': offering.get_default_class_type_display(),
            'required_periods': required, 'scheduled_periods': scheduled, 'scheduled_classes': actual['classes'],
            'difference': difference, 'completion_percentage': completion, 'status': status,
            'faculty': assigned_faculty, 'faculty_count': len(assigned_faculty),
            'preferred_room': offering.preferred_room.code if offering.preferred_room_id else '',
            'required_room_type': offering.room_type_requirement,
            'actual_rooms': sorted(actual['rooms']), 'delivery_modes': sorted(actual['delivery_modes']),
            'issues': issues,
        })

    status_order = {'UNSCHEDULED': 0, 'UNDER_SCHEDULED': 1, 'OVER_SCHEDULED': 2, 'COMPLETE': 3}
    rows.sort(key=lambda row: (status_order[row['status']], row['program'].casefold(), row['year'],
                               row['section'].casefold(), row['course_code'].casefold()))
    status_filter = params.get('allocation_status')
    if status_filter:
        rows = [row for row in rows if row['status'] == status_filter.upper()]
    query = (params.get('search') or '').strip().casefold()
    if query:
        rows = [row for row in rows if query in ' '.join((row['course_code'], row['course_name'], row['section'],
                 row['program'], ' '.join(row['faculty']))).casefold()]
    return rows


def course_allocation_visual(request):
    """A single filtered offering list and all dependent allocation analytics."""
    rows = course_allocation(request)
    statuses = ('COMPLETE', 'UNDER_SCHEDULED', 'UNSCHEDULED', 'OVER_SCHEDULED')
    required = sum(row['required_periods'] for row in rows)
    scheduled = sum(row['scheduled_periods'] for row in rows)
    covered = sum(min(row['scheduled_periods'], row['required_periods']) for row in rows)
    summary = {'total_offerings': len(rows), 'fully_scheduled': sum(row['status'] == 'COMPLETE' for row in rows),
               'under_scheduled': sum(row['status'] == 'UNDER_SCHEDULED' for row in rows),
               'unscheduled': sum(row['status'] == 'UNSCHEDULED' for row in rows),
               'over_scheduled': sum(row['status'] == 'OVER_SCHEDULED' for row in rows),
               'required_periods': required, 'scheduled_periods': scheduled,
               'coverage_percentage': round(covered * 100 / required, 2) if required else 0,
               'scheduled_classes': sum(row['scheduled_classes'] for row in rows)}
    by_program = defaultdict(lambda: {'offerings': 0, 'required_periods': 0, 'scheduled_periods': 0, **{status.lower(): 0 for status in statuses}})
    by_year = defaultdict(lambda: {'offerings': 0, 'required_periods': 0, 'scheduled_periods': 0, **{status.lower(): 0 for status in statuses}})
    by_activity = defaultdict(int)
    by_section = defaultdict(lambda: {'section_id': '', 'section': '', 'program': '', 'year': 0,
        'offering_count': 0, 'required_periods': 0, 'scheduled_periods': 0})
    faculty_allocations = defaultdict(lambda: {'offerings': set(), 'required_periods': 0})
    for row in rows:
        program = by_program[row['program']]
        year = by_year[row['year']]
        for group in (program, year):
            group['offerings'] += 1
            group['required_periods'] += row['required_periods']
            group['scheduled_periods'] += row['scheduled_periods']
            group[row['status'].lower()] += 1
        by_activity[row['activity_type_label']] += 1
        section = by_section[row['section_id']]
        section.update({'section_id': row['section_id'], 'section': row['section'], 'program': row['program'], 'year': row['year']})
        section['offering_count'] += 1
        section['required_periods'] += row['required_periods']
        section['scheduled_periods'] += row['scheduled_periods']
        for faculty in row['faculty']:
            faculty_allocations[faculty]['offerings'].add(row['course_offering_id'])
            faculty_allocations[faculty]['required_periods'] += row['required_periods']
    sections = []
    for item in by_section.values():
        item['difference'] = item['scheduled_periods'] - item['required_periods']
        item['completion_percentage'] = round(item['scheduled_periods'] * 100 / item['required_periods'], 2) if item['required_periods'] else None
        sections.append(item)
    sections.sort(key=lambda item: (-max(item['required_periods'] - item['scheduled_periods'], 0), item['program'].casefold(), item['year'], item['section'].casefold()))
    faculty_rows = [{'faculty': name, 'allocated_offerings': len(data['offerings']), 'required_periods': data['required_periods']} for name, data in faculty_allocations.items()]
    faculty_rows.sort(key=lambda item: (-item['allocated_offerings'], item['faculty'].casefold()))
    attention = [row for row in rows if row['status'] != 'COMPLETE' or 'Needs Faculty Assignment' in row['issues']]
    version = _version(request)
    return {'version': ({'id': str(version.pk), 'title': version.timetable.title,
                        'session': version.timetable.academic_session.name,
                        'semester': version.timetable.semester.name} if version else None),
            'summary': summary, 'offerings': rows, 'needs_attention': attention,
            'status_distribution': [{'status': status, 'count': sum(row['status'] == status for row in rows)} for status in statuses],
            'program_summary': [{'program': key, **value} for key, value in sorted(by_program.items(), key=lambda item: item[0].casefold())],
            'year_summary': [{'year': key, **value} for key, value in sorted(by_year.items())],
            'program_year_summary': [{'program': program, 'year': year, **value} for (program, year), value in sorted(
                _course_allocation_program_year(rows).items(), key=lambda item: (item[0][0].casefold(), item[0][1]))],
            'activity_summary': [{'activity_type': key, 'count': value} for key, value in sorted(by_activity.items())],
            'section_coverage': sections, 'faculty_allocations': faculty_rows,
            'scope': {'filters': {key: request.query_params.get(key, '') for key in
                     ('session', 'semester', 'program', 'year', 'section', 'course', 'faculty', 'allocation_status', 'activity_type')},
                     'activity_type_data_quality': 'Activity type is populated from CourseOffering.default_class_type.'}}


def _course_allocation_program_year(rows):
    result = defaultdict(lambda: {'offerings': 0, 'required_periods': 0, 'scheduled_periods': 0, **{status.lower(): 0 for status in ('COMPLETE', 'UNDER_SCHEDULED', 'UNSCHEDULED', 'OVER_SCHEDULED')}})
    for row in rows:
        group = result[(row['program'], row['year'])]
        group['offerings'] += 1
        group['required_periods'] += row['required_periods']
        group['scheduled_periods'] += row['scheduled_periods']
        group[row['status'].lower()] += 1
    return result


def course_allocation_options(request):
    version = _version(request)
    query = CourseOffering.objects.filter(active=True)
    if version:
        query = query.filter(semester_id=version.timetable.semester_id,
                             semester__session_id=version.timetable.academic_session_id)
    query = query.select_related('section__program', 'semester__session', 'course').prefetch_related('faculties__faculty')
    sections, courses, faculty = {}, {}, {}
    for offering in query.distinct():
        sections[str(offering.section_id)] = {'id': str(offering.section_id), 'name': offering.section.name,
            'label': f'{offering.section.name} — {offering.section.program.name} — Year {offering.section.year}'}
        courses[str(offering.course_id)] = {'id': str(offering.course_id), 'name': offering.course.code,
            'label': f'{offering.course.code} — {offering.course.name}'}
        for assignment in offering.faculties.all():
            name = faculty_name(assignment.faculty)
            faculty[str(assignment.faculty_id)] = {'id': str(assignment.faculty_id), 'name': name,
                'label': f'{name}{f" — {assignment.faculty.employee_code}" if assignment.faculty.employee_code else ""}'}
    return {'sections': sorted(sections.values(), key=lambda row: row['label'].casefold()),
            'courses': sorted(courses.values(), key=lambda row: row['label'].casefold()),
            'faculty': sorted(faculty.values(), key=lambda row: row['label'].casefold()),
            'years': sorted(query.values_list('section__year', flat=True).distinct()),
            'statuses': [{'id': value, 'name': label} for value, label in (
                ('COMPLETE', 'Complete'), ('UNDER_SCHEDULED', 'Under-Scheduled'),
                ('UNSCHEDULED', 'Unscheduled'), ('OVER_SCHEDULED', 'Over-Scheduled'))],
            'activity_types': [{'id': value, 'name': label} for value, label in CourseOffering.ClassType.choices]}


def unscheduled_analytics(request):
    """Gap rows plus scope-wide metrics so the all-clear state remains meaningful."""
    rows = course_allocation(request)
    incomplete = [row for row in rows if row['status'] in ('UNSCHEDULED', 'UNDER_SCHEDULED')]
    required = sum(row['required_periods'] for row in rows)
    scheduled = sum(row['scheduled_periods'] for row in rows)
    remaining = sum(max(row['required_periods'] - row['scheduled_periods'], 0) for row in rows)
    covered = sum(min(row['required_periods'], row['scheduled_periods']) for row in rows)
    by_section = defaultdict(lambda: {'section_id': '', 'section': '', 'program': '', 'year': 0, 'remaining_periods': 0, 'offerings': 0})
    by_program = defaultdict(int)
    by_year = defaultdict(int)
    by_activity = defaultdict(int)
    by_faculty = defaultdict(lambda: {'remaining_periods': 0, 'offerings': set()})
    for row in incomplete:
        gap = max(row['required_periods'] - row['scheduled_periods'], 0)
        section = by_section[row['section_id']]
        section.update({'section_id': row['section_id'], 'section': row['section'], 'program': row['program'], 'year': row['year']})
        section['remaining_periods'] += gap
        section['offerings'] += 1
        by_program[row['program']] += gap
        by_year[row['year']] += gap
        by_activity[row['activity_type_label']] += gap
        if not row['faculty']:
            by_faculty['No Faculty Assigned']['remaining_periods'] += gap
            by_faculty['No Faculty Assigned']['offerings'].add(row['course_offering_id'])
        for name in row['faculty']:
            by_faculty[name]['remaining_periods'] += gap
            by_faculty[name]['offerings'].add(row['course_offering_id'])
    by_section = sorted(by_section.values(), key=lambda item: (-item['remaining_periods'], item['program'].casefold(), item['year'], item['section'].casefold()))
    status_counts = {status: sum(row['status'] == status for row in rows) for status in ('COMPLETE', 'UNDER_SCHEDULED', 'UNSCHEDULED', 'OVER_SCHEDULED')}
    return {
        'summary': {'total_offerings': len(rows), 'incomplete_offerings': len(incomplete),
                    'unscheduled_offerings': status_counts['UNSCHEDULED'], 'partially_scheduled': status_counts['UNDER_SCHEDULED'],
                    'over_scheduled': status_counts['OVER_SCHEDULED'], 'required_periods': required,
                    'scheduled_periods': scheduled, 'remaining_periods': remaining,
                    'coverage_percentage': round(covered * 100 / required, 2) if required else 0,
                    'affected_sections': len(by_section)},
        'version': _version_label(request), 'offerings': incomplete,
        'status_distribution': [{'status': status, 'count': count} for status, count in status_counts.items()],
        'remaining_by_section': by_section,
        'remaining_by_program': [{'program': name, 'remaining_periods': count} for name, count in sorted(by_program.items())],
        'remaining_by_year': [{'year': year, 'remaining_periods': count} for year, count in sorted(by_year.items())],
        'remaining_by_activity': [{'activity_type': name, 'remaining_periods': count} for name, count in sorted(by_activity.items())],
        'faculty_gaps': [{'faculty': name, 'remaining_periods': value['remaining_periods'], 'offerings': len(value['offerings'])}
                         for name, value in sorted(by_faculty.items())],
        'offerings_without_faculty': [row for row in incomplete if not row['faculty']],
        'scope_has_offerings': bool(rows),
    }


def _version_label(request):
    version = _version(request)
    return {'id': str(version.pk), 'title': version.timetable.title,
            'session': version.timetable.academic_session.name,
            'semester': version.timetable.semester.name} if version else None


def unscheduled(request):
    return unscheduled_analytics(request)['offerings']


def conflicts(request):
    version=_version(request); rows=[]
    for entry in _entries(request,version) if version else []:
        data={'weekday':entry.weekday,'start_slot':entry.start_slot_id,'block_length':entry.block_length,'section':entry.section_id,'room':entry.room_id,'faculty_ids':list(entry.faculty_assignments.values_list('faculty_id',flat=True))}
        for issue in validate_entry(version,data,entry.pk): rows.append({**issue,'entry_id':str(entry.pk),'section':str(entry.section),'course_code':entry.course_offering.course.code,'room':entry.room.code if entry.room else ''})
    return rows


def version_activity(request):
    qs=AuditEvent.objects.select_related('actor').order_by('-created_at')
    if request.query_params.get('version'): qs=qs.filter(version_id=request.query_params['version'])
    return [{'id':str(e.id),'event_type':e.event_type,'version_id':str(e.version_id) if e.version_id else None,'actor':e.actor.get_full_name() or e.actor.email if e.actor else None,'metadata':e.metadata,'created_at':e.created_at} for e in qs[:500]]


def analytics(request):
    workload=faculty_workload(request); rooms=room_utilization(request); entry_qs, weekday_counts, slot_counts=scheduled_entry_analytics(request); entries=[entry_row(e) for e in entry_qs]; allocation=course_allocation(request)
    weekday=defaultdict(int); slots=defaultdict(int); room_types=defaultdict(int); years=defaultdict(int); heatmap=defaultdict(int)
    for weekday_value, count in weekday_counts.items(): weekday[ScheduleEntry.Weekday(weekday_value).label] = count
    for slot_label, count in slot_counts.items(): slots[slot_label] = count
    for row in entries: heatmap[f"{row['weekday']}:{row['time']}"]+=1
    for room in Room.objects.filter(active=True): room_types[room.get_room_type_display()]+=1
    for row in students_by_year(request):
        years[f"Year {row['year']}"] = row['student_count']
    overall_utilization=round(sum(x['occupied_slots'] for x in rooms)*100/(sum(x['total_available_slots'] for x in rooms)),2) if rooms and sum(x['total_available_slots'] for x in rooms) else 0
    return {'total_faculty':published_faculty_count(entry_qs),'total_rooms':Room.objects.filter(active=True).count(),'total_sections':Section.objects.count(),'scheduled_classes':entry_qs.count(),'room_utilization_percentage':overall_utilization,'average_faculty_workload':round(sum(x['total_scheduled_periods'] for x in workload)/len(workload),2) if workload else 0,'under_scheduled_courses':sum(1 for x in allocation if x['difference']<0),'weekday_load':dict(weekday),'time_slot_load':dict(slots),'room_type_distribution':dict(room_types),'projector_distribution':{'Projector Available':Room.objects.filter(active=True,has_projector=True).count(),'No Projector':Room.objects.filter(active=True,has_projector=False).count()},'students_by_year':dict(years),'heatmap':dict(heatmap)}

def free_rooms(request):
    version = _version(request)
    raw_weekday = request.query_params.get('weekday')
    weekday = int(raw_weekday) if raw_weekday not in (None, '') else None
    slot_id = request.query_params.get('time_slot') or request.query_params.get('start_slot')
    raw_capacity = request.query_params.get('capacity', '')
    required = int(raw_capacity) if raw_capacity not in (None, '') else 0
    rooms = Room.objects.filter(active=True, capacity__gte=required).order_by('code')
    room_type = request.query_params.get('room_type')
    if room_type:
        rooms = rooms.filter(room_type=room_type)
    projector = request.query_params.get('projector')
    if projector in ('true', 'false'):
        rooms = rooms.filter(has_projector=(projector == 'true'))

    blocked_ids = set()
    occupied_ids = set()
    requested_slot = None
    if slot_id:
        requested_slot = TimeSlot.objects.filter(pk=slot_id).first()
        if requested_slot and requested_slot.is_break:
            return {'version': _version_label(request), 'requested_slot': requested_slot.label, 'weekday': weekday,
                    'message': 'Break periods are not valid room-availability searches.',
                    'summary': {'free_rooms': 0, 'projector_available': 0, 'average_capacity': 0, 'largest_capacity': 0},
                    'analytics': {'by_room_type': [], 'by_building': [], 'capacity_distribution': []},
                    'audit': {'active_rooms': Room.objects.filter(active=True).count(), 'occupied_rooms': 0,
                              'blocked_rooms': 0, 'free_rooms': 0, 'occupied_codes': [], 'blocked_codes': [], 'free_codes': []},
                    'rooms': []}
        if requested_slot:
            # The filter asks about exactly one period. Expand each scheduled
            # block through usable template slots so earlier starts overlap.
            slots = list(TimeSlot.objects.filter(template=requested_slot.template, is_break=False).order_by('order'))
            requested_index = next((index for index, slot in enumerate(slots) if slot.pk == requested_slot.pk), None)
            if requested_index is not None:
                slot_index = {slot.pk: index for index, slot in enumerate(slots)}
                occupancy = ScheduleEntry.objects.filter(version=version, weekday=weekday, room__isnull=False).select_related('start_slot') if version and weekday is not None else ScheduleEntry.objects.none()
                for entry in occupancy:
                    start_index = slot_index.get(entry.start_slot_id)
                    if start_index is not None and start_index <= requested_index < start_index + entry.block_length:
                        occupied_ids.add(entry.room_id)
                matching_rules = RoomAvailability.objects.filter(
                    time_slot=requested_slot, status__in=('BLOCKED', 'MAINTENANCE'),
                ).filter(Q(weekday__isnull=True) | Q(weekday=weekday), room__active=True)
                blocked_ids = set(matching_rules.values_list('room_id', flat=True))
                rooms = rooms.exclude(pk__in=occupied_ids | blocked_ids)

    room_rows = list(rooms)
    codes = lambda values: sorted(Room.objects.filter(pk__in=values).values_list('code', flat=True))
    active_ids = set(Room.objects.filter(active=True).values_list('pk', flat=True))
    occupied_active = occupied_ids & active_ids
    blocked_active = blocked_ids & active_ids
    blocked_only = blocked_active - occupied_active
    base_free_ids = active_ids - occupied_active - blocked_only
    return {'version': _version_label(request), 'requested_slot': requested_slot.label if slot_id and requested_slot else None,
            'weekday': weekday, 'summary': {'free_rooms': len(room_rows),
                'projector_available': sum(room.has_projector for room in room_rows),
                'average_capacity': round(sum(room.capacity for room in room_rows) / len(room_rows), 2) if room_rows else 0,
                'largest_capacity': max((room.capacity for room in room_rows), default=0)},
            'analytics': {'by_room_type': _count_rooms(room_rows, lambda room: room.get_room_type_display()),
                'by_building': _count_rooms(room_rows, lambda room: room.building.strip() or 'Unknown'),
                'capacity_distribution': _capacity_distribution(room_rows)},
            'audit': {'active_rooms': len(active_ids), 'occupied_rooms': len(occupied_active),
                'blocked_rooms': len(blocked_only), 'free_rooms': len(base_free_ids),
                'occupied_codes': codes(occupied_active), 'blocked_codes': codes(blocked_only),
                'free_codes': codes(base_free_ids)},
            'rooms': [{'room_id': str(room.pk), 'room_no': room.code, 'room_type': room.get_room_type_display(),
                'capacity': room.capacity, 'building': room.building.strip() or 'Unknown', 'floor': room.floor,
                'projector': 'Yes' if room.has_projector else 'No'} for room in room_rows]}


def _count_rooms(rooms, key):
    counts = defaultdict(int)
    for room in rooms:
        counts[key(room)] += 1
    return [{'label': label, 'count': count} for label, count in sorted(counts.items())]


def _capacity_distribution(rooms):
    buckets = {'0-30': 0, '31-50': 0, '51-70': 0, '71+': 0}
    for room in rooms:
        key = '0-30' if room.capacity <= 30 else '31-50' if room.capacity <= 50 else '51-70' if room.capacity <= 70 else '71+'
        buckets[key] += 1
    return [{'label': label, 'count': count} for label, count in buckets.items()]

def faculty_arrangements(request):
    qs=FacultyArrangement.objects.select_related('schedule_entry__start_slot','schedule_entry__course_offering__course','schedule_entry__section','schedule_entry__room','absent_faculty__user','substitute_faculty__user','created_by').prefetch_related('evidence').exclude(status='CANCELLED')
    if request.user.role=='FACULTY': qs=qs.filter(substitute_faculty__user=request.user)
    for key in ('arrangement_date','absent_faculty_id','substitute_faculty_id','status'):
        if request.query_params.get(key): qs=qs.filter(**{key:request.query_params[key]})
    if request.query_params.get('from'): qs=qs.filter(arrangement_date__gte=request.query_params['from'])
    if request.query_params.get('to'): qs=qs.filter(arrangement_date__lte=request.query_params['to'])
    def name(f): return (f'{f.user.first_name} {f.user.last_name}'.strip() if f.user else '') or f.name or f.initials or f.employee_code
    return [{'arrangement_date':a.arrangement_date,'day':a.schedule_entry.get_weekday_display(),'time_slot':a.schedule_entry.start_slot.label,'section':str(a.schedule_entry.section),'course':a.schedule_entry.course_offering.course.code,'room':a.schedule_entry.room.code if a.schedule_entry.room else '','absent_faculty':name(a.absent_faculty),'arrangement_faculty':name(a.substitute_faculty),'status':a.status,'attendance':'Uploaded' if a.evidence.exists() else 'Pending','created_by':a.created_by.email,'created_at':a.created_at} for a in qs.order_by('-arrangement_date','-created_at')]
