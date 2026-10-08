from collections import defaultdict
from scheduling.models import ScheduleEntry
from common.models import TimeSlot
from faculty.models import FacultyAvailability
from rooms.models import Room, RoomAvailability
from academics.models import CourseOffering, Section
from scheduling.weekdays import WORKING_DAYS, WEEKEND_ERROR_CODE, WEEKDAY_NAMES


def _delivery_room_error(delivery_mode, expected_delivery, room):
    """Enforce room presence from the entry's actual delivery mode.

    Delivery-policy mismatches are reported separately; an ONLINE entry must
    never acquire a physical-room requirement just because another entry was
    validated as OFFLINE.
    """
    if delivery_mode == 'ONLINE':
        if room:
            return 'ONLINE_ROOM_NOT_ALLOWED', 'Online classes must not reserve a physical room.'
        return None
    if delivery_mode == 'OFFLINE' and expected_delivery == 'OFFLINE' and not room:
        return 'ROOM_REQUIRED', 'A physical room is required for this offline class.'
    return None


def validate_entry(version, data, exclude=None):
    conflicts=[]; weekday=int(data['weekday'])
    if weekday not in WORKING_DAYS:
        return [{'type':WEEKEND_ERROR_CODE,'code':WEEKEND_ERROR_CODE,'severity':'ERROR','weekday':weekday,'message':'Classes can only be scheduled Monday through Friday.'}]
    start=TimeSlot.objects.get(pk=data['start_slot']); slots=list(TimeSlot.objects.filter(template=start.template,order__gte=start.order).order_by('order')[:int(data.get('block_length',1))]); ids={x.pk for x in slots}
    if len(slots)<int(data.get('block_length',1)): conflicts.append({'type':'INVALID_BLOCK','severity':'ERROR','message':'Block exceeds the configured time slots.','slots':[str(x.pk) for x in slots]})
    if any(x.is_break for x in slots): conflicts.append({'type':'BREAK_OVERLAP','severity':'ERROR','message':'Block crosses a break.','slots':[str(x.pk) for x in slots]})
    room_id=data.get('room'); room=Room.objects.filter(pk=room_id).first() if room_id else None
    offering=CourseOffering.objects.select_related('course').filter(pk=data.get('course_offering')).first()
    section=Section.objects.select_related('program','semester__session').prefetch_related('period_delivery_policies').filter(pk=data.get('section')).first()
    from academics.weekly_off import get_section_weekly_off_policy
    weekly_off_policy=get_section_weekly_off_policy(section,version.timetable.academic_session,version.timetable.semester)
    if weekly_off_policy and weekly_off_policy.policy_type=='WEEKLY_OFF' and weekday==weekly_off_policy.weekday:
        day=WEEKDAY_NAMES[weekday]
        return [{'type':'SECTION_WEEKLY_OFF_DAY_VIOLATION','code':'SECTION_WEEKLY_OFF_DAY_VIOLATION','severity':'ERROR','section_id':str(section.pk),'section':section.name,'weekday':weekday,'day':day,'course':offering.course.code if offering else None,'time_slot':slots[0].label if slots else None,'message':f'{section.name} has {day} configured as its weekly OFF day.'}]
    from academics.delivery import get_section_delivery_policy, expected_delivery_mode
    section_policy,offline_weekday=get_section_delivery_policy(section,version.timetable.semester)
    if section and section_policy=='HYBRID' and offline_weekday not in WORKING_DAYS:
        return [{'type':'INVALID_HYBRID_OFFLINE_DAY','code':'INVALID_HYBRID_OFFLINE_DAY','severity':'ERROR','section_id':str(section.pk),'offline_weekday':offline_weekday,'message':'Hybrid offline day must be a working day (Monday-Friday).'}]
    expected_delivery=expected_delivery_mode(section,weekday,version.timetable.semester)
    if section and section_policy=='HYBRID' and weekday!=offline_weekday and room:
        conflicts.append({'type':'OFFLINE_DAY_MISMATCH','code':'OFFLINE_DAY_MISMATCH','severity':'ERROR','section_id':str(section.pk),'weekday':weekday,'offline_weekday':offline_weekday,'message':f'{section.name} physical classes are configured for {WEEKDAY_NAMES[offline_weekday]}.'})
    room_presence_error = _delivery_room_error(expected_delivery, expected_delivery, room)
    if room_presence_error:
        code, message = room_presence_error
        conflict = {'type':code,'code':code,'severity':'ERROR','message':message}
        if code == 'ONLINE_ROOM_NOT_ALLOWED' and room:
            conflict['room_id'] = str(room.pk)
        conflicts.append(conflict)
    from rooms.services.eligibility import room_eligibility_error
    if expected_delivery=='OFFLINE' and section and room:
        room_error=room_eligibility_error(room,offering,section,activity_type=data.get('entry_type') or (offering.default_class_type if offering else None),weekday=weekday,slot_ids=[str(x.pk) for x in slots],blocked_slots={(str(a.room_id),a.weekday,str(a.time_slot_id)) for a in RoomAvailability.objects.filter(room_id=room.pk,status__in=['BLOCKED','MAINTENANCE'])} if room else None)
        if room_error:conflicts.append({'type':room_error['code'],'severity':'ERROR','room_id':str(room.pk) if room else None,'section_id':str(section.pk),**room_error})
    for faculty_id in data.get('faculty_ids',[]):
        for slot in slots:
            av=FacultyAvailability.objects.filter(faculty_id=faculty_id,weekday=weekday,time_slot=slot).first()
            if av and not av.is_available: conflicts.append({'type':'FACULTY_UNAVAILABLE','severity':'ERROR','message':'Faculty is unavailable.','faculty_id':str(faculty_id),'weekday':weekday,'time_slot':str(slot.pk)})
    if room:
        for slot in slots:
            av=RoomAvailability.objects.filter(room=room,weekday=weekday,time_slot=slot).first()
            if av and av.status in ('BLOCKED','MAINTENANCE'): conflicts.append({'type':f'ROOM_{av.status}','severity':'ERROR','message':f'Room is {av.status.lower()}.','room_id':str(room.pk),'time_slot':str(slot.pk),'reason':av.reason})
    existing=ScheduleEntry.objects.filter(version=version,weekday=weekday).exclude(pk=exclude) if exclude else ScheduleEntry.objects.filter(version=version,weekday=weekday)
    for entry in existing.select_related('start_slot','room','section'):
        occupied=set(TimeSlot.objects.filter(template=entry.start_slot.template,order__gte=entry.start_slot.order).order_by('order').values_list('pk',flat=True)[:entry.block_length])
        if ids & occupied:
            if str(entry.section_id)==str(data.get('section')): conflicts.append({'type':'SECTION_CLASH','severity':'ERROR','message':'Section is already scheduled in this period.','conflicting_entry_id':str(entry.pk)})
            if data.get('room') and str(entry.room_id)==str(data.get('room')): conflicts.append({'type':'ROOM_CLASH','severity':'ERROR','message':'Room is already occupied.','conflicting_entry_id':str(entry.pk)})
            assigned={str(x) for x in entry.faculty_assignments.values_list('faculty_id',flat=True)}
            overlap=assigned & {str(x) for x in data.get('faculty_ids',[])}
            for faculty_id in overlap: conflicts.append({'type':'FACULTY_CLASH','severity':'ERROR','message':'Faculty is already assigned in this period.','faculty_id':faculty_id,'conflicting_entry_id':str(entry.pk),'weekday':weekday,'slots':[str(x) for x in ids & occupied]})
    return conflicts
def validate_version(version):
    entries=list(version.entries.select_related('section','section__program','section__semester__session','course_offering','course_offering__course','room','room__reserved_program','start_slot','start_slot__template').prefetch_related('faculty_assignments','section__period_delivery_policies','section__weekly_off_policies'))
    entry_faculty={entry.pk:[str(x.faculty_id) for x in entry.faculty_assignments.all()] for entry in entries}
    slots_by_template={}
    conflicts=[]
    invalid_hybrid_sections=set()
    official_hybrid_section_ids=set()
    physical_offline_section_ids=set()
    for entry in entries:
        from academics.weekly_off import get_section_weekly_off_policy
        weekly_off_policy=get_section_weekly_off_policy(entry.section,version.timetable.academic_session,version.timetable.semester)
        if weekly_off_policy and weekly_off_policy.policy_type=='WEEKLY_OFF' and entry.weekday==weekly_off_policy.weekday:
            day=WEEKDAY_NAMES[entry.weekday]
            conflicts.append({'type':'SECTION_WEEKLY_OFF_DAY_VIOLATION','code':'SECTION_WEEKLY_OFF_DAY_VIOLATION','severity':'ERROR','entry_id':str(entry.pk),'section_id':str(entry.section_id),'section':entry.section.name,'weekday':entry.weekday,'day':day,'course':entry.course_offering.course.code,'time_slot':entry.start_slot.label,'room':entry.room.code if entry.room_id else None,'locked':entry.locked,'version_id':str(version.pk),'message':f'{entry.section.name} has {day} configured as its official weekly OFF day, but {entry.course_offering.course.code} is scheduled {entry.start_slot.label}.'})
        if entry.weekday not in WORKING_DAYS:
            conflicts.append({'type':WEEKEND_ERROR_CODE,'code':WEEKEND_ERROR_CODE,'severity':'ERROR','entry_id':str(entry.pk),'weekday':entry.weekday,'message':'Classes can only be scheduled Monday through Friday.'})
        from academics.delivery import get_section_delivery_policy
        section_policy,offline_weekday=get_section_delivery_policy(entry.section,version.timetable.semester)
        from academics.delivery import get_section_delivery_policy_record
        period_policy=get_section_delivery_policy_record(entry.section,version.timetable.semester)
        if period_policy and period_policy.source=='OFFICIAL_TIMETABLE' and section_policy=='HYBRID':
            official_hybrid_section_ids.add(str(entry.section_id))
            if entry.delivery_mode=='OFFLINE' and entry.room_id and entry.weekday==offline_weekday:
                physical_offline_section_ids.add(str(entry.section_id))
        if section_policy=='HYBRID' and offline_weekday not in WORKING_DAYS and entry.section_id not in invalid_hybrid_sections:
            invalid_hybrid_sections.add(entry.section_id)
            day_name=WEEKDAY_NAMES[offline_weekday] if isinstance(offline_weekday,int) and 0<=offline_weekday<len(WEEKDAY_NAMES) else str(offline_weekday)
            conflicts.append({'type':'INVALID_HYBRID_OFFLINE_DAY','code':'INVALID_HYBRID_OFFLINE_DAY','severity':'ERROR','section_id':str(entry.section_id),'offline_weekday':offline_weekday,'day':day_name,'message':'Hybrid offline day must be a working day (Monday-Friday).'})
        if section_policy=='HYBRID' and entry.delivery_mode=='OFFLINE' and entry.weekday!=offline_weekday:
            day_name=WEEKDAY_NAMES[offline_weekday] if isinstance(offline_weekday,int) and 0<=offline_weekday<len(WEEKDAY_NAMES) else str(offline_weekday)
            conflicts.append({'type':'OFFLINE_DAY_MISMATCH','code':'OFFLINE_DAY_MISMATCH','severity':'ERROR','section_id':str(entry.section_id),'weekday':entry.weekday,'offline_weekday':offline_weekday,'message':f'{entry.section.name} physical classes are configured for {day_name}.'})
        from academics.delivery import expected_delivery_mode
        expected_delivery=expected_delivery_mode(entry.section,entry.weekday,version.timetable.semester)
        if entry.delivery_mode!=expected_delivery: conflicts.append({'type':'DELIVERY_MODE_MISMATCH','severity':'ERROR','message':'Entry delivery mode does not match the section delivery policy and weekday.','entry_id':str(entry.pk),'expected_delivery_mode':expected_delivery,'actual_delivery_mode':entry.delivery_mode})
        room_presence_error = _delivery_room_error(entry.delivery_mode, expected_delivery, entry.room_id)
        if room_presence_error:
            code, message = room_presence_error
            conflicts.append({'type':code,'code':code,'severity':'ERROR','message':message,'entry_id':str(entry.pk),'room_id':str(entry.room_id) if entry.room_id else None})
        template_id=entry.start_slot.template_id
        if template_id not in slots_by_template:
            slots_by_template[template_id]=list(TimeSlot.objects.filter(template_id=template_id).order_by('order'))
    windows={}
    for entry in entries:
        from academics.delivery import expected_delivery_mode
        expected_delivery = expected_delivery_mode(entry.section, entry.weekday, version.timetable.semester)
        ordered=slots_by_template[entry.start_slot.template_id]; position=next((i for i,x in enumerate(ordered) if x.pk==entry.start_slot_id),-1)
        window=ordered[position:position+entry.block_length] if position>=0 else []
        windows[entry.pk]=window
        if len(window)<entry.block_length: conflicts.append({'type':'INVALID_BLOCK','severity':'ERROR','message':'Block exceeds the configured time slots.','slots':[str(x.pk) for x in window]})
        if any(x.is_break for x in window): conflicts.append({'type':'BREAK_OVERLAP','severity':'ERROR','message':'Block crosses a break.','slots':[str(x.pk) for x in window]})
    faculty_ids={faculty_id for values in entry_faculty.values() for faculty_id in values}
    availability={(str(x.faculty_id),x.weekday,str(x.time_slot_id)):x for x in FacultyAvailability.objects.filter(faculty_id__in=faculty_ids)}
    room_ids={str(x.room_id) for x in entries if x.room_id}
    from rooms.models import RoomEligibilityException
    room_exceptions=list(RoomEligibilityException.objects.filter(room_id__in=room_ids,active=True).select_related('course','program'))
    room_availability={(str(x.room_id),x.weekday,str(x.time_slot_id)):x for x in RoomAvailability.objects.filter(room_id__in=room_ids)}
    section_index=defaultdict(list); room_index=defaultdict(list); faculty_index=defaultdict(list)
    for entry in entries:
        for slot in windows[entry.pk]:
            key=(entry.weekday,str(slot.pk)); section_index[(str(entry.section_id),)+key].append(entry); room_index[(str(entry.room_id),)+key].append(entry) if entry.room_id else None
            for faculty_id in entry_faculty[entry.pk]: faculty_index[(faculty_id,)+key].append(entry)
            for faculty_id in entry_faculty[entry.pk]:
                blocked=availability.get((faculty_id,entry.weekday,str(slot.pk)))
                if blocked and not blocked.is_available: conflicts.append({'type':'FACULTY_UNAVAILABLE','severity':'ERROR','message':'Faculty is unavailable.','faculty_id':faculty_id,'weekday':entry.weekday,'time_slot':str(slot.pk)})
            room_block=room_availability.get((str(entry.room_id),entry.weekday,str(slot.pk))) if entry.room_id else None
            if room_block and room_block.status in ('BLOCKED','MAINTENANCE'): conflicts.append({'type':f'ROOM_{room_block.status}','severity':'ERROR','message':f'Room is {room_block.status.lower()}.','room_id':str(entry.room_id),'time_slot':str(slot.pk),'reason':room_block.reason})
        from rooms.services.eligibility import required_course_room_code
        required_room_code=required_course_room_code(entry.course_offering) if entry.entry_type=='PRACTICAL' else None
        if required_room_code and entry.delivery_mode=='OFFLINE' and expected_delivery=='OFFLINE' and (not entry.room or entry.room.code != required_room_code): conflicts.append({'type':'REQUIRED_ROOM_MISMATCH','severity':'ERROR','message':'Quantum Physics and Advanced Functional Materials Lab must use Room 105.','required_room_code':required_room_code,'entry_id':str(entry.pk)})
        if required_room_code and entry.delivery_mode=='OFFLINE' and expected_delivery=='OFFLINE' and entry.room and entry.room.room_type != 'QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB': conflicts.append({'type':'REQUIRED_ROOM_TYPE_MISMATCH','severity':'ERROR','message':'Room 105 must have room type Quantum Physics and Advanced Functional Materials Lab.','entry_id':str(entry.pk)})
        if entry.delivery_mode=='OFFLINE' and expected_delivery=='OFFLINE' and entry.section and entry.room:
            from rooms.services.eligibility import room_eligibility_error
            room_error=room_eligibility_error(entry.room,entry.course_offering,entry.section,activity_type=entry.entry_type,weekday=entry.weekday,slot_ids=[str(slot.pk) for slot in windows[entry.pk]],exceptions=room_exceptions,blocked_slots={(str(entry.room_id),entry.weekday,str(slot.pk)) for slot in windows[entry.pk] if (room_availability.get((str(entry.room_id),entry.weekday,str(slot.pk))) and room_availability[(str(entry.room_id),entry.weekday,str(slot.pk))].status in ('BLOCKED','MAINTENANCE'))} if entry.room_id else None)
            if room_error:conflicts.append({'type':room_error['code'],'severity':'ERROR','entry_id':str(entry.pk),'room_id':str(entry.room_id) if entry.room_id else None,'section_id':str(entry.section_id),**room_error})
    for section_id in sorted(official_hybrid_section_ids-physical_offline_section_ids):
        section=next((entry.section for entry in entries if str(entry.section_id)==section_id),None)
        if section:
            conflicts.append({'type':'OFFLINE_DAY_MISSING','code':'OFFLINE_DAY_MISSING','severity':'ERROR','section_id':section_id,'section':section.name,'message':f'{section.name} must have at least one physical class on its configured offline weekday.'})
    for index,conflict_type,message in ((section_index,'SECTION_CLASH','Section is already scheduled in this period.'),(room_index,'ROOM_CLASH','Room is already occupied.')):
        for grouped in index.values():
            for entry in grouped:
                for other in grouped:
                    if other.pk!=entry.pk and (conflict_type!='ROOM_CLASH' or entry.room_id): conflicts.append({'type':conflict_type,'severity':'ERROR','message':message,'conflicting_entry_id':str(other.pk)})
    for (faculty_id,weekday,slot_id),grouped in faculty_index.items():
        for entry in grouped:
            for other in grouped:
                if other.pk!=entry.pk: conflicts.append({'type':'FACULTY_CLASH','severity':'ERROR','message':'Faculty is already assigned in this period.','faculty_id':faculty_id,'conflicting_entry_id':str(other.pk),'weekday':weekday,'slots':[slot_id]})
    scheduled=defaultdict(int)
    for entry in entries: scheduled[entry.course_offering_id]+=entry.block_length
    completion=[]
    for offering in version.timetable.semester.course_offerings.filter(active=True).select_related('course'):
        count=scheduled[offering.pk]; completion.append({'course_offering_id':str(offering.pk),'required':offering.weekly_periods,'scheduled':count,'remaining':max(0,offering.weekly_periods-count),'complete':count>=offering.weekly_periods})
    return {'valid':not conflicts,'error_count':len(conflicts),'warning_count':0,'conflicts':conflicts,'course_completion':completion}


def validate_generated_entries(version, generated_entries, requirements, mode='REBUILD_UNLOCKED', scope=None):
    """Validate a solver payload before it can be advertised or applied."""
    from collections import defaultdict
    from academics.models import CourseOffering
    from rooms.models import RoomEligibilityException
    from rooms.services.eligibility import room_eligibility_error
    from scheduling.solver.engine import fixed_occupancy

    scope_ids=set(map(str, scope or []))
    fixed_section,fixed_faculty,fixed_room=fixed_occupancy(version,mode,scope)
    seen={'SECTION':defaultdict(set),'FACULTY':defaultdict(set),'ROOM':defaultdict(set)}
    for kind, resources in (('SECTION',fixed_section),('FACULTY',fixed_faculty),('ROOM',fixed_room)):
        for key, ids in resources.items():
            if ids: seen[kind][key].update(ids)
    requirement_map={item.requirement_id:item for item in requirements}
    offering_ids={item.course_offering_id for item in requirements}
    offerings={str(item.pk):item for item in CourseOffering.objects.filter(pk__in=offering_ids).select_related('course','section','section__program')}
    section_ids={item.section_id for item in requirements}
    sections={str(item.pk):item for item in Section.objects.filter(pk__in=section_ids).select_related('program','semester__session').prefetch_related('period_delivery_policies','weekly_off_policies')}
    from academics.delivery import get_section_delivery_policy, get_section_delivery_policy_record
    official_hybrid_sections={sid for sid,section in sections.items() if (policy:=get_section_delivery_policy_record(section,version.timetable.semester)) and policy.active and policy.source=='OFFICIAL_TIMETABLE' and policy.mode=='HYBRID'}
    preserved=version.entries.select_related('section','course_offering__course','start_slot','room').prefetch_related('section__weekly_off_policies','faculty_assignments__faculty')
    scope_ids=set(map(str,scope or []))
    if mode=='REBUILD_UNLOCKED':
        from django.db.models import Q
        preserved=preserved.filter(Q(locked=True)|~Q(section_id__in=scope_ids)) if scope_ids else preserved.filter(locked=True)
    conflicts=[]
    from academics.weekly_off import get_section_weekly_off_policy
    for fixed in preserved:
        fixed_policy=get_section_weekly_off_policy(fixed.section,version.timetable.academic_session,version.timetable.semester)
        if fixed_policy and fixed_policy.policy_type=='WEEKLY_OFF' and fixed.weekday==fixed_policy.weekday:
            day=WEEKDAY_NAMES[fixed.weekday]
            conflicts.append({'code':'SECTION_WEEKLY_OFF_DAY_VIOLATION','type':'SECTION_WEEKLY_OFF_DAY_VIOLATION','severity':'ERROR','section_id':str(fixed.section_id),'section':fixed.section.name,'weekday':fixed.weekday,'day':day,'course':fixed.course_offering.course.code,'time_slot':fixed.start_slot.label,'room':fixed.room.code if fixed.room_id else None,'faculty':[assignment.faculty.name or assignment.faculty.initials or assignment.faculty.employee_code for assignment in fixed.faculty_assignments.all()],'entry_id':str(fixed.pk),'version_id':str(version.pk),'locked':fixed.locked,'message':f'{fixed.section.name} has {day} configured as its official weekly OFF day.'})
    preserved_offline_sections=set()
    for fixed in preserved.filter(section_id__in=official_hybrid_sections,delivery_mode='OFFLINE',room_id__isnull=False).select_related('section__semester__session'):
        policy=get_section_delivery_policy_record(fixed.section,version.timetable.semester)
        if policy and fixed.weekday==policy.offline_weekday:
            preserved_offline_sections.add(str(fixed.section_id))
    room_ids={str(item.get('room_id')) for item in generated_entries if item.get('room_id')}
    rooms={str(item.pk):item for item in Room.objects.filter(pk__in=room_ids).select_related('reserved_program')}
    exceptions=list(RoomEligibilityException.objects.filter(room_id__in=room_ids,active=True).select_related('program','course'))
    slot_rows=list(TimeSlot.objects.filter(template__institution=version.timetable.institution).order_by('template_id','order'))
    by_template=defaultdict(list)
    slot_lookup={str(slot.pk):slot for slot in slot_rows}
    for slot in slot_rows:by_template[slot.template_id].append(slot)
    blocked={(str(row.room_id),row.weekday,str(row.time_slot_id)) for row in RoomAvailability.objects.filter(room_id__in=room_ids,status__in=('BLOCKED','MAINTENANCE'))}
    faculty_ids={str(faculty.get('faculty_id')) for item in generated_entries for faculty in item.get('faculty',[])}
    unavailable={(str(row.faculty_id),row.weekday,str(row.time_slot_id)) for row in FacultyAvailability.objects.filter(faculty_id__in=faculty_ids,is_available=False)}
    scheduled=defaultdict(int);requirement_counts=defaultdict(int)

    def add_error(code,message,item,offering=None,section=None,room=None,weekday=None,slot=None):
        conflicts.append({'code':code,'type':code,'severity':'ERROR','message':message,
            'course_code':offering.course.code if offering else None,
            'section':section.name if section else None,'section_id':str(section.pk) if section else item.get('section_id'),
            'program':section.program.code if section else None,'year':section.year if section else None,
            'weekday':weekday,'day':WEEKDAY_NAMES[weekday] if isinstance(weekday,int) and 0<=weekday<len(WEEKDAY_NAMES) else None,
            'time_slot':slot.label if slot else None,'room':room.code if room else None,
            'entry_id':item.get('requirement_id')})

    for item in generated_entries:
        requirement=requirement_map.get(item.get('requirement_id'))
        offering=offerings.get(str(item.get('course_offering_id')))
        section=sections.get(str(item.get('section_id')))
        room=rooms.get(str(item.get('room_id'))) if item.get('room_id') else None
        weekday=item.get('weekday');length=int(item.get('block_length') or 0)
        if not requirement or not offering or not section:
            add_error('GENERATED_REFERENCE_MISSING','Generated entry references a missing requirement, offering, or section.',item,offering,section,room,weekday)
            continue
        if weekday not in WORKING_DAYS:
            add_error(WEEKEND_ERROR_CODE,'Classes can only be scheduled Monday through Friday.',item,offering,section,room,weekday)
            continue
        from academics.weekly_off import get_section_weekly_off_policy
        weekly_off_policy=get_section_weekly_off_policy(section,version.timetable.academic_session,version.timetable.semester)
        if weekly_off_policy and weekly_off_policy.policy_type=='WEEKLY_OFF' and weekday==weekly_off_policy.weekday:
            day=WEEKDAY_NAMES[weekday]
            add_error('SECTION_WEEKLY_OFF_DAY_VIOLATION',f'{section.name} has {day} configured as its weekly OFF day.',item,offering,section,room,weekday)
        scheduled[offering.pk]+=length
        requirement_counts[requirement.requirement_id]+=1
        ordered=by_template.get(slot_lookup.get(str(item.get('start_slot_id'))).template_id,[]) if slot_lookup.get(str(item.get('start_slot_id'))) else []
        pos=next((index for index,slot in enumerate(ordered) if str(slot.pk)==str(item.get('start_slot_id'))),-1)
        window=ordered[pos:pos+length] if pos>=0 else []
        if length!=requirement.block_length or len(window)!=length or any(slot.is_break for slot in window) or any(window[i].order+1!=window[i+1].order for i in range(max(0,len(window)-1))):
            add_error('PRACTICAL_BLOCK_INVALID','Generated block length or contiguous slot window does not match its requirement.',item,offering,section,room,weekday,window[0] if window else None)
            continue
        supplied=[str(value) for value in item.get('occupied_slot_ids',[])]
        if supplied and supplied!=[str(slot.pk) for slot in window]:
            add_error('OCCUPIED_SLOT_MISMATCH','Generated occupied slots do not match the contiguous block.',item,offering,section,room,weekday,window[0])
        from academics.delivery import expected_delivery_mode
        expected_delivery=expected_delivery_mode(section,weekday,version.timetable.semester)
        delivery_mode=item.get('delivery_mode')
        policy_mode,offline_weekday=get_section_delivery_policy(section,version.timetable.semester)
        if policy_mode=='HYBRID' and item.get('delivery_mode')=='OFFLINE' and weekday!=offline_weekday:
            add_error('OFFLINE_DAY_MISMATCH',f'{section.name} physical classes are configured for {WEEKDAY_NAMES[offline_weekday]}.',item,offering,section,room,weekday,window[0] if window else None)
        if item.get('delivery_mode')!=expected_delivery:
            add_error('DELIVERY_MODE_MISMATCH','Generated delivery mode does not match the section policy.',item,offering,section,room,weekday,window[0])
        room_presence_error = _delivery_room_error(delivery_mode, expected_delivery, room)
        if room_presence_error:
            code, message = room_presence_error
            add_error(code, message, item, offering, section, room, weekday, window[0] if window else None)
        if delivery_mode=='OFFLINE' and expected_delivery=='OFFLINE' and room:
            room_error=room_eligibility_error(room,offering,section,activity_type=item.get('entry_type'),weekday=weekday,slot_ids=[str(slot.pk) for slot in window],blocked_slots=blocked,exceptions=exceptions)
            if room_error:add_error(room_error['code'],room_error['message'],item,offering,section,room,weekday,window[0])
        slot_ids=[str(slot.pk) for slot in window]
        for slot_id in slot_ids:
            slot=slot_lookup[slot_id]
            keys=[('SECTION',(str(section.pk),weekday,slot_id))]
            keys += [('FACULTY',(str(fac.get('faculty_id')),weekday,slot_id)) for fac in item.get('faculty',[])]
            if room:keys.append(('ROOM',(str(room.pk),weekday,slot_id)))
            for kind,key in keys:
                if seen[kind][key]:
                    add_error(f'{kind}_CLASH',f'{kind.title()} is assigned to overlapping classes.',item,offering,section,room,weekday,slot)
                seen[kind][key].add(str(item.get('requirement_id')))
            for fac in item.get('faculty',[]):
                if (str(fac.get('faculty_id')),weekday,slot_id) in unavailable:
                    add_error('FACULTY_UNAVAILABLE','Faculty is unavailable for this period.',item,offering,section,room,weekday,slot)

    for requirement in requirements:
        if requirement_counts[requirement.requirement_id]!=1:
            offering=offerings.get(str(requirement.course_offering_id));section=sections.get(str(requirement.section_id))
            conflicts.append({'code':'REQUIREMENT_OCCURRENCE_COUNT','type':'REQUIREMENT_OCCURRENCE_COUNT','severity':'ERROR','course_code':offering.course.code if offering else None,'section':section.name if section else None,'section_id':requirement.section_id,'message':f'Requirement {requirement.requirement_id} must occur exactly once in the generated result.'})
    generated_offline_sections={str(item.get('section_id')) for item in generated_entries if item.get('delivery_mode')=='OFFLINE' and item.get('room_id') and item.get('weekday')==get_section_delivery_policy(sections.get(str(item.get('section_id'))),version.timetable.semester)[1]}
    for section_id in sorted(official_hybrid_sections-(preserved_offline_sections|generated_offline_sections)):
        section=sections[section_id]
        conflicts.append({'code':'OFFLINE_DAY_MISSING','type':'OFFLINE_DAY_MISSING','severity':'ERROR','section_id':section_id,'section':section.name,'message':f'{section.name} must have at least one physical class on its configured offline weekday.'})
    for offering_id,offering in offerings.items():
        expected=sum(req.block_length for req in requirements if req.course_offering_id==offering_id)
        if scheduled[offering.pk]!=expected:
            conflicts.append({'code':'WEEKLY_PERIOD_MISMATCH','type':'WEEKLY_PERIOD_MISMATCH','severity':'ERROR','course_code':offering.course.code,'section':offering.section.name,'section_id':str(offering.section_id),'expected_periods':expected,'scheduled_periods':scheduled[offering.pk],'message':f'{offering.course.code} / {offering.section.name} has {scheduled[offering.pk]} generated periods; expected {expected}.'})
    return {'valid':not conflicts,'error_count':len(conflicts),'conflicts':conflicts}
