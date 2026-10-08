from datetime import date, datetime, timedelta
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import FileResponse
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone
from accounts.models import Role
from common.models import TimeSlot
from scheduling.models import ScheduleEntry, TimetableVersion
from .models import Faculty, FacultyAvailability, FacultyArrangement, ArrangementAttendanceEvidence

MANAGERS={Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.TIMETABLE_COORDINATOR,Role.HOD_OR_DEAN_APPROVER}
def manager(user): return user.is_superuser or user.role in MANAGERS
def faculty_name(f): return (f'{f.user.first_name} {f.user.last_name}'.strip() if f.user else '') or f.name or f.initials or f.employee_code
def arrangement_class_end(entry, arrangement_date):
    slots=occupied_slots(entry)
    if len(slots)!=max(1,entry.block_length):return None
    end_local=datetime.combine(arrangement_date,slots[-1].end_time)
    return timezone.make_aware(end_local,timezone.get_current_timezone())
def arrangement_row(a):
    e=a.schedule_entry
    evidence=list(a.evidence.all()) if 'evidence' in getattr(a,'_prefetched_objects_cache',{}) else list(a.evidence.order_by('-uploaded_at')[:1])
    latest=max(evidence,key=lambda item:item.uploaded_at) if evidence else None
    class_end=arrangement_class_end(e,a.arrangement_date)
    return {'id':str(a.pk),'schedule_entry':str(a.schedule_entry_id),'arrangement_date':a.arrangement_date,'day':e.get_weekday_display(),'weekday':e.weekday,'time_slot':e.start_slot.label,'class_end_at':class_end.isoformat() if class_end else None,'timezone':timezone.get_current_timezone_name(),'course_code':e.course_offering.course.code,'course_name':e.course_offering.course.name,'section':str(e.section),'section_id':str(e.section_id),'room':e.room.code if e.room else '','absent_faculty':faculty_name(a.absent_faculty),'absent_faculty_id':str(a.absent_faculty_id),'substitute_faculty':faculty_name(a.substitute_faculty),'substitute_faculty_id':str(a.substitute_faculty_id),'reason':a.reason,'status':a.status,'attendance':'Uploaded' if latest else 'Pending','evidence':{'id':str(latest.pk),'uploaded_at':latest.uploaded_at,'original_filename':latest.original_filename,'view_url':f'/api/faculty-arrangements/{a.pk}/attendance/{latest.pk}/view/'} if latest else None,'created_by':a.created_by.email,'created_at':a.created_at}

def scoped(qs,user):
    if user.role=='FACULTY': return qs.filter(Q(substitute_faculty__user=user)|Q(absent_faculty__user=user)).distinct()
    if user.role=='HOD_OR_DEAN_APPROVER': return qs.filter(schedule_entry__version__timetable__department__faculties__user=user).distinct()
    return qs
def occupied_slots(entry, slot_cache=None):
    from common.models import TimeSlot
    slots=(slot_cache or {}).get(entry.start_slot.template_id)
    if slots is None:slots=list(TimeSlot.objects.filter(template_id=entry.start_slot.template_id).order_by('order'))
    start=next((index for index,item in enumerate(slots) if item.pk==entry.start_slot_id),None)
    if start is None:return []
    selected=[]
    for slot in slots[start:]:
        if slot.is_break or (selected and slot.order!=selected[-1].order+1):break
        selected.append(slot)
        if len(selected)>=max(1,entry.block_length):break
    return selected if len(selected)==max(1,entry.block_length) else []

def candidates(day, entry, user):
    from common.models import TimeSlot
    relevant_entries=list(ScheduleEntry.objects.filter(version__status='PUBLISHED',weekday=entry.weekday).select_related('start_slot').prefetch_related('faculty_assignments'))
    relevant_arrangements=list(FacultyArrangement.objects.filter(arrangement_date=day,status__in=['ASSIGNED','COMPLETED'],schedule_entry__weekday=entry.weekday).select_related('schedule_entry__start_slot'))
    template_ids={entry.start_slot.template_id}|{item.start_slot.template_id for item in relevant_entries}|{item.schedule_entry.start_slot.template_id for item in relevant_arrangements}
    slot_cache={template_id:[] for template_id in template_ids}
    for slot in TimeSlot.objects.filter(template_id__in=template_ids).order_by('template_id','order'):slot_cache[slot.template_id].append(slot)
    slots=occupied_slots(entry,slot_cache)
    if not slots:return []
    slot_ids=[slot.pk for slot in slots]
    assigned=set(entry.faculty_assignments.values_list('faculty_id',flat=True))
    regular=set()
    for scheduled in relevant_entries:
        if {slot.pk for slot in occupied_slots(scheduled,slot_cache)} & set(slot_ids):
            regular.update(assignment.faculty_id for assignment in scheduled.faculty_assignments.all())
    arranged=set()
    for item in relevant_arrangements:
        if {s.pk for s in occupied_slots(item.schedule_entry,slot_cache)} & set(slot_ids):arranged.add(item.substitute_faculty_id)
    unavailable=set(FacultyAvailability.objects.filter(weekday=entry.weekday,time_slot_id__in=slot_ids,is_available=False).values_list('faculty_id',flat=True))
    busy=regular|arranged|unavailable|assigned
    eligible_qs=Faculty.objects.filter(active=True).exclude(pk__in=busy)
    if user.role=='FACULTY' and not manager(user):eligible_qs=eligible_qs.filter(user__is_active=True,user__role=Role.FACULTY)
    eligible=list(eligible_qs.select_related('user','department').order_by('name','employee_code'))
    ids=[item.pk for item in eligible]
    today_counts=dict(ScheduleEntry.objects.filter(version__status='PUBLISHED',weekday=entry.weekday,faculty_assignments__faculty_id__in=ids).values('faculty_assignments__faculty_id').annotate(count=Count('id')).values_list('faculty_assignments__faculty_id','count'))
    arrangement_counts=dict(FacultyArrangement.objects.filter(arrangement_date=day,status__in=['ASSIGNED','COMPLETED'],substitute_faculty_id__in=ids).values('substitute_faculty_id').annotate(count=Count('id')).values_list('substitute_faculty_id','count'))
    week_start=day-timedelta(days=day.weekday());week_end=week_start+timedelta(days=6)
    week_counts=dict(FacultyArrangement.objects.filter(arrangement_date__gte=week_start,arrangement_date__lte=week_end,status__in=['ASSIGNED','COMPLETED'],substitute_faculty_id__in=ids).values('substitute_faculty_id').annotate(count=Count('id')).values_list('substitute_faculty_id','count'))
    return [{'id':str(f.pk),'name':faculty_name(f),'employee_code':f.employee_code,'department':f.department.name,'normal_classes_today':today_counts.get(f.pk,0),'arrangements_today':arrangement_counts.get(f.pk,0),'arrangements_this_week':week_counts.get(f.pk,0)} for f in eligible]

def validate_arrangement(data, user, date, absent, entry, substitute, reserved=None):
    if not entry or entry.version.status!='PUBLISHED': return 'Schedule entry is not from a published timetable.'
    if date.weekday()>=5:return 'Weekend dates are not valid working days.'
    if date.weekday()!=entry.weekday: return 'Arrangement date does not match the timetable weekday.'
    if not entry.faculty_assignments.filter(faculty=absent).exists(): return 'Absent faculty is not assigned to this schedule entry.'
    if absent==substitute: return 'Substitute faculty must differ from absent faculty.'
    if FacultyArrangement.objects.filter(schedule_entry=entry,arrangement_date=date).exists(): return 'An arrangement already exists for this class and date.'
    if reserved and (substitute.pk,entry.start_slot_id) in reserved: return 'Substitute faculty is already assigned in another row of this request.'
    if not any(x['id']==str(substitute.pk) for x in candidates(date,entry,user)): return 'Substitute faculty is unavailable or already busy.'
    return None

class ArrangementView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        qs=scoped(FacultyArrangement.objects.select_related('schedule_entry__start_slot','schedule_entry__course_offering__course','schedule_entry__section','schedule_entry__room','absent_faculty__user','substitute_faculty__user','created_by').prefetch_related('evidence'),request.user)
        for key in ('arrangement_date','status','absent_faculty_id','substitute_faculty_id'):
            if request.query_params.get(key): qs=qs.filter(**{key:request.query_params[key]})
        if request.query_params.get('from'): qs=qs.filter(arrangement_date__gte=request.query_params['from'])
        if request.query_params.get('to'): qs=qs.filter(arrangement_date__lte=request.query_params['to'])
        rows=[]
        for a in qs.order_by('-arrangement_date','schedule_entry__weekday','schedule_entry__start_slot__order'):
            row=arrangement_row(a)
            row['is_arrangement_faculty']=a.substitute_faculty.user_id==request.user.id
            rows.append(row)
        return Response(rows)
    def post(self,request):
        self_service=request.user.role=='FACULTY' and not manager(request.user)
        if not manager(request.user) and not self_service: return Response({'detail':'You do not have permission to create arrangements.','code':'PERMISSION_DENIED'},status=403)
        data=request.data
        entry_query=ScheduleEntry.objects.select_related('version','start_slot').filter(pk=data.get('schedule_entry',data.get('schedule_entry_id')),version__status='PUBLISHED')
        if self_service:entry_query=entry_query.filter(version__timetable__active=True)
        entry=entry_query.first()
        absent=getattr(request.user,'faculty_profile',None) if self_service else Faculty.objects.filter(pk=data.get('absent_faculty')).first()
        substitute=Faculty.objects.filter(pk=data.get('substitute_faculty',data.get('arrangement_faculty_id')),active=True).first()
        if not entry or not absent or not substitute:return Response({'detail':'A published schedule entry and valid faculty are required.'},status=400)
        try: day=date.fromisoformat(str(data.get('arrangement_date',data.get('date'))))
        except (TypeError,ValueError):return Response({'detail':'arrangement_date must be YYYY-MM-DD.','code':'INVALID_CLASS_DATE'},status=400)
        if self_service and day<date.today():return Response({'detail':'Past class dates cannot be arranged.','code':'INVALID_CLASS_DATE'},status=400)
        if self_service and not entry.faculty_assignments.filter(faculty=absent).exists():return Response({'detail':'This is not one of your assigned classes.','code':'NOT_YOUR_CLASS'},status=403)
        error=validate_arrangement(data,request.user,day,absent,entry,substitute)
        if error:
            code='ARRANGEMENT_ALREADY_EXISTS' if 'already exists' in error else 'SUBSTITUTE_MUST_DIFFER' if 'must differ' in error else 'FACULTY_NOT_AVAILABLE' if 'unavailable' in error else 'INVALID_CLASS_DATE' if 'weekday' in error or 'Weekend' in error else 'INVALID_ARRANGEMENT'
            return Response({'detail':error,'code':code},status=400)
        try:
            with transaction.atomic():
                a=FacultyArrangement.objects.create(schedule_entry=entry,arrangement_date=day,absent_faculty=absent,substitute_faculty=substitute,reason=data.get('reason',''),created_by=request.user)
        except IntegrityError:
            return Response({'detail':'An arrangement already exists for this class and date.','code':'ARRANGEMENT_ALREADY_EXISTS'},status=400)
        from notifications.services import notify_arrangement_after_commit
        notify_arrangement_after_commit(a,'ASSIGNED',request.user)
        return Response(arrangement_row(a),status=201)

class ArrangementBulkView(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request):
        if not manager(request.user): return Response({'detail':'You do not have permission to create arrangements.'},status=403)
        from datetime import date
        try: day=date.fromisoformat(str(request.data.get('date')))
        except (TypeError,ValueError): return Response({'detail':'date must be YYYY-MM-DD.','errors':[]},status=400)
        absent=Faculty.objects.filter(pk=request.data.get('absent_faculty')).first(); rows=request.data.get('arrangements') or []
        if not absent or not isinstance(rows,list) or not rows:return Response({'detail':'absent_faculty and arrangements are required.','errors':[]},status=400)
        errors=[]; validated=[]; reserved=set()
        for index,row in enumerate(rows,1):
            entry=ScheduleEntry.objects.filter(pk=row.get('schedule_entry')).select_related('version').first(); substitute=Faculty.objects.filter(pk=row.get('substitute_faculty')).first()
            error=validate_arrangement(row,request.user,day,absent,entry,substitute,reserved) if entry and substitute else 'A valid schedule_entry and substitute_faculty are required.'
            if error: errors.append({'row':index,'schedule_entry':row.get('schedule_entry'),'message':error})
            else: validated.append((entry,substitute)); reserved.add((substitute.pk,entry.start_slot_id))
        if errors:return Response({'detail':'No arrangements were saved.','errors':errors},status=400)
        with transaction.atomic():
            created=[FacultyArrangement.objects.create(schedule_entry=e,arrangement_date=day,absent_faculty=absent,substitute_faculty=s,reason=request.data.get('reason',''),created_by=request.user) for e,s in validated]
        from notifications.services import notify_arrangement_after_commit
        for arrangement in created: notify_arrangement_after_commit(arrangement,'ASSIGNED',request.user)
        return Response({'created':[arrangement_row(a) for a in created]},status=201)

class ArrangementReassignView(APIView):
    permission_classes=[IsAuthenticated]
    def patch(self,request,arrangement_id):
        if not manager(request.user): return Response({'detail':'You do not have permission to reassign arrangements.'},status=403)
        with transaction.atomic():
            arrangement=FacultyArrangement.objects.select_for_update().select_related('schedule_entry__start_slot','schedule_entry__version','absent_faculty','substitute_faculty').prefetch_related('evidence').filter(pk=arrangement_id).first()
            if not arrangement:return Response({'detail':'Arrangement not found.'},status=404)
            if arrangement.status!='ASSIGNED':return Response({'detail':'Only assigned arrangements can be reassigned.'},status=400)
            if arrangement.evidence.exists():return Response({'detail':'Attendance evidence has already been uploaded for this arrangement. Reassignment requires an administrative correction.'},status=400)
            substitute=Faculty.objects.filter(pk=request.data.get('substitute_faculty')).first()
            if not substitute:return Response({'detail':'A valid substitute faculty is required.'},status=400)
            if substitute.pk==arrangement.substitute_faculty_id:return Response({'detail':'Select a different substitute faculty.'},status=400)
            error=validate_arrangement(request.data,request.user,arrangement.arrangement_date,arrangement.absent_faculty,arrangement.schedule_entry,substitute)
            if error and error!='An arrangement already exists for this class and date.':return Response({'detail':error},status=400)
            old_substitute=arrangement.substitute_faculty
            arrangement.substitute_faculty=substitute; arrangement.save(update_fields=['substitute_faculty','updated_at'])
            from notifications.services import notify_arrangement_after_commit
            notify_arrangement_after_commit(arrangement,'REASSIGNED',request.user,old_substitute)
            notify_arrangement_after_commit(arrangement,'ASSIGNED',request.user)
        return Response(arrangement_row(arrangement))

class ArrangementCancelView(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request,arrangement_id):
        if not manager(request.user):return Response({'detail':'You do not have permission to cancel arrangements.'},status=403)
        arrangement=FacultyArrangement.objects.filter(pk=arrangement_id).first()
        if not arrangement:return Response({'detail':'Arrangement not found.'},status=404)
        if arrangement.status!='CANCELLED':
            arrangement.status='CANCELLED';arrangement.save(update_fields=['status','updated_at'])
            from notifications.services import notify_arrangement_after_commit
            notify_arrangement_after_commit(arrangement,'CANCELLED',request.user)
        return Response(arrangement_row(arrangement))

class AvailableFacultyView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        self_service=request.user.role=='FACULTY' and not manager(request.user)
        if not manager(request.user) and not self_service: return Response({'detail':'Permission denied.'},status=403)
        try: day=date.fromisoformat(str(request.query_params['date']))
        except (KeyError,ValueError): return Response({'detail':'A valid date is required.'},status=400)
        entry_query=ScheduleEntry.objects.filter(pk=request.query_params.get('schedule_entry'),version__status='PUBLISHED')
        if self_service:entry_query=entry_query.filter(version__timetable__active=True)
        entry=entry_query.first()
        if not entry:return Response({'detail':'Published schedule entry not found.'},status=404)
        if self_service:
            own=getattr(request.user,'faculty_profile',None)
            if not own or not entry.faculty_assignments.filter(faculty=own).exists():return Response({'detail':'This is not one of your assigned classes.','code':'NOT_YOUR_CLASS'},status=403)
        if self_service and (day<date.today() or day.weekday()>=5 or day.weekday()!=entry.weekday):return Response({'detail':'Choose a future working date matching the class weekday.','code':'INVALID_CLASS_DATE'},status=400)
        return Response(candidates(day,entry,request.user))

class MyArrangementClassesView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        if request.user.role!='FACULTY':return Response({'detail':'Faculty role required.'},status=403)
        faculty=getattr(request.user,'faculty_profile',None)
        if not faculty:return Response({'detail':'No Faculty profile is linked to this account.'},status=404)
        qs=ScheduleEntry.objects.filter(version__status='PUBLISHED',version__timetable__active=True,faculty_assignments__faculty=faculty,weekday__lt=5).select_related('start_slot__template','course_offering__course','section','room').distinct().order_by('weekday','start_slot__order')
        return Response([{'schedule_entry':str(e.pk),'weekday':e.weekday,'day':e.get_weekday_display(),'time_slot':{'label':e.start_slot.label,'start_time':e.start_slot.start_time.strftime('%H:%M'),'end_time':e.start_slot.end_time.strftime('%H:%M')},'course':{'code':e.course_offering.course.code,'name':e.course_offering.course.name},'section':{'id':str(e.section_id),'name':str(e.section)},'room':e.room.code if e.room else None,'delivery_mode':e.delivery_mode,'period_count':e.block_length,'original_faculty':faculty_name(faculty)} for e in qs])

class AvailabilityMatrixView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        from datetime import date
        try: day=date.fromisoformat(str(request.query_params.get('date')))
        except (TypeError,ValueError): return Response({'detail':'A valid date is required.'},status=400)
        department=request.query_params.get('department'); faculty_filter=request.query_params.get('faculty'); status_filter=request.query_params.get('status'); slot_filter=request.query_params.get('slot')
        if status_filter is not None and status_filter not in ('','FREE','REGULAR_CLASS','ARRANGEMENT','UNAVAILABLE'): return Response({'detail':'Invalid availability status.'},status=400)
        faculties=Faculty.objects.select_related('user','department').all()
        if department: faculties=faculties.filter(department_id=department)
        if faculty_filter: faculties=faculties.filter(pk=faculty_filter)
        if request.user.role=='FACULTY': faculties=faculties.filter(user=request.user)
        faculties=list(faculties.order_by('employee_code'))
        slots_qs=TimeSlot.objects.filter(is_break=False).select_related('template').order_by('template_id','order')
        if slot_filter:
            if not slots_qs.filter(pk=slot_filter).exists(): return Response({'detail':'Invalid teaching time slot.'},status=400)
            slots_qs=slots_qs.filter(pk=slot_filter)
        slots=list(slots_qs)
        version=TimetableVersion.objects.filter(status='PUBLISHED',timetable__active=True).order_by('-published_at','-version_no').first()
        entries=list(ScheduleEntry.objects.filter(version=version,weekday=day.weekday(),faculty_assignments__faculty_id__in=[f.pk for f in faculties]).select_related('start_slot','course_offering__course','section','room').prefetch_related('faculty_assignments').distinct()) if version else []
        arrangements=list(FacultyArrangement.objects.filter(arrangement_date=day,status__in=['ASSIGNED','COMPLETED'],substitute_faculty_id__in=[f.pk for f in faculties]).select_related('schedule_entry__start_slot','schedule_entry__course_offering__course','schedule_entry__section','schedule_entry__room'))
        unavailable={(str(row.faculty_id),row.time_slot_id) for row in FacultyAvailability.objects.filter(faculty_id__in=[f.pk for f in faculties],weekday=day.weekday(),is_available=False)}
        regular={}; arranged={}
        for entry in entries:
            for assignment in entry.faculty_assignments.all(): regular[(str(assignment.faculty_id),entry.start_slot_id)]={'course':entry.course_offering.course.code,'section':str(entry.section),'room':entry.room.code if entry.room else '','class_type':entry.get_entry_type_display()}
        for item in arrangements:
            entry=item.schedule_entry; arranged[(str(item.substitute_faculty_id),entry.start_slot_id)]={'course':entry.course_offering.course.code,'section':str(entry.section),'room':entry.room.code if entry.room else '','class_type':entry.get_entry_type_display()}
        rows=[]
        for faculty in faculties:
            cells=[]
            for slot in slots:
                key=(str(faculty.pk),slot.pk)
                if key in arranged: cell={'status':'ARRANGEMENT',**arranged[key]}
                elif key in regular: cell={'status':'REGULAR_CLASS',**regular[key]}
                elif key in unavailable: cell={'status':'UNAVAILABLE'}
                else: cell={'status':'FREE'}
                if not status_filter or cell['status']==status_filter: cells.append({'time_slot_id':str(slot.pk),'label':slot.label,**cell})
            rows.append({'faculty_id':str(faculty.pk),'name':faculty_name(faculty),'employee_code':faculty.employee_code,'department':faculty.department.name,'cells':cells})
        return Response({'date':day,'weekday':day.weekday(),'version_id':str(version.pk) if version else None,'slots':[{'id':str(s.pk),'label':s.label} for s in slots],'rows':rows,'summary':{'total_faculty':len(rows),'free_now':sum(c['status']=='FREE' for r in rows for c in r['cells']),'regular_classes':sum(c['status']=='REGULAR_CLASS' for r in rows for c in r['cells']),'arrangement_classes':sum(c['status']=='ARRANGEMENT' for r in rows for c in r['cells']),'unavailable':sum(c['status']=='UNAVAILABLE' for r in rows for c in r['cells'])}})

class AffectedClassesView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        if not manager(request.user): return Response({'detail':'Permission denied.'},status=403)
        try: day=__import__('datetime').date.fromisoformat(request.query_params['date'])
        except (KeyError,ValueError): return Response({'detail':'A valid date is required.'},status=400)
        qs=ScheduleEntry.objects.filter(version__status='PUBLISHED',weekday=day.weekday(),faculty_assignments__faculty_id=request.query_params.get('absent_faculty')).select_related('start_slot','course_offering__course','section','room').distinct()
        return Response([{'schedule_entry':str(e.pk),'time_slot':e.start_slot.label,'section':str(e.section),'course_code':e.course_offering.course.code,'room':e.room.code if e.room else '','candidates':candidates(day,e,request.user)} for e in qs.order_by('start_slot__order')])

class ArrangementSummaryView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        qs=scoped(FacultyArrangement.objects.all(),request.user); today=__import__('datetime').date.today(); week=today-timedelta(days=today.weekday())
        return Response({'today':qs.filter(arrangement_date=today).count(),'this_week':qs.filter(arrangement_date__gte=week,arrangement_date__lte=week+timedelta(days=6)).count(),'this_month':qs.filter(arrangement_date__year=today.year,arrangement_date__month=today.month).count(),'pending_evidence':qs.filter(evidence__isnull=True).exclude(status='CANCELLED').distinct().count(),'taken_by_faculty':[{'faculty_id':str(x['substitute_faculty_id']),'count':x['count']} for x in qs.values('substitute_faculty_id').annotate(count=Count('id'))],'absences_by_faculty':[{'faculty_id':str(x['absent_faculty_id']),'count':x['count']} for x in qs.values('absent_faculty_id').annotate(count=Count('id'))]})

class ArrangementEvidenceView(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request,arrangement_id):
        f=request.FILES.get('file'); allowed={'image/jpeg','image/png','image/webp','application/pdf'}
        with transaction.atomic():
            a=scoped(FacultyArrangement.objects.select_for_update().select_related('substitute_faculty__user','schedule_entry__start_slot__template'),request.user).filter(pk=arrangement_id).first()
            if not a or a.substitute_faculty.user_id!=request.user.id:return Response({'detail':'Only the assigned substitute may upload evidence.'},status=403)
            if a.status!='ASSIGNED':return Response({'code':'ARRANGEMENT_NOT_ACTIVE','detail':'Attendance can be uploaded only for an assigned arrangement.'},status=400)
            if a.evidence.exists():return Response({'code':'ATTENDANCE_ALREADY_UPLOADED','detail':'Attendance has already been uploaded for this arrangement.'},status=409)
            available_after=arrangement_class_end(a.schedule_entry,a.arrangement_date)
            if available_after is None:return Response({'code':'CLASS_END_TIME_UNAVAILABLE','detail':'The class end time could not be determined for this arrangement.'},status=400)
            if timezone.now()<available_after:return Response({'code':'ATTENDANCE_UPLOAD_TOO_EARLY','detail':'Attendance can be uploaded only after the arranged class has ended.','available_after':available_after.isoformat()},status=400)
            if not f or f.size>10*1024*1024 or f.content_type not in allowed:return Response({'detail':'Upload a JPG, PNG, WEBP, or PDF up to 10 MB.'},status=400)
            e=ArrangementAttendanceEvidence.objects.create(arrangement=a,file=f,uploaded_by=request.user,original_filename=f.name,mime_type=f.content_type,file_size=f.size)
        from notifications.services import notify_arrangement_attendance_after_commit
        notify_arrangement_attendance_after_commit(a,e,request.user)
        return Response({'id':str(e.pk),'status':'Uploaded','uploaded_at':e.uploaded_at,'original_filename':e.original_filename,'view_url':f'/api/faculty-arrangements/{a.pk}/attendance/{e.pk}/view/'},status=201)
    def get(self,request,arrangement_id,evidence_id):
        e=ArrangementAttendanceEvidence.objects.select_related('arrangement__absent_faculty__user','arrangement__substitute_faculty__user').filter(pk=evidence_id,arrangement_id=arrangement_id).first()
        if not e:return Response({'detail':'Evidence not found.'},status=404)
        allowed=manager(request.user) or e.arrangement.absent_faculty.user_id==request.user.id or e.arrangement.substitute_faculty.user_id==request.user.id
        if not allowed:return Response({'detail':'You do not have permission to view this attendance evidence.'},status=403)
        return FileResponse(e.file.open('rb'),content_type=e.mime_type,as_attachment=request.query_params.get('download')=='1',filename=e.original_filename)

class ReceivedArrangementAttendanceView(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        if request.user.role!=Role.FACULTY:return Response({'detail':'This endpoint is available to Faculty users only.'},status=403)
        faculty=getattr(request.user,'faculty_profile',None)
        if not faculty:return Response({'detail':'No Faculty profile is linked to this account.'},status=404)
        evidence=ArrangementAttendanceEvidence.objects.filter(arrangement__absent_faculty=faculty).select_related(
            'arrangement__absent_faculty__user','arrangement__substitute_faculty__user',
            'arrangement__schedule_entry__start_slot','arrangement__schedule_entry__course_offering__course',
            'arrangement__schedule_entry__section',
        ).order_by('-uploaded_at')
        results=[]
        for item in evidence:
            arrangement=item.arrangement; entry=arrangement.schedule_entry
            results.append({
                'id':str(item.pk),'arrangement_id':str(arrangement.pk),'date':arrangement.arrangement_date,
                'time_slot':{'label':entry.start_slot.label,'start_time':entry.start_slot.start_time,'end_time':entry.start_slot.end_time},
                'course':{'code':entry.course_offering.course.code,'name':entry.course_offering.course.name},
                'section':{'id':str(entry.section_id),'name':entry.section.name},
                'original_faculty':{'id':str(arrangement.absent_faculty_id),'name':faculty_name(arrangement.absent_faculty)},
                'arrangement_faculty':{'id':str(arrangement.substitute_faculty_id),'name':faculty_name(arrangement.substitute_faculty)},
                'attendance_status':'UPLOADED','evidence':{'id':str(item.pk),'original_filename':item.original_filename,'mime_type':item.mime_type,'file_size':item.file_size,'uploaded_at':item.uploaded_at,'view_url':f'/api/faculty-arrangements/{arrangement.pk}/attendance/{item.pk}/view/'},
            })
        return Response({'count':len(results),'results':results})
