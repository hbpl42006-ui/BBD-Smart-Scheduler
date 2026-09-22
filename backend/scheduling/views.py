from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from scheduling.models import Timetable,TimetableVersion,ScheduleEntry,ScheduleEntryFaculty
from scheduling.serializers import TimetableSerializer,TimetableVersionSerializer,ScheduleEntrySerializer
from scheduling.services.validation import validate_entry,validate_version
from common.permissions import RolePermission
from scheduling.services.audit import record
from accounts.models import Role,User
from notifications.services import notify,notify_published_diff
from faculty.models import Faculty
from drf_spectacular.utils import extend_schema, inline_serializer
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers
from django.utils import timezone
from accounts.models import Role

class TimetableList(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request): return Response(TimetableSerializer(Timetable.objects.select_related('semester','department'),many=True).data)
    def post(self,request):
        if not RolePermission().has_permission(request,self): return Response({'detail':'Permission denied'},403)
        s=TimetableSerializer(data=request.data); s.is_valid(raise_exception=True)
        with transaction.atomic():
            t=s.save(created_by=request.user); version=TimetableVersion.objects.create(timetable=t,version_no=1,created_by=request.user); record('TIMETABLE_CREATED',request.user,timetable=t,version=version,entity_type='Timetable',entity_id=t.pk,new_data={'title':t.title}); record('VERSION_CREATED',request.user,timetable=t,version=version,entity_type='TimetableVersion',entity_id=version.pk)
        return Response(TimetableSerializer(t).data,status=201)
class VersionList(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,timetable_id): return Response(TimetableVersionSerializer(TimetableVersion.objects.filter(timetable_id=timetable_id),many=True).data)
    def post(self,request,timetable_id):
        if not RolePermission().has_permission(request,self): return Response({'detail':'Permission denied'},403)
        source=TimetableVersion.objects.get(timetable_id=timetable_id,pk=request.data.get('source_version')) if request.data.get('source_version') else TimetableVersion.objects.filter(timetable_id=timetable_id).order_by('-version_no').first()
        with transaction.atomic():
            version=TimetableVersion.objects.create(timetable_id=timetable_id,version_no=TimetableVersion.objects.filter(timetable_id=timetable_id).count()+1,created_by=request.user,previous_version=source)
            for entry in source.entries.all():
                clone=ScheduleEntry.objects.create(version=version,section=entry.section,course_offering=entry.course_offering,weekday=entry.weekday,start_slot=entry.start_slot,block_length=entry.block_length,room=entry.room,entry_type=entry.entry_type,locked=entry.locked,note=entry.note); clone.faculty_assignments.set(entry.faculty_assignments.all())
            record('VERSION_CLONED',request.user,timetable=version.timetable,version=version,entity_type='TimetableVersion',entity_id=version.pk,metadata={'source_version':str(source.pk)})
        return Response(TimetableVersionSerializer(version).data,status=201)
class EntryList(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,version_id): return Response(ScheduleEntrySerializer(ScheduleEntry.objects.filter(version_id=version_id).select_related('course_offering__course','section','room','start_slot'),many=True).data)
    def post(self,request,version_id):
        if not RolePermission().has_permission(request,self): return Response({'detail':'Permission denied'},403)
        version=TimetableVersion.objects.get(pk=version_id)
        if version.status!='DRAFT': return Response({'detail':'Only draft versions can be changed.'},409)
        conflicts=validate_entry(version,request.data)
        if conflicts:return Response({'valid':False,'conflicts':conflicts},400)
        data={k:v for k,v in request.data.items() if k not in ('faculty_assignments','faculty_ids')};s=ScheduleEntrySerializer(data={**data,'version':str(version.pk)});s.is_valid(raise_exception=True); entry=s.save(); self._save_faculty(entry,request.data); record('ENTRY_CREATED',request.user,timetable=version.timetable,version=version,entity_type='ScheduleEntry',entity_id=entry.pk,new_data=ScheduleEntrySerializer(entry).data); return Response(ScheduleEntrySerializer(entry).data,status=201)
    @staticmethod
    def _save_faculty(entry,data):
        assignments=data.get('faculty_assignments')
        if assignments is None and data.get('faculty_ids') is not None: assignments=[{'faculty_id':x,'role':'PRIMARY' if i==0 else 'CO_FACULTY'} for i,x in enumerate(data['faculty_ids'])]
        if assignments is not None:
            ScheduleEntryFaculty.objects.filter(schedule_entry=entry).delete()
            ScheduleEntryFaculty.objects.bulk_create([ScheduleEntryFaculty(schedule_entry=entry,faculty_id=x['faculty_id'],role=x.get('role','PRIMARY')) for x in assignments])
class EntryDetail(APIView):
    permission_classes=[IsAuthenticated]
    def patch(self,request,version_id,entry_id):
        if not RolePermission().has_permission(request,self): return Response({'detail':'Permission denied'},403)
        entry=ScheduleEntry.objects.get(pk=entry_id,version_id=version_id)
        if entry.locked:return Response({'detail':'Locked entries cannot be edited.'},409)
        if entry.version.status!='DRAFT':return Response({'detail':'Only drafts can be edited.'},409)
        data={**{'section':entry.section_id,'course_offering':entry.course_offering_id,'weekday':entry.weekday,'start_slot':entry.start_slot_id,'block_length':entry.block_length,'room':entry.room_id},**request.data}; conflicts=validate_entry(entry.version,data,entry.pk)
        if conflicts:return Response({'valid':False,'conflicts':conflicts},400)
        old=ScheduleEntrySerializer(entry).data;data={k:v for k,v in request.data.items() if k not in ('faculty_assignments','faculty_ids')};s=ScheduleEntrySerializer(entry,data=data,partial=True);s.is_valid(raise_exception=True);entry=s.save();EntryList._save_faculty(entry,request.data);record('ENTRY_UPDATED',request.user,timetable=entry.version.timetable,version=entry.version,entity_type='ScheduleEntry',entity_id=entry.pk,old_data=old,new_data=ScheduleEntrySerializer(entry).data);return Response(ScheduleEntrySerializer(entry).data)
    def delete(self,request,version_id,entry_id):
        if not RolePermission().has_permission(request,self):return Response({'detail':'Permission denied'},403)
        entry=ScheduleEntry.objects.get(pk=entry_id,version_id=version_id)
        if entry.locked or entry.version.status!='DRAFT':return Response({'detail':'Entry cannot be deleted.'},409)
        record('ENTRY_DELETED',request.user,timetable=entry.version.timetable,version=entry.version,entity_type='ScheduleEntry',entity_id=entry.pk,old_data=ScheduleEntrySerializer(entry).data);entry.delete();return Response(status=204)
class ValidateEntry(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request,version_id):
        version=TimetableVersion.objects.get(pk=version_id)
        # The builder sends the edited ScheduleEntry as `id`. Keep the older
        # explicit field as a compatibility fallback for existing clients.
        current_entry_id=request.data.get('id') or request.data.get('exclude_entry_id')
        conflicts=validate_entry(version,request.data,current_entry_id)
        return Response({'valid':not conflicts,'conflicts':conflicts})
class EntryLock(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request,entry_id):
        if not RolePermission().has_permission(request,self):return Response({'detail':'Permission denied'},403)
        entry=ScheduleEntry.objects.get(pk=entry_id); entry.locked=True;entry.save(update_fields=['locked']);record('ENTRY_LOCKED',request.user,timetable=entry.version.timetable,version=entry.version,entity_type='ScheduleEntry',entity_id=entry.pk);return Response(ScheduleEntrySerializer(entry).data)
class EntryUnlock(EntryLock):
    def post(self,request,entry_id):
        if not RolePermission().has_permission(request,self):return Response({'detail':'Permission denied'},403)
        entry=ScheduleEntry.objects.get(pk=entry_id);entry.locked=False;entry.save(update_fields=['locked']);record('ENTRY_UNLOCKED',request.user,timetable=entry.version.timetable,version=entry.version,entity_type='ScheduleEntry',entity_id=entry.pk);return Response(ScheduleEntrySerializer(entry).data)
class SectionTimetable(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,version_id):
        section_id=request.query_params.get('section')
        if getattr(request.user,'role',None)==Role.FACULTY and not ScheduleEntryFaculty.objects.filter(schedule_entry__version_id=version_id,schedule_entry__section_id=section_id,faculty__user=request.user).exists(): return Response({'detail':'You do not have access to this section timetable.'},status=403)
        qs=ScheduleEntry.objects.filter(version_id=version_id,section_id=section_id).select_related('start_slot','room','course_offering__course');return Response({'version':version_id,'entries':ScheduleEntrySerializer(qs,many=True).data})
class FacultyTimetable(SectionTimetable):
    def get(self,request,version_id):
        qs=ScheduleEntry.objects.filter(version_id=version_id,faculty_assignments__faculty_id=request.query_params.get('faculty')).distinct().select_related('start_slot','room','course_offering__course');return Response({'version':version_id,'entries':ScheduleEntrySerializer(qs,many=True).data})
class MyFacultyTimetable(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        faculty=getattr(request.user,'faculty_profile',None)
        if not faculty:return Response({'detail':'No Faculty profile is linked to this account.'},status=404)
        timetable=Timetable.objects.filter(active=True,department=faculty.department).order_by('-updated_at').first()
        if not timetable:return Response({'faculty':{'id':str(faculty.id),'name':faculty.initials or faculty.employee_code},'entries':[]})
        version=TimetableVersion.objects.filter(timetable=timetable,status='PUBLISHED').order_by('-version_no').first()
        if not version:return Response({'faculty':{'id':str(faculty.id),'name':faculty.initials or faculty.employee_code},'timetable':{'id':str(timetable.id),'title':timetable.title},'entries':[],'message':'No published timetable is available yet.'})
        qs=ScheduleEntry.objects.filter(version=version,faculty_assignments__faculty=faculty).distinct().select_related('start_slot','room','section','course_offering__course')
        user_name=f'{faculty.user.first_name} {faculty.user.last_name}'.strip() if faculty.user else ''
        sections=list(version.entries.filter(faculty_assignments__faculty=faculty).select_related('section__program','section__semester').values('section_id','section__name','section__program__name','section__semester__name').distinct())
        return Response({'faculty':{'id':str(faculty.id),'name':user_name or faculty.initials or faculty.employee_code,'department':faculty.department.name},'timetable':{'id':str(timetable.id),'title':timetable.title,'academic_session':timetable.academic_session.name,'semester':timetable.semester.name},'version':{'id':str(version.id),'version_no':version.version_no,'status':version.status},'sections':[{'id':str(row['section_id']),'name':row['section__name'],'program_name':row['section__program__name'],'semester_name':row['section__semester__name']} for row in sections],'entries':ScheduleEntrySerializer(qs,many=True).data})
class RoomAllocation(SectionTimetable):
    def get(self,request,version_id):
        qs=ScheduleEntry.objects.filter(version_id=version_id).select_related('start_slot','room','section','course_offering__course');return Response({'version':version_id,'entries':ScheduleEntrySerializer(qs,many=True).data})
class AvailableRooms(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,version_id):
        from rooms.models import Room
        from common.models import TimeSlot
        weekday=request.query_params.get('weekday'); start_id=request.query_params.get('start_slot'); block=int(request.query_params.get('block_length',1)); start=TimeSlot.objects.filter(pk=start_id).first(); slots=list(TimeSlot.objects.filter(template=start.template,order__gte=start.order).order_by('order')[:block]) if start else []; occupied=set()
        for entry in ScheduleEntry.objects.filter(version_id=version_id,weekday=weekday).select_related('start_slot'):
            used=TimeSlot.objects.filter(template=entry.start_slot.template,order__gte=entry.start_slot.order).order_by('order').values_list('pk',flat=True)[:entry.block_length]
            if set(used)&{x.pk for x in slots}: occupied.add(entry.room_id)
        qs=Room.objects.filter(active=True,capacity__gte=request.query_params.get('capacity',0)).exclude(pk__in=occupied); room_type=request.query_params.get('room_type');
        if room_type: qs=qs.filter(room_type=room_type)
        if slots: qs=qs.exclude(availabilities__weekday=weekday,availabilities__time_slot__in=slots,availabilities__status__in=['BLOCKED','MAINTENANCE'])
        return Response([{'id':str(r.id),'code':r.code,'building':r.building,'floor':r.floor,'capacity':r.capacity,'room_type':r.room_type} for r in qs])
class ValidateVersion(APIView):
    permission_classes=[IsAuthenticated]
    def post(self,request,version_id): return Response(validate_version(TimetableVersion.objects.get(pk=version_id)))

def _can(request, roles):
    return request.user.is_superuser or request.user.role in roles

def _validation(version):
    result=validate_version(version)
    if not version.entries.exists():
        result['valid']=False; result['conflicts'].append({'type':'EMPTY_TIMETABLE','severity':'ERROR','message':'Timetable must contain at least one schedule entry.'})
    return result

class VersionLifecycle(APIView):
    permission_classes=[IsAuthenticated]
    action=None
    def post(self,request,version_id):
        with transaction.atomic():
            version=TimetableVersion.objects.select_for_update().select_related('timetable').get(pk=version_id)
            now=timezone.now()
            if self.action=='submit':
                if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.TIMETABLE_COORDINATOR}): return Response({'detail':'You do not have permission to submit timetables.'},403)
                if version.status!='DRAFT': return Response({'detail':'Only draft versions can be submitted.'},400)
                validation=_validation(version)
                if not validation['valid']: return Response({'detail':'Timetable has blocking conflicts.','validation':validation},400)
                version.status='IN_REVIEW';version.submitted_by=request.user;version.submitted_at=now;version.save(update_fields=['status','submitted_by','submitted_at','updated_at']);record('TIMETABLE_SUBMITTED',request.user,timetable=version.timetable,version=version,entity_type='TimetableVersion',entity_id=version.pk)
                reviewers=User.objects.filter(role__in=[Role.HOD_OR_DEAN_APPROVER,Role.ACADEMIC_ADMIN,Role.SUPER_ADMIN],is_active=True).exclude(pk=request.user.pk);notify(event_type='TIMETABLE_AWAITING_REVIEW',recipients=reviewers,title='Timetable awaiting review',message=f'Timetable v{version.version_no} is awaiting your review.',action_url=f'/timetables/{version.timetable_id}/versions',metadata={'version_id':str(version.pk)},actor=request.user)
            elif self.action in ('approve','reject'):
                if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.HOD_OR_DEAN_APPROVER}): return Response({'detail':'You do not have approval permission.'},403)
                if version.status!='IN_REVIEW': return Response({'detail':'Only versions in review can be reviewed.'},400)
                if self.action=='approve':
                    validation=_validation(version)
                    if not validation['valid']: return Response({'detail':'Timetable has blocking conflicts.','validation':validation},400)
                    version.status='APPROVED';version.approved_by=request.user;version.approved_at=now;version.save(update_fields=['status','approved_by','approved_at','updated_at']);record('TIMETABLE_APPROVED',request.user,timetable=version.timetable,version=version,entity_type='TimetableVersion',entity_id=version.pk)
                    coordinators=User.objects.filter(role=Role.TIMETABLE_COORDINATOR,is_active=True);notify(event_type='TIMETABLE_APPROVED',recipients=coordinators,title='BBD Smart Scheduler - Timetable Approved',message=f'Timetable v{version.version_no} has been approved.',action_url=f'/timetables/{version.timetable_id}/versions',metadata={'version_id':str(version.pk)},actor=request.user)
                else:
                    reason=str(request.data.get('reason','')).strip()
                    if not reason:return Response({'detail':'A rejection reason is required.'},400)
                    version.status='DRAFT';version.rejected_by=request.user;version.rejected_at=now;version.rejection_reason=reason;version.save(update_fields=['status','rejected_by','rejected_at','rejection_reason','updated_at']);record('TIMETABLE_REJECTED',request.user,timetable=version.timetable,version=version,entity_type='TimetableVersion',entity_id=version.pk,metadata={'reason':reason})
                    coordinators=User.objects.filter(role=Role.TIMETABLE_COORDINATOR,is_active=True);notify(event_type='TIMETABLE_REJECTED',recipients=coordinators,title='BBD Smart Scheduler - Timetable Rejected',message=f'Timetable v{version.version_no} was rejected. Reason: {reason}',action_url=f'/timetables/{version.timetable_id}/versions',metadata={'version_id':str(version.pk),'reason':reason},actor=request.user)
            elif self.action=='publish':
                if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN}): return Response({'detail':'You do not have publishing permission.'},403)
                if version.status!='APPROVED': return Response({'detail':'Only approved versions can be published.'},400)
                validation=_validation(version)
                if not validation['valid']: return Response({'detail':'Timetable has blocking conflicts.','validation':validation},400)
                previous_published=list(TimetableVersion.objects.filter(timetable=version.timetable,status='PUBLISHED').exclude(pk=version.pk))
                for archived in previous_published:
                    archived.status='ARCHIVED'; archived.save(update_fields=['status','updated_at'])
                    record('TIMETABLE_ARCHIVED',request.user,timetable=version.timetable,version=archived,entity_type='TimetableVersion',entity_id=archived.pk,metadata={'superseded_by_version_id':str(version.pk),'superseded_by_version_number':version.version_no})
                version.status='PUBLISHED';version.published_by=request.user;version.published_at=now;version.save(update_fields=['status','published_by','published_at','updated_at']);record('TIMETABLE_PUBLISHED',request.user,timetable=version.timetable,version=version,entity_type='TimetableVersion',entity_id=version.pk)
                transaction.on_commit(lambda old=previous_published[0] if previous_published else None,new=version,actor=request.user: notify_published_diff(old,new,actor))
            else:return Response({'detail':'Unsupported lifecycle action.'},400)
        return Response(TimetableVersionSerializer(version).data)

class VersionAudit(APIView):
    permission_classes=[IsAuthenticated]
    @extend_schema(responses=inline_serializer(name='VersionHistoryEvent',fields={'id':serializers.UUIDField(),'event_type':serializers.CharField(),'actor_id':serializers.UUIDField(allow_null=True),'actor_name':serializers.CharField(allow_null=True),'actor_email':serializers.CharField(allow_null=True),'metadata':serializers.DictField(),'created_at':serializers.DateTimeField()}))
    def get(self,request,version_id):
        if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.TIMETABLE_COORDINATOR,Role.HOD_OR_DEAN_APPROVER}): return Response({'detail':'You do not have permission to view lifecycle history.'},403)
        from audit.models import AuditEvent
        events=AuditEvent.objects.filter(version_id=version_id).select_related('actor')
        return Response([{'id':str(event.id),'event_type':event.event_type,'actor_id':str(event.actor_id) if event.actor_id else None,'actor_name':(f'{event.actor.first_name} {event.actor.last_name}'.strip() or event.actor.email) if event.actor else None,'actor_email':event.actor.email if event.actor else None,'metadata':event.metadata,'created_at':event.created_at} for event in events])

class CreateDraft(APIView):
    permission_classes=[IsAuthenticated]
    @extend_schema(request=None,responses=TimetableVersionSerializer)
    def post(self,request,version_id):
        if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.TIMETABLE_COORDINATOR}): return Response({'detail':'You do not have permission to create timetable drafts.'},403)
        with transaction.atomic():
            source=TimetableVersion.objects.select_for_update().select_related('timetable').get(pk=version_id)
            if source.status!='PUBLISHED': return Response({'detail':'Only published versions can be used to create a new draft.'},400)
            latest=TimetableVersion.objects.select_for_update().filter(timetable=source.timetable).order_by('-version_no').first()
            version=TimetableVersion.objects.create(timetable=source.timetable,version_no=(latest.version_no+1 if latest else 1),created_by=request.user,previous_version=source,notes=f'Created from version {source.version_no}')
            for entry in source.entries.all():
                clone=ScheduleEntry.objects.create(version=version,section=entry.section,course_offering=entry.course_offering,weekday=entry.weekday,start_slot=entry.start_slot,block_length=entry.block_length,room=entry.room,entry_type=entry.entry_type,locked=entry.locked,note=entry.note)
                ScheduleEntryFaculty.objects.bulk_create([ScheduleEntryFaculty(schedule_entry=clone,faculty=a.faculty,role=a.role) for a in entry.faculty_assignments.all()])
            record('TIMETABLE_DRAFT_CREATED',request.user,timetable=source.timetable,version=version,entity_type='TimetableVersion',entity_id=version.pk,metadata={'source_version_id':str(source.pk),'new_version_id':str(version.pk),'source_version_number':source.version_no,'new_version_number':version.version_no})
        return Response(TimetableVersionSerializer(version).data,status=201)

class VersionCompare(APIView):
    permission_classes=[IsAuthenticated]
    @extend_schema(responses=inline_serializer(name='VersionComparison',fields={'from_version':serializers.DictField(),'to_version':serializers.DictField(),'summary':serializers.DictField(),'added':serializers.ListField(child=serializers.DictField()),'removed':serializers.ListField(child=serializers.DictField()),'changed':serializers.ListField(child=serializers.DictField())}))
    def get(self,request):
        if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.TIMETABLE_COORDINATOR,Role.HOD_OR_DEAN_APPROVER}): return Response({'detail':'You do not have permission to compare timetable versions.'},403)
        left=TimetableVersion.objects.filter(pk=request.query_params.get('from')).first();right=TimetableVersion.objects.filter(pk=request.query_params.get('to')).first()
        if not left or not right:return Response({'detail':'Both versions are required.'},400)
        def rows(version):
            return {(str(e.section_id),str(e.course_offering_id)):{'section':e.section.name,'course':e.course_offering.course.code,'weekday':e.weekday,'start_slot':str(e.start_slot_id),'room':str(e.room_id) if e.room_id else None,'faculty':sorted(str(x) for x in e.faculty_assignments.values_list('faculty_id',flat=True)),'block_length':e.block_length,'entry_type':e.entry_type} for e in version.entries.select_related('section','course_offering__course')}
        a,b=rows(left),rows(right);added=[b[k] for k in b.keys()-a.keys()];removed=[a[k] for k in a.keys()-b.keys()];changed=[{'before':a[k],'after':b[k]} for k in a.keys()&b.keys() if a[k]!=b[k]]
        return Response({'from_version':{'id':str(left.pk),'version_no':left.version_no,'status':left.status},'to_version':{'id':str(right.pk),'version_no':right.version_no,'status':right.status},'summary':{'added':len(added),'removed':len(removed),'changed':len(changed)},'added':added,'removed':removed,'changed':changed})

class ApprovalQueue(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request):
        if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.HOD_OR_DEAN_APPROVER}): return Response({'detail':'You do not have approval permission.'},403)
        versions=TimetableVersion.objects.filter(status='IN_REVIEW').select_related('timetable__department','timetable__academic_session','timetable__semester','submitted_by')
        return Response([{'id':str(v.pk),'timetable':str(v.timetable_id),'title':v.timetable.title,'version_no':v.version_no,'status':v.status,'department':v.timetable.department.name,'academic_session':v.timetable.academic_session.name,'semester':v.timetable.semester.name,'submitted_by':f'{v.submitted_by.first_name} {v.submitted_by.last_name}'.strip() or v.submitted_by.email if v.submitted_by else '—','submitted_at':v.submitted_at,'entry_count':v.entries.count()} for v in versions])

class VersionReview(APIView):
    permission_classes=[IsAuthenticated]
    def get(self,request,version_id):
        if not _can(request,{Role.SUPER_ADMIN,Role.ACADEMIC_ADMIN,Role.HOD_OR_DEAN_APPROVER}): return Response({'detail':'You do not have approval permission.'},403)
        v=TimetableVersion.objects.select_related('timetable','submitted_by').get(pk=version_id)
        name=f'{v.submitted_by.first_name} {v.submitted_by.last_name}'.strip() if v.submitted_by else None
        return Response({'id':str(v.pk),'timetable':v.timetable.title,'timetable_id':str(v.timetable_id),'source_version':str(v.pk),'version_no':v.version_no,'status':v.status,'created_at':v.created_at,'submitted_at':v.submitted_at,'submitted_by':name,'entry_count':v.entries.count()})
