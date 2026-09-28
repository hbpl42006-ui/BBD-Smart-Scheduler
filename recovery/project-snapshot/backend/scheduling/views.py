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
        version=TimetableVersion.objects.get(pk=version_id); conflicts=validate_entry(version,request.data,request.data.get('exclude_entry_id')); return Response({'valid':not conflicts,'conflicts':conflicts})
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
        qs=ScheduleEntry.objects.filter(version_id=version_id,section_id=request.query_params.get('section')).select_related('start_slot','room','course_offering__course');return Response({'version':version_id,'entries':ScheduleEntrySerializer(qs,many=True).data})
class FacultyTimetable(SectionTimetable):
    def get(self,request,version_id):
        qs=ScheduleEntry.objects.filter(version_id=version_id,faculty_assignments__faculty_id=request.query_params.get('faculty')).distinct().select_related('start_slot','room','course_offering__course');return Response({'version':version_id,'entries':ScheduleEntrySerializer(qs,many=True).data})
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
