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
    conflicts=[]
    for e in version.entries.all(): conflicts.extend(validate_entry(version,{'weekday':e.weekday,'start_slot':e.start_slot_id,'block_length':e.block_length,'section':e.section_id,'room':e.room_id},e.pk))
    completion=[]
    for offering in version.timetable.semester.course_offerings.all():
        scheduled=sum(e.block_length for e in version.entries.filter(course_offering=offering)); completion.append({'course_offering_id':str(offering.pk),'required':offering.weekly_periods,'scheduled':scheduled,'remaining':max(0,offering.weekly_periods-scheduled),'complete':scheduled>=offering.weekly_periods})
    return {'valid':not conflicts,'error_count':len(conflicts),'warning_count':0,'conflicts':conflicts,'course_completion':completion}
