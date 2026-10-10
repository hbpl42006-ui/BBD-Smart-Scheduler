from datetime import date

from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError as DRFValidationError

from accounts.models import Role
from common.models import TimeSlot
from scheduling.models import TemporaryScheduleBlock, TemporarySchedulePlan, TimetableVersion
from scheduling.services.audit import record
from scheduling.services.temporary_schedules import plan_impact
from academics.models import Section
from rooms.models import Room
from faculty.models import Faculty

MANAGERS = {Role.SUPER_ADMIN, Role.ACADEMIC_ADMIN, Role.TIMETABLE_COORDINATOR}
PUBLISHERS = {Role.SUPER_ADMIN, Role.ACADEMIC_ADMIN}


def _can_manage(request):
    return request.user.is_superuser or request.user.role in MANAGERS


def _sections_in_version(section_ids, version):
    if not isinstance(section_ids, list): return None
    try:
        found = Section.objects.filter(pk__in=section_ids, semester=version.timetable.semester)
        return found if found.count() == len(set(section_ids)) else None
    except (ValidationError, ValueError, TypeError):
        return None


def _pattern_blocks(plan, patterns, sections, version):
    if not isinstance(patterns,list) or not patterns:
        raise DRFValidationError({'patterns':'Add at least one valid schedule pattern.'})
    template_ids=set(version.entries.values_list('start_slot__template_id',flat=True).distinct())
    if len(template_ids)!=1:
        raise DRFValidationError({'version':'The published version must use exactly one canonical time-slot template.'})
    calendar=list(TimeSlot.objects.filter(template_id=next(iter(template_ids))).order_by('order','pk'))
    if not calendar:
        raise DRFValidationError({'version':'The published version has no canonical time slots.'})
    by_id={str(slot.pk):index for index,slot in enumerate(calendar)}
    created=[]
    for index,pattern in enumerate(patterns):
        prefix=f'patterns[{index}]'
        if not isinstance(pattern,dict):
            raise DRFValidationError({'patterns':f'{prefix} must be an object.'})
        start_index=by_id.get(str(pattern.get('start_slot')))
        end_index=by_id.get(str(pattern.get('end_slot')))
        if start_index is None or end_index is None or end_index<start_index:
            raise DRFValidationError({'patterns':f'{prefix}: choose a valid start and later end time.'})
        window=calendar[start_index:end_index+1]
        for slot in window:
            if slot.is_break:
                raise DRFValidationError({'patterns':f'{prefix}: selected range crosses break {slot.label}. Split the pattern around the break.'})
        if any(window[pos].end_time!=window[pos+1].start_time for pos in range(len(window)-1)):
            raise DRFValidationError({'patterns':f'{prefix}: selected range crosses a gap in the canonical timetable.'})
        mode=pattern.get('mode','weekday')
        if mode=='date':
            try: specific_date=date.fromisoformat(pattern.get('specific_date',''))
            except (TypeError,ValueError): raise DRFValidationError({'patterns':f'{prefix}: choose a valid specific date.'}) from None
            if not plan.start_date<=specific_date<=plan.end_date:
                raise DRFValidationError({'patterns':f'{prefix}: specific date must be within the plan date range.'})
            recurrences=[{'specific_date':specific_date,'weekday':None}]
        elif mode=='weekday':
            weekdays=pattern.get('weekdays')
            if not isinstance(weekdays,list) or not weekdays:
                raise DRFValidationError({'patterns':f'{prefix}: choose at least one weekday.'})
            try: weekdays=list(dict.fromkeys(int(day) for day in weekdays))
            except (TypeError,ValueError): raise DRFValidationError({'patterns':f'{prefix}: weekdays must be Monday-Friday.'}) from None
            if any(day not in range(5) for day in weekdays):
                raise DRFValidationError({'patterns':f'{prefix}: weekdays must be Monday-Friday.'})
            recurrences=[{'specific_date':None,'weekday':day} for day in weekdays]
        else:
            raise DRFValidationError({'patterns':f'{prefix}: mode must be weekday or date.'})
        room_id=pattern.get('room_id') or pattern.get('room') or None
        if room_id and len(sections)>1:
            raise DRFValidationError({'room':'A shared room cannot be assigned to simultaneous section blocks. Choose no room / assign later, or select one section.'})
        try: room=Room.objects.get(pk=room_id) if room_id else None
        except (Room.DoesNotExist,ValidationError,ValueError,TypeError):
            raise DRFValidationError({'room':'Choose a valid room.'}) from None
        delivery=pattern.get('delivery_mode','OFFLINE')
        if delivery not in ('OFFLINE','ONLINE'):
            raise DRFValidationError({'delivery_mode':'Choose OFFLINE or ONLINE.'})
        if delivery=='ONLINE' and room:
            raise DRFValidationError({'room':'Online patterns cannot reserve a physical room.'})
        faculty_ids=pattern.get('faculty',[])
        if not isinstance(faculty_ids,list) or Faculty.objects.filter(pk__in=faculty_ids).count()!=len(set(map(str,faculty_ids))):
            raise DRFValidationError({'faculty':'Choose valid faculty records.'})
        for section in sections:
            for recurrence in recurrences:
                block=TemporaryScheduleBlock.objects.create(plan=plan,section=section,
                    specific_date=recurrence['specific_date'],weekday=recurrence['weekday'],
                    start_slot=window[0],block_length=len(window),event_type=pattern.get('event_type',plan.event_type),
                    title=pattern.get('title',plan.title),room=room,trainer_name=pattern.get('trainer_name',''),
                    delivery_mode=delivery,override_regular_class=bool(pattern.get('override_regular_class',False)))
                block.faculty.set(faculty_ids)
                created.append(block)
    return created


def _plan_data(plan):
    from scheduling.services.temporary_schedules import academic_today
    impact = plan.published_impact if plan.published_impact else None
    return {'id': str(plan.pk), 'title': plan.title, 'event_type': plan.event_type, 'status': plan.status,
            'display_status': 'EXPIRED' if plan.status == 'PUBLISHED' and plan.end_date < academic_today() else plan.status,
            'base_timetable_version': str(plan.base_timetable_version_id), 'start_date': plan.start_date.isoformat(),
            'end_date': plan.end_date.isoformat(), 'priority': plan.priority, 'notes': plan.notes,
            'sections': [str(pk) for pk in plan.sections.values_list('pk', flat=True)],
            'created_by': plan.created_by.email, 'created_at': plan.created_at.isoformat(),
            'published_at': plan.published_at.isoformat() if plan.published_at else None,
            'affected_regular_periods': impact.get('affected_regular_periods') if impact else None,
            'blocks': [_block_data(block) for block in plan.blocks.prefetch_related('faculty').select_related('start_slot','room')]}


def _block_data(block):
    return {'id': str(block.pk), 'section': str(block.section_id), 'specific_date': block.specific_date.isoformat() if block.specific_date else None,
            'weekday': block.weekday, 'start_slot': str(block.start_slot_id), 'block_length': block.block_length,
            'event_type': block.event_type, 'title': block.title, 'room': str(block.room_id) if block.room_id else None,
            'faculty': list(block.faculty.values_list('pk', flat=True)), 'trainer_name': block.trainer_name,
            'delivery_mode': block.delivery_mode, 'override_regular_class': block.override_regular_class, 'notes': block.notes}


class TemporaryPlanList(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        plans = TemporarySchedulePlan.objects.select_related('base_timetable_version').prefetch_related('sections').all()
        if request.query_params.get('version'):
            plans = plans.filter(base_timetable_version_id=request.query_params['version'])
        return Response([_plan_data(plan) for plan in plans])
    def post(self, request):
        if not _can_manage(request): return Response({'detail':'Permission denied.'}, status=403)
        version = TimetableVersion.objects.filter(pk=request.data.get('base_timetable_version'), status='PUBLISHED').first()
        if not version: return Response({'detail':'Select a published timetable version.'}, status=400)
        try:
            start, end = date.fromisoformat(request.data['start_date']), date.fromisoformat(request.data['end_date'])
        except (KeyError, ValueError, TypeError):
            return Response({'start_date':'Valid start and end dates are required.'}, status=400)
        if start > end or not request.data.get('title','').strip():
            return Response({'detail':'A title and valid date range are required.'},status=400)
        if request.data.get('event_type','OTHER') not in TemporarySchedulePlan.EventType.values:
            return Response({'detail':'Invalid event type.'},status=400)
        chosen=request.data.get('sections',[])
        sections=_sections_in_version(chosen,version)
        if sections is None:
            return Response({'sections':'Select valid sections from the published timetable semester.'},status=400)
        sections=list(sections)
        if not sections:
            return Response({'sections':'Select at least one section.'},status=400)
        if 'patterns' in request.data and not request.data.get('patterns'):
            return Response({'patterns':'Add at least one schedule pattern.'},status=400)
        try:
            priority=int(request.data.get('priority',0))
            if priority < 0: raise ValueError
        except (ValueError,TypeError): return Response({'detail':'Priority must be a non-negative integer.'},status=400)
        try:
            with transaction.atomic():
                plan = TemporarySchedulePlan.objects.create(base_timetable_version=version, title=request.data.get('title','').strip(),
                    event_type=request.data.get('event_type',TemporarySchedulePlan.EventType.OTHER), start_date=start, end_date=end,
                    priority=priority, notes=request.data.get('notes',''), created_by=request.user)
                plan.sections.set(sections)
                if 'patterns' in request.data:
                    _pattern_blocks(plan,request.data['patterns'],sections,version)
        except DRFValidationError as error:
            return Response(error.detail,status=400)
        except (ValidationError,ValueError,TypeError) as error:
            return Response({'detail':f'Invalid schedule pattern: {error}'},status=400)
        return Response(_plan_data(plan), status=201)


class TemporaryPlanDetail(APIView):
    permission_classes = [IsAuthenticated]
    def get_object(self, plan_id): return TemporarySchedulePlan.objects.filter(pk=plan_id).first()
    def get(self, request, plan_id):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        plan=self.get_object(plan_id); return Response(_plan_data(plan)) if plan else Response({'detail':'Plan not found.'},status=404)
    def patch(self, request, plan_id):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        plan=self.get_object(plan_id)
        if not plan: return Response({'detail':'Plan not found.'},status=404)
        if plan.status != 'DRAFT': return Response({'detail':'Only draft plans can be edited.'},status=409)
        for key in ('title','event_type','notes'):
            if key in request.data: setattr(plan,key,request.data[key])
        if 'priority' in request.data:
            try:
                plan.priority=int(request.data['priority'])
                if plan.priority < 0: raise ValueError
            except (ValueError,TypeError): return Response({'detail':'Priority must be a non-negative integer.'},status=400)
        try:
            for key in ('start_date','end_date'):
                if key in request.data: setattr(plan,key,date.fromisoformat(request.data[key]))
        except (ValueError,TypeError): return Response({'detail':'Invalid date.'},status=400)
        if plan.start_date > plan.end_date or not plan.title.strip(): return Response({'detail':'A title and valid date range are required.'},status=400)
        if plan.event_type not in TemporarySchedulePlan.EventType.values: return Response({'detail':'Invalid event type.'},status=400)
        if 'sections' in request.data:
            chosen=request.data['sections']
            sections=_sections_in_version(chosen,plan.base_timetable_version)
            if sections is None:
                return Response({'detail':'Select sections from the published timetable semester.'},status=400)
        plan.save()
        if 'sections' in request.data: plan.sections.set(sections)
        return Response(_plan_data(plan))
    def delete(self, request, plan_id):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        plan=self.get_object(plan_id)
        if not plan: return Response(status=404)
        if plan.status != 'DRAFT': return Response({'detail':'Only draft plans can be deleted.'},status=409)
        plan.delete(); return Response(status=204)


class TemporaryPlanBlocks(APIView):
    permission_classes=[IsAuthenticated]
    def post(self, request, plan_id):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        plan=TemporarySchedulePlan.objects.filter(pk=plan_id,status='DRAFT').first()
        if not plan: return Response({'detail':'Draft plan not found.'},status=404)
        try:
            section=Section.objects.get(pk=request.data['section'])
            if section.semester_id != plan.base_timetable_version.timetable.semester_id:
                return Response({'detail':'Block section belongs to another timetable semester.'},status=400)
            slot=TimeSlot.objects.get(pk=request.data['start_slot'])
            room=Room.objects.get(pk=request.data['room']) if request.data.get('room') else None
            faculty_ids=request.data.get('faculty', [])
            if not isinstance(faculty_ids,list) or Faculty.objects.filter(pk__in=faculty_ids).count()!=len(set(faculty_ids)):
                return Response({'detail':'Select valid faculty records.'},status=400)
            if request.data.get('specific_date') and request.data.get('weekday') is not None:
                return Response({'detail':'Choose a date or a weekday, not both.'},status=400)
            if request.data.get('event_type',plan.event_type) not in TemporarySchedulePlan.EventType.values:
                return Response({'detail':'Invalid event type.'},status=400)
            if request.data.get('delivery_mode','OFFLINE') not in ('ONLINE','OFFLINE'):
                return Response({'detail':'Invalid delivery mode.'},status=400)
            if int(request.data.get('block_length',1)) < 1:
                return Response({'detail':'Block length must be positive.'},status=400)
            with transaction.atomic():
                block=TemporaryScheduleBlock.objects.create(plan=plan,section=section,
                specific_date=request.data.get('specific_date') or None, weekday=request.data.get('weekday'),
                start_slot=slot, block_length=request.data.get('block_length',1),
                event_type=request.data.get('event_type',plan.event_type), title=request.data.get('title',plan.title),
                room=room, trainer_name=request.data.get('trainer_name',''),
                delivery_mode=request.data.get('delivery_mode','OFFLINE'), override_regular_class=bool(request.data.get('override_regular_class')),
                notes=request.data.get('notes',''))
                block.faculty.set(Faculty.objects.filter(pk__in=faculty_ids))
                plan.sections.add(block.section)
        except (KeyError, Section.DoesNotExist, TimeSlot.DoesNotExist, Room.DoesNotExist, ValidationError, ValueError, TypeError) as error:
            return Response({'detail':f'Invalid block data: {error}'},status=400)
        return Response(_block_data(block),status=201)
    def get(self, request, plan_id):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        plan=TemporarySchedulePlan.objects.filter(pk=plan_id).first()
        return Response([_block_data(block) for block in plan.blocks.prefetch_related('faculty').all()] if plan else [],status=200 if plan else 404)


class TemporaryPlanBlockDetail(APIView):
    permission_classes=[IsAuthenticated]
    def delete(self, request, plan_id, block_id):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        block=TemporaryScheduleBlock.objects.filter(pk=block_id,plan_id=plan_id,plan__status='DRAFT').first()
        if not block: return Response({'detail':'Draft block not found.'},status=404)
        block.delete();return Response(status=204)


class TemporaryPlanOptions(APIView):
    permission_classes=[IsAuthenticated]
    def get(self, request):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        versions=TimetableVersion.objects.filter(status='PUBLISHED').select_related('timetable').order_by('-published_at','-version_no')
        return Response({'can_publish': request.user.is_superuser or request.user.role in PUBLISHERS,
            'versions':[{'id':str(v.pk),'title':v.timetable.title,'version_no':v.version_no,'status':v.status} for v in versions]})


class TemporarySectionOptions(APIView):
    """Unpaginated section master options for the temporary schedule selector."""
    permission_classes = [IsAuthenticated]
    def get(self, request):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        sections=Section.objects.select_related('program','semester__session').order_by('program__name','year','name','pk')
        version_id=request.query_params.get('version')
        if version_id:
            version=TimetableVersion.objects.filter(pk=version_id,status='PUBLISHED').select_related('timetable__semester').first()
            if not version:
                return Response({'version':'Select a valid published timetable version.'},status=400)
            sections=sections.filter(semester_id=version.timetable.semester_id)
        if request.query_params.get('program'):
            sections=sections.filter(program_id=request.query_params['program'])
        if request.query_params.get('year'):
            try:
                sections=sections.filter(year=int(request.query_params['year']))
            except (TypeError,ValueError):
                return Response({'year':'Enter a valid year.'},status=400)
        return Response([{'id':str(section.pk),'name':section.name,'year':section.year,
            'program_id':str(section.program_id),'program_name':section.program.name,
            'semester_id':str(section.semester_id),'academic_session_id':str(section.semester.session_id)}
            for section in sections])


class TemporarySlotOptions(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        version=TimetableVersion.objects.filter(pk=request.query_params.get('version'),status='PUBLISHED').first()
        if not version:
            return Response({'version':'Select a valid published timetable version.'},status=400)
        template_ids=version.entries.values_list('start_slot__template_id',flat=True).distinct()
        if len(template_ids)!=1:
            return Response({'detail':'The published version must use exactly one canonical time-slot template.'},status=400)
        template_id=template_ids[0]
        slots=TimeSlot.objects.filter(template_id=template_id).order_by('order','pk')
        return Response([{'id':str(slot.pk),'label':slot.label,'start_time':slot.start_time.isoformat(),
            'end_time':slot.end_time.isoformat(),'order':slot.order,'is_break':slot.is_break,
            'template_id':str(slot.template_id)} for slot in slots])


class TemporaryRoomOptions(APIView):
    """Compact, unpaginated active-room options for temporary schedule plans."""
    permission_classes = [IsAuthenticated]
    def get(self, request):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        rooms=Room.objects.filter(active=True).order_by('code','pk')
        return Response([{'id':str(room.pk),'code':room.code,'building':room.building,'floor':room.floor,
            'capacity':room.capacity,'room_type':room.room_type,'room_type_label':room.get_room_type_display(),
            'has_projector':room.has_projector} for room in rooms])


class TemporaryFacultyOptions(APIView):
    """Compact, unpaginated active-faculty options for temporary schedule plans."""
    permission_classes = [IsAuthenticated]
    def get(self, request):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        faculty_rows=Faculty.objects.filter(active=True).select_related('user','department').order_by('name','employee_code','pk')
        return Response([{'id':str(person.pk),'name':person.name or (f'{person.user.first_name} {person.user.last_name}'.strip() if person.user_id else '') or person.initials or person.employee_code or 'Faculty',
            'employee_code':person.employee_code,'initials':person.initials,'department_name':person.department.name}
            for person in faculty_rows])


class TemporaryPlanReview(APIView):
    permission_classes=[IsAuthenticated]
    def post(self, request, plan_id, action):
        if not _can_manage(request): return Response({'detail':'Permission denied.'},status=403)
        if action in ('publish','cancel') and not (request.user.is_superuser or request.user.role in PUBLISHERS):
            return Response({'detail':'Publishing and cancellation require academic administrator permission.'},status=403)
        plan=TemporarySchedulePlan.objects.filter(pk=plan_id).first()
        if not plan: return Response({'detail':'Plan not found.'},status=404)
        if action in ('validate','preview'):
            return Response(plan_impact(plan))
        if action == 'publish':
            if plan.status != 'DRAFT': return Response({'detail':'Only draft plans can be published.'},status=409)
            with transaction.atomic():
                plan=TemporarySchedulePlan.objects.select_for_update().get(pk=plan.pk)
                impact=plan_impact(plan)
                if not impact['valid']: return Response(impact,status=400)
                plan.status=TemporarySchedulePlan.Status.PUBLISHED;plan.published_at=timezone.now();plan.published_impact=impact
                plan.save(update_fields=['status','published_at','published_impact','updated_at'])
                record('TEMPORARY_PLAN_PUBLISHED',request.user,timetable=plan.base_timetable_version.timetable,
                       version=plan.base_timetable_version,entity_type='TemporarySchedulePlan',entity_id=plan.pk,
                       metadata={'plan_id':str(plan.pk),'start_date':plan.start_date.isoformat(),'end_date':plan.end_date.isoformat()})
            return Response(_plan_data(plan))
        if action == 'cancel':
            if plan.status != 'PUBLISHED': return Response({'detail':'Only published plans can be cancelled.'},status=409)
            plan.status=TemporarySchedulePlan.Status.CANCELLED;plan.save(update_fields=['status','updated_at'])
            record('TEMPORARY_PLAN_CANCELLED',request.user,timetable=plan.base_timetable_version.timetable,version=plan.base_timetable_version,
                   entity_type='TemporarySchedulePlan',entity_id=plan.pk,metadata={'plan_id':str(plan.pk)})
            return Response(_plan_data(plan))
        return Response({'detail':'Unknown action.'},status=404)
