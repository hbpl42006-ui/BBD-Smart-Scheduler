from dataclasses import dataclass,asdict
from collections import defaultdict
@dataclass(frozen=True)
class FacultyAssignment: faculty_id:str; role:str
@dataclass(frozen=True)
class Requirement:
    requirement_id:str; course_offering_id:str; section_id:str; entry_type:str; block_length:int; faculty:tuple[FacultyAssignment,...]; room_type_requirement:str; source_placement:dict|None=None
@dataclass(frozen=True)
class Candidate:
    requirement_id:str; course_offering_id:str; section_id:str; weekday:int; start_slot_id:str; occupied_slot_ids:tuple[str,...]; block_length:int; room_id:str; faculty:tuple[FacultyAssignment,...]; entry_type:str
def resolve_slots(start,block,by_template):
    ordered=by_template.get(str(start.template_id),[]);pos=next((i for i,x in enumerate(ordered) if x.id==start.id),-1);window=ordered[pos:pos+block] if pos>=0 else []
    if len(window)!=block or any(x.is_break for x in window) or any(window[i].order+1!=window[i+1].order for i in range(len(window)-1)):return []
    return window
def build_requirements(version,config):
    from academics.models import CourseOffering
    mode=config.get('mode','FILL_GAPS');scope=config.get('section_ids');qs=CourseOffering.objects.filter(semester=version.timetable.semester,active=True).select_related('course','section');qs=qs.filter(section_id__in=scope) if scope else qs;result=[];errors=[];rules={str(x.get('course_offering_id')):x for x in config.get('offering_rules',[])}
    for offering in qs:
        entries=list(version.entries.filter(course_offering=offering).prefetch_related('faculty_assignments'));fixed=entries if mode=='FILL_GAPS' else [x for x in entries if x.locked];remaining=offering.weekly_periods-sum(x.block_length for x in fixed)
        if remaining<0:errors.append({'code':'FIXED_PERIODS_EXCEED_REQUIREMENT','course_offering_id':str(offering.id)});continue
        rule=rules.get(str(offering.id),{});assign=rule.get('faculty') or [{'faculty_id':str(x.faculty_id),'role':x.role} for x in offering.faculties.all()]
        if remaining and not assign:errors.append({'code':'NO_FACULTY_ASSIGNMENT','course_offering_id':str(offering.id)});continue
        lengths=rule.get('session_lengths') or [1]*remaining
        if sum(lengths)!=remaining:errors.append({'code':'INVALID_SESSION_PATTERN','course_offering_id':str(offering.id),'required_remaining_periods':remaining,'provided_session_lengths':lengths});continue
        for i,length in enumerate(lengths):result.append(Requirement(f'{offering.id}:{i}',str(offering.id),str(offering.section_id),rule.get('entry_type',offering.default_class_type),int(length),tuple(FacultyAssignment(str(x['faculty_id']),x.get('role','PRIMARY')) for x in assign),offering.room_type_requirement))
    return result,errors
def fixed_occupancy(version,mode,scope=None):
    fixed=version.entries.select_related('start_slot').prefetch_related('faculty_assignments');fixed=(fixed.filter(locked=True)|version.entries.exclude(section_id__in=scope)) if mode=='REBUILD_UNLOCKED' and scope else fixed;section=defaultdict(set);faculty=defaultdict(set);room=defaultdict(set)
    for e in fixed:
        ordered=list(e.start_slot.template.slots.order_by('order'));pos=next((i for i,x in enumerate(ordered) if x.id==e.start_slot_id),-1)
        for s in ordered[pos:pos+e.block_length]:
            key=(e.weekday,str(s.id));section[(str(e.section_id),)+key].add(str(e.id));room[(str(e.room_id),)+key].add(str(e.id)) if e.room_id else None
            for a in e.faculty_assignments.all():faculty[(str(a.faculty_id),)+key].add(str(e.id))
    return section,faculty,room
def fixed_conflicts(version,mode,scope=None):
    fixed=list(version.entries.select_related('start_slot').prefetch_related('faculty_assignments'))
    if mode=='REBUILD_UNLOCKED' and scope:fixed=[e for e in fixed if e.locked or str(e.section_id) not in set(scope)]
    seen={}; conflicts=[]
    for e in fixed:
        ordered=list(e.start_slot.template.slots.order_by('order'));pos=next((i for i,x in enumerate(ordered) if x.id==e.start_slot_id),-1)
        slots=ordered[pos:pos+e.block_length]
        resources=[('SECTION',str(e.section_id))]+([('ROOM',str(e.room_id))] if e.room_id else [])+[('FACULTY',str(a.faculty_id)) for a in e.faculty_assignments.all()]
        for kind,rid in resources:
            for slot in slots:
                key=(kind,rid,e.weekday,str(slot.id));prior=seen.get(key)
                if prior and prior!=str(e.id):conflicts.append({'code':f'LOCKED_{kind}_CLASH','entry_ids':[prior,str(e.id)],'resource_id':rid,'weekday':e.weekday,'slot_id':str(slot.id)})
                seen[key]=str(e.id)
    return conflicts
def build_candidates(version,requirements,config):
    from common.models import TimeSlot
    from rooms.models import Room
    from faculty.models import FacultyAvailability
    from rooms.models import RoomAvailability
    slots=list(TimeSlot.objects.select_related('template').order_by('template_id','order'));by_template=defaultdict(list)
    for s in slots:by_template[str(s.template_id)].append(s)
    section,faculty,occupied=fixed_occupancy(version,config.get('mode','FILL_GAPS'),config.get('section_ids'));rooms=list(Room.objects.filter(active=True));out=defaultdict(list);diagnostics=[]
    for req in requirements:
        section_obj=version.timetable.semester.sections.filter(pk=req.section_id).first();strength=section_obj.student_strength if section_obj else 0
        for day in range(6):
            for start in slots:
                window=resolve_slots(start,req.block_length,by_template)
                if not window:continue
                ids=tuple(str(x.id) for x in window)
                for room in rooms:
                    if room.capacity<strength or (req.room_type_requirement and room.room_type!=req.room_type_requirement):continue
                    if any((str(req.section_id),day,s) in section for s in ids) or any((str(room.id),day,s) in occupied for s in ids) or any((a.faculty_id,day,s) in faculty for a in req.faculty for s in ids):continue
                    if FacultyAvailability.objects.filter(faculty_id__in=[a.faculty_id for a in req.faculty],weekday=day,time_slot_id__in=ids,is_available=False).exists():continue
                    if RoomAvailability.objects.filter(room=room,weekday=day,time_slot_id__in=ids,status__in=['BLOCKED','MAINTENANCE']).exists():continue
                    out[req.requirement_id].append(Candidate(req.requirement_id,req.course_offering_id,req.section_id,day,str(start.id),ids,req.block_length,str(room.id),req.faculty,req.entry_type))
        if not out[req.requirement_id]:diagnostics.append({'code':'NO_VALID_PLACEMENT','requirement_id':req.requirement_id,'course_offering_id':req.course_offering_id,'section_id':req.section_id,'block_length':req.block_length})
    return out,diagnostics
def candidate_dict(c):return asdict(c)|{'faculty':[asdict(x) for x in c.faculty]}
