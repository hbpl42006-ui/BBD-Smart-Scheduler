from collections import defaultdict
from datetime import datetime, timezone

from academics.models import CourseOffering, Section
from audit.models import AuditEvent
from common.models import TimeSlot
from faculty.models import Faculty
from rooms.models import Room, RoomAvailability
from scheduling.models import ScheduleEntry, Timetable, TimetableVersion
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
    ).prefetch_related('faculty_assignments__faculty__user')
    for field, key in (('section_id', 'section'), ('room_id', 'room'), ('course_offering__semester_id', 'semester')):
        if request.query_params.get(key): qs = qs.filter(**{field: request.query_params[key]})
    if request.query_params.get('program'): qs = qs.filter(section__program_id=request.query_params['program'])
    if request.query_params.get('department'): qs = qs.filter(section__program__department_id=request.query_params['department'])
    if request.query_params.get('session') or request.query_params.get('academic_session'): qs = qs.filter(section__semester__session_id=request.query_params.get('session') or request.query_params.get('academic_session'))
    if request.query_params.get('course'): qs = qs.filter(course_offering__course_id=request.query_params['course'])
    if request.query_params.get('faculty'): qs = qs.filter(faculty_assignments__faculty_id=request.query_params['faculty']).distinct()
    return qs


def faculty_name(faculty):
    linked = f'{faculty.user.first_name} {faculty.user.last_name}'.strip() if faculty.user else ''
    return linked or faculty.name or faculty.initials or faculty.employee_code


def entry_row(entry):
    assignments = list(entry.faculty_assignments.all())
    faculties = [{'id': str(a.faculty_id), 'name': faculty_name(a.faculty), 'role': a.role} for a in assignments]
    return {'entry_id': str(entry.pk), 'section_id': str(entry.section_id), 'section': str(entry.section), 'program': entry.section.program.name,
            'semester': entry.section.semester.name, 'session': entry.section.semester.session.name, 'day': DAYS.get(entry.weekday, str(entry.weekday)),
            'weekday': entry.weekday, 'time': entry.start_slot.label, 'course_offering_id': str(entry.course_offering_id),
            'course_code': entry.course_offering.course.code, 'course_name': entry.course_offering.course.name,
            'faculty': faculties, 'faculty_names': ', '.join(x['name'] for x in faculties), 'room': entry.room.code if entry.room else '',
            'room_id': str(entry.room_id) if entry.room_id else None, 'entry_type': entry.entry_type, 'block_length': entry.block_length}


def faculty_workload(request):
    if request.user.role == 'FACULTY' and getattr(request.user, 'faculty_profile', None): request = _with_query(request, faculty=str(request.user.faculty_profile.pk))
    version = _version(request); grouped = defaultdict(lambda: {'assigned_courses': set(), 'sections': set(), 'lecture_periods': 0, 'practical_periods': 0, 'total_scheduled_periods': 0})
    if version:
        for entry in _entries(request, version):
            for assignment in entry.faculty_assignments.all():
                row = grouped[assignment.faculty_id]; row['assigned_courses'].add(entry.course_offering.course.code); row['sections'].add(str(entry.section)); row['total_scheduled_periods'] += entry.block_length
                if entry.entry_type == 'PRACTICAL': row['practical_periods'] += entry.block_length
                else: row['lecture_periods'] += entry.block_length
    faculty_qs = Faculty.objects.select_related('user', 'department').filter(pk__in=grouped.keys()) if grouped else Faculty.objects.none()
    if request.query_params.get('faculty'): faculty_qs = Faculty.objects.filter(pk=request.query_params['faculty']).select_related('user', 'department')
    return [{'faculty_id': str(f.pk), 'faculty_name': faculty_name(f), 'employee_code': f.employee_code, 'department': f.department.name,
             'assigned_courses': sorted(grouped[f.pk]['assigned_courses']), 'sections': sorted(grouped[f.pk]['sections']),
             'lecture_periods': grouped[f.pk]['lecture_periods'], 'practical_periods': grouped[f.pk]['practical_periods'],
             'total_scheduled_periods': grouped[f.pk]['total_scheduled_periods'], 'total_weekly_teaching_hours': grouped[f.pk]['total_scheduled_periods'],
             'distinct_courses': len(grouped[f.pk]['assigned_courses']), 'distinct_sections': len(grouped[f.pk]['sections'])} for f in faculty_qs]

def _with_query(request, **values):
    from django.http import QueryDict
    query=QueryDict(mutable=True);query.update(request.query_params);query.update(values);request.query_params=query;return request


def room_utilization(request):
    version = _version(request); rooms = Room.objects.filter(active=True)
    if request.query_params.get('room'): rooms = rooms.filter(pk=request.query_params['room'])
    entries = list(_entries(request, version)) if version else []
    templates = {e.start_slot.template_id for e in entries} or set(TimeSlot.objects.filter(is_break=False).values_list('template_id', flat=True))
    usable = sum(TimeSlot.objects.filter(template_id__in=templates, is_break=False).count() for _ in [0])
    rows=[]
    for room in rooms:
        used_entries=[e for e in entries if e.room_id==room.pk]; occupied=sum(e.block_length for e in used_entries); total=usable
        rows.append({'room_id':str(room.pk),'room_no':room.code,'room_type':room.get_room_type_display(),'capacity':room.capacity,'total_available_slots':total,'occupied_slots':occupied,'free_slots':max(total-occupied,0),'utilization_percentage':round(occupied*100/total,2) if total else 0,'courses':sorted({e.course_offering.course.code for e in used_entries}),'sections':sorted({str(e.section) for e in used_entries})})
    return rows


def section_timetable(request):
    version=_version(request); return [entry_row(e) for e in _entries(request,version)] if version else []


def faculty_timetable(request):
    faculty_id=request.query_params.get('faculty')
    if request.user.role == 'FACULTY': faculty_id=getattr(getattr(request.user,'faculty_profile',None),'pk',None)
    if not faculty_id:return []
    return [row for row in section_timetable(request) if any(f['id']==str(faculty_id) for f in row['faculty'])]


def room_timetable(request): return section_timetable(request)


def course_allocation(request):
    version=_version(request); grouped=defaultdict(lambda:{'scheduled':0,'sections':set(),'faculty':set(),'rooms':set()})
    for entry in _entries(request,version) if version else []:
        key=entry.course_offering_id; row=grouped[key]; row['scheduled']+=entry.block_length; row['sections'].add(str(entry.section)); row['rooms'].add(entry.room.code if entry.room else '')
        row['faculty'].update(faculty_name(a.faculty) for a in entry.faculty_assignments.all())
    offerings=CourseOffering.objects.filter(pk__in=grouped.keys()).select_related('course','section__program','semester')
    return [{'course_offering_id':str(o.pk),'course_code':o.course.code,'course_name':o.course.name,'program':o.section.program.name,'semester':o.semester.name,'sections':sorted(grouped[o.pk]['sections']),'faculty':sorted(grouped[o.pk]['faculty']),'scheduled_periods':grouped[o.pk]['scheduled'],'required_periods':o.weekly_periods,'difference':grouped[o.pk]['scheduled']-o.weekly_periods,'rooms':sorted(x for x in grouped[o.pk]['rooms'] if x)} for o in offerings]


def unscheduled(request): return [row for row in course_allocation(request) if row['difference'] < 0]


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
    workload=faculty_workload(request); rooms=room_utilization(request); entries=section_timetable(request); allocation=course_allocation(request)
    weekday=defaultdict(int); slots=defaultdict(int)
    for row in entries: weekday[row['day']]+=row['block_length']; slots[row['time']]+=row['block_length']
    return {'total_faculty':Faculty.objects.count(),'total_rooms':Room.objects.filter(active=True).count(),'total_sections':Section.objects.count(),'scheduled_classes':len(entries),'room_utilization_percentage':round(sum(x['utilization_percentage'] for x in rooms)/len(rooms),2) if rooms else 0,'average_faculty_workload':round(sum(x['total_scheduled_periods'] for x in workload)/len(workload),2) if workload else 0,'under_scheduled_courses':sum(1 for x in allocation if x['difference']<0),'weekday_load':dict(weekday),'time_slot_load':dict(slots)}

def free_rooms(request):
    version=_version(request); weekday=int(request.query_params.get('weekday',0)); slot_id=request.query_params.get('time_slot') or request.query_params.get('start_slot'); required=int(request.query_params.get('capacity',0)); rooms=Room.objects.filter(active=True,capacity__gte=required)
    if request.query_params.get('room_type'): rooms=rooms.filter(room_type=request.query_params['room_type'])
    if version and slot_id:
        requested_slot=TimeSlot.objects.filter(pk=slot_id).first()
        if requested_slot:
            length=max(int(request.query_params.get('block_length',1)),1); slot_ids=list(TimeSlot.objects.filter(template=requested_slot.template,is_break=False,order__gte=requested_slot.order,order__lt=requested_slot.order+length).values_list('pk',flat=True))
            occupied=ScheduleEntry.objects.filter(version=version,weekday=weekday,start_slot_id__in=slot_ids).values_list('room_id',flat=True)
            blocked=RoomAvailability.objects.filter(time_slot_id__in=slot_ids,status__in=['BLOCKED','MAINTENANCE']).filter(weekday__isnull=True) | RoomAvailability.objects.filter(time_slot_id__in=slot_ids,status__in=['BLOCKED','MAINTENANCE'],weekday=weekday)
            rooms=rooms.exclude(pk__in=occupied).exclude(pk__in=blocked.values('room_id'))
    return [{'room_id':str(r.pk),'room_no':r.code,'room_type':r.get_room_type_display(),'capacity':r.capacity,'building':r.building,'floor':r.floor} for r in rooms]
