from collections import defaultdict
from scheduling.models import ScheduleEntry
from common.models import TimeSlot
from faculty.models import FacultyAvailability
from rooms.models import Room, RoomAvailability
from academics.models import CourseOffering, Section
def validate_entry(version, data, exclude=None):
    conflicts=[]; weekday=int(data['weekday']); start=TimeSlot.objects.get(pk=data['start_slot']); slots=list(TimeSlot.objects.filter(template=start.template,order__gte=start.order).order_by('order')[:int(data.get('block_length',1))]); ids={x.pk for x in slots}
    if len(slots)<int(data.get('block_length',1)): conflicts.append({'type':'INVALID_BLOCK','severity':'ERROR','message':'Block exceeds the configured time slots.','slots':[str(x.pk) for x in slots]})
    if any(x.is_break for x in slots): conflicts.append({'type':'BREAK_OVERLAP','severity':'ERROR','message':'Block crosses a break.','slots':[str(x.pk) for x in slots]})
    room_id=data.get('room'); room=Room.objects.filter(pk=room_id).first() if room_id else None
    offering=CourseOffering.objects.select_related('course').filter(pk=data.get('course_offering')).first()
    section=Section.objects.filter(pk=data.get('section')).first()
    if room and section and room.capacity<section.student_strength: conflicts.append({'type':'ROOM_CAPACITY_EXCEEDED','severity':'ERROR','message':'Room capacity is insufficient.','room_id':str(room.pk),'required_capacity':section.student_strength,'room_capacity':room.capacity})
    if room and offering and offering.room_type_requirement and room.room_type!=offering.room_type_requirement: conflicts.append({'type':'ROOM_TYPE_MISMATCH','severity':'ERROR','message':'Room type does not match course requirement.','room_id':str(room.pk),'required_room_type':offering.room_type_requirement,'actual_room_type':room.room_type})
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
    entries=list(version.entries.select_related('section','section__program','course_offering','course_offering__course','room','start_slot','start_slot__template').prefetch_related('faculty_assignments'))
    entry_faculty={entry.pk:[str(x.faculty_id) for x in entry.faculty_assignments.all()] for entry in entries}
    slots_by_template={}
    for entry in entries:
        template_id=entry.start_slot.template_id
        if template_id not in slots_by_template:
            slots_by_template[template_id]=list(TimeSlot.objects.filter(template_id=template_id).order_by('order'))
    windows={}; conflicts=[]
    for entry in entries:
        ordered=slots_by_template[entry.start_slot.template_id]; position=next((i for i,x in enumerate(ordered) if x.pk==entry.start_slot_id),-1)
        window=ordered[position:position+entry.block_length] if position>=0 else []
        windows[entry.pk]=window
        if len(window)<entry.block_length: conflicts.append({'type':'INVALID_BLOCK','severity':'ERROR','message':'Block exceeds the configured time slots.','slots':[str(x.pk) for x in window]})
        if any(x.is_break for x in window): conflicts.append({'type':'BREAK_OVERLAP','severity':'ERROR','message':'Block crosses a break.','slots':[str(x.pk) for x in window]})
    faculty_ids={faculty_id for values in entry_faculty.values() for faculty_id in values}
    availability={(str(x.faculty_id),x.weekday,str(x.time_slot_id)):x for x in FacultyAvailability.objects.filter(faculty_id__in=faculty_ids)}
    room_ids={str(x.room_id) for x in entries if x.room_id}
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
        if entry.room and entry.room.capacity<entry.section.student_strength: conflicts.append({'type':'ROOM_CAPACITY_EXCEEDED','severity':'ERROR','message':'Room capacity is insufficient.','room_id':str(entry.room_id),'required_capacity':entry.section.student_strength,'room_capacity':entry.room.capacity})
        if entry.room and entry.course_offering.room_type_requirement and entry.room.room_type!=entry.course_offering.room_type_requirement: conflicts.append({'type':'ROOM_TYPE_MISMATCH','severity':'ERROR','message':'Room type does not match course requirement.','room_id':str(entry.room_id),'required_room_type':entry.course_offering.room_type_requirement,'actual_room_type':entry.room.room_type})
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
