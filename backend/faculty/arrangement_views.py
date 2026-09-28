from datetime import timedelta
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.models import Role
from common.models import TimeSlot
from scheduling.models import ScheduleEntry, TimetableVersion
from .models import Faculty, FacultyAvailability, FacultyArrangement, ArrangementAttendanceEvidence

MANAGERS={Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.TIMETABLE_COORDINATOR,Role.HOD_OR_DEAN_APPROVER}
def manager(user): return user.is_superuser or user.role in MANAGERS
def faculty_name(f): return (f'{f.user.first_name} {f.user.last_name}'.strip() if f.user else '') or f.name or f.initials or f.employee_code
def arrangement_row(a):
    e=a.schedule_entry
    return {'id':str(a.pk),'schedule_entry':str(a.schedule_entry_id),'arrangement_date':a.arrangement_date,'day':e.get_weekday_display(),'weekday':e.weekday,'time_slot':e.start_slot.label,'course_code':e.course_offering.course.code,'course_name':e.course_offering.course.name,'section':str(e.section),'section_id':str(e.section_id),'room':e.room.code if e.room else '','absent_faculty':faculty_name(a.absent_faculty),'absent_faculty_id':str(a.absent_faculty_id),'substitute_faculty':faculty_name(a.substitute_faculty),'substitute_faculty_id':str(a.substitute_faculty_id),'reason':a.reason,'status':a.status,'attendance':'Uploaded' if a.evidence.exists() else 'Pending','created_by':a.created_by.email,'created_at':a.created_at}

def scoped(qs,user):
    if user.role=='FACULTY': return qs.filter(substitute_faculty__user=user)
    if user.role=='HOD_OR_DEAN_APPROVER': return qs.filter(schedule_entry__version__timetable__department__faculties__user=user).distinct()
    return qs
def candidates(date, entry, user):
    busy=ScheduleEntry.objects.filter(version__status='PUBLISHED',weekday=entry.weekday,start_slot=entry.start_slot,section__isnull=False).values_list('faculty_assignments__faculty_id',flat=True)
    arrangements=FacultyArrangement.objects.filter(arrangement_date=date,status__in=['ASSIGNED','COMPLETED'],schedule_entry__weekday=entry.weekday,schedule_entry__start_slot=entry.start_slot).values_list('substitute_faculty_id',flat=True)
    blocked=set(Faculty.objects.filter(availabilities__weekday=entry.weekday,availabilities__time_slot=entry.start_slot,availabilities__is_available=False).values_list('pk',flat=True))
    assigned=entry.faculty_assignments.values_list('faculty_id',flat=True)
    qs=Faculty.objects.select_related('user','department').exclude(pk__in=assigned).exclude(pk__in=busy).exclude(pk__in=arrangements).exclude(pk__in=blocked)
    return [{'id':str(f.pk),'name':faculty_name(f),'employee_code':f.employee_code,'department':f.department.name,
             'normal_classes_today':ScheduleEntry.objects.filter(version__status='PUBLISHED',weekday=entry.weekday,faculty_assignments__faculty=f).distinct().count(),
             'arrangements_today':FacultyArrangement.objects.filter(arrangement_date=date,substitute_faculty=f,status__in=['ASSIGNED','COMPLETED']).count(),
             'arrangements_this_week':FacultyArrangement.objects.filter(arrangement_date__gte=date-timedelta(days=date.weekday()),arrangement_date__lte=date+timedelta(days=6-date.weekday()),substitute_faculty=f,status__in=['ASSIGNED','COMPLETED']).count()} for f in qs.order_by('name','employee_code')]

def validate_arrangement(data, user, date, absent, entry, substitute, reserved=None):
    if not entry or entry.version.status!='PUBLISHED': return 'Schedule entry is not from a published timetable.'
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
        return Response([arrangement_row(a) for a in qs.order_by('-arrangement_date','schedule_entry__weekday','schedule_entry__start_slot__order')])
    def post(self,request):
        if not manager(request.user): return Response({'detail':'You do not have permission to create arrangements.'},status=403)
        data=request.data; entry=ScheduleEntry.objects.filter(pk=data.get('schedule_entry'),version__status='PUBLISHED').first(); absent=Faculty.objects.filter(pk=data.get('absent_faculty')).first(); substitute=Faculty.objects.filter(pk=data.get('substitute_faculty')).first()
        if not entry or not absent or not substitute:return Response({'detail':'A published schedule entry and valid faculty are required.'},status=400)
        from datetime import date
        try: day=date.fromisoformat(str(data.get('arrangement_date')))
        except ValueError:return Response({'detail':'arrangement_date must be YYYY-MM-DD.'},status=400)
        error=validate_arrangement(data,request.user,day,absent,entry,substitute)
        if error:return Response({'detail':error},status=400)
        a=FacultyArrangement.objects.create(schedule_entry=entry,arrangement_date=day,absent_faculty=absent,substitute_faculty=substitute,reason=data.get('reason',''),created_by=request.user)
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
        if not manager(request.user): return Response({'detail':'Permission denied.'},status=403)
        from datetime import date
        try: day=date.fromisoformat(str(request.query_params['date']))
        except (KeyError,ValueError): return Response({'detail':'A valid date is required.'},status=400)
        entry=ScheduleEntry.objects.filter(pk=request.query_params.get('schedule_entry'),version__status='PUBLISHED').first()
        if not entry:return Response({'detail':'Published schedule entry not found.'},status=404)
        return Response(candidates(day,entry,request.user))

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
        a=scoped(FacultyArrangement.objects.select_related('substitute_faculty__user'),request.user).filter(pk=arrangement_id).first()
        if not a or a.substitute_faculty.user_id!=request.user.id:return Response({'detail':'Only the assigned substitute may upload evidence.'},status=403)
        f=request.FILES.get('file'); allowed={'image/jpeg','image/png','image/webp','application/pdf'}
        if not f or f.size>10*1024*1024 or f.content_type not in allowed:return Response({'detail':'Upload a JPG, PNG, WEBP, or PDF up to 10 MB.'},status=400)
        e=ArrangementAttendanceEvidence.objects.create(arrangement=a,file=f,uploaded_by=request.user,original_filename=f.name,mime_type=f.content_type,file_size=f.size);return Response({'id':str(e.pk),'status':'Uploaded'},status=201)
    def get(self,request,arrangement_id,evidence_id):
        if not manager(request.user) or request.user.role not in {Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.TIMETABLE_COORDINATOR,Role.HOD_OR_DEAN_APPROVER}:return Response({'detail':'Evidence access is restricted.'},status=403)
        e=ArrangementAttendanceEvidence.objects.filter(pk=evidence_id,arrangement_id=arrangement_id).first()
        if not e:return Response({'detail':'Evidence not found.'},status=404)
        return FileResponse(e.file.open('rb'),content_type=e.mime_type)
