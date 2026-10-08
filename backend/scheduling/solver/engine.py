from dataclasses import dataclass,asdict
from collections import defaultdict
import time
from scheduling.weekdays import WORKING_DAYS, WEEKEND_ERROR_CODE, WEEKDAY_NAMES
def normalize_session_pattern(full_pattern,remaining_periods,required_block_size):
    remaining_periods=int(remaining_periods); block=max(1,int(required_block_size or 1))
    if remaining_periods==0:return []
    if remaining_periods<0 or remaining_periods%block:return None
    return [block]*(remaining_periods//block)

def diagnose_session_blocks(version, mode='REBUILD_UNLOCKED', scope=None):
    """Read-only master-data and preserved-entry block diagnostics."""
    from academics.models import CourseOffering
    from django.db.models import Prefetch
    entries=version.entries.all()
    offerings=CourseOffering.objects.filter(semester=version.timetable.semester,active=True).select_related('course','section').prefetch_related(Prefetch('schedule_entries',queryset=entries,to_attr='_diagnostic_entries'))
    if scope:offerings=offerings.filter(section_id__in=scope)
    rows=[]
    for offering in offerings:
        fixed=[entry for entry in offering._diagnostic_entries if mode=='FILL_GAPS' or entry.locked]
        locked=sum(entry.block_length for entry in fixed)
        remaining=offering.weekly_periods-locked
        activity=offering.default_class_type
        block=1 if activity in {'LECTURE','TUTORIAL'} else int(offering.required_block_size or 1)
        if offering.weekly_periods%block or remaining<0 or remaining%block:
            rows.append({'course_code':offering.course.code,'course_name':offering.course.name,'section':offering.section.name,'activity_type':offering.default_class_type,'weekly_periods':offering.weekly_periods,'block_size':block,'locked_periods':locked,'remaining_periods':remaining,'status':'OVER_SATISFIED' if remaining<0 else 'INVALID_MASTER_BLOCK' if offering.weekly_periods%block else 'INVALID_REMAINDER'})
    return rows
@dataclass(frozen=True)
class FacultyAssignment: faculty_id:str; role:str
@dataclass(frozen=True)
class Requirement:
    requirement_id:str; course_offering_id:str; section_id:str; entry_type:str; block_length:int; faculty:tuple[FacultyAssignment,...]; room_type_requirement:str; required_capacity:int=0; source_placement:dict|None=None
@dataclass(frozen=True)
class Candidate:
    requirement_id:str; course_offering_id:str; section_id:str; weekday:int; start_slot_id:str; occupied_slot_ids:tuple[str,...]; block_length:int; room_id:str; faculty:tuple[FacultyAssignment,...]; entry_type:str; delivery_mode:str='OFFLINE'
def resolve_slots(start,block,by_template):
    ordered=by_template.get(str(start.template_id),[]);pos=next((i for i,x in enumerate(ordered) if x.id==start.id),-1);window=ordered[pos:pos+block] if pos>=0 else []
    if len(window)!=block or any(x.is_break for x in window) or any(window[i].order+1!=window[i+1].order for i in range(len(window)-1)):return []
    return window
def build_requirements(version,config,statistics=None):
    from academics.models import CourseOffering
    from django.db.models import Prefetch
    mode=config.get('mode','FILL_GAPS');scope=config.get('section_ids');entry_qs=version.entries.all().prefetch_related('faculty_assignments');qs=CourseOffering.objects.filter(semester=version.timetable.semester,active=True).select_related('course','section').prefetch_related(Prefetch('schedule_entries',queryset=entry_qs,to_attr='_generation_entries'), 'faculties__faculty');qs=qs.filter(section_id__in=scope) if scope else qs;result=[];errors=[];rules={str(x.get('course_offering_id')):x for x in config.get('offering_rules',[])}
    for offering in qs:
        entries=list(getattr(offering,'_generation_entries',[]));fixed=entries if mode=='FILL_GAPS' else [x for x in entries if x.locked];remaining=offering.weekly_periods-sum(x.block_length for x in fixed)
        activity=next((x.get('entry_type') for x in config.get('offering_rules',[]) if str(x.get('course_offering_id'))==str(offering.id)),None) or offering.default_class_type
        rule=rules.get(str(offering.id),{})
        block=1 if activity in {'LECTURE','TUTORIAL'} else int(rule.get('block_size') or offering.required_block_size or 1)
        if remaining==0:
            if statistics is not None:statistics['fully_satisfied_offerings_skipped']=statistics.get('fully_satisfied_offerings_skipped',0)+1
            continue
        partial=next((entry for entry in fixed if entry.locked and entry.block_length!=block),None)
        if partial:
            errors.append({'code':'PARTIAL_LOCKED_BLOCK','course_offering_id':str(offering.id),'course_code':offering.course.code,'section':offering.section.name,'weekday':partial.weekday,'slot_id':str(partial.start_slot_id),'entry_id':str(partial.pk),'expected_block_size':block,'actual_block_size':partial.block_length,'message':f'Locked {offering.course.code} / {offering.section.name} entry has {partial.block_length} periods; this offering requires blocks of {block}.'})
            continue
        if remaining<0:
            errors.append({'code':'FIXED_PERIODS_EXCEED_REQUIREMENT','course_offering_id':str(offering.id),'course_code':offering.course.code,'section':offering.section.name,'weekly_periods':offering.weekly_periods,'fixed_periods':sum(x.block_length for x in fixed),'preserved_periods':sum(x.block_length for x in fixed),'locked_periods':offering.weekly_periods-remaining,'remaining_periods':remaining,'mode':mode,'source_entry_ids':[str(x.pk) for x in fixed],'message':f'{offering.course.code} / {offering.section.name} has more preserved periods than its weekly requirement.'})
            continue
        assign=rule.get('faculty') or [{'faculty_id':str(x.faculty_id),'role':x.role} for x in offering.faculties.all()]
        if remaining and not assign:
            errors.append({'code':'NO_FACULTY_ASSIGNMENT','course_offering_id':str(offering.id),'course_code':offering.course.code,'section':offering.section.name,'message':f'{offering.course.code} / {offering.section.name} has no faculty assignment.'})
            continue
        lengths=rule.get('session_lengths')
        allow_remainder=bool(rule.get('allow_remainder_period', offering.allow_remainder_period or config.get('allow_remainder_period',False))) and activity=='PRACTICAL'
        if not lengths:
            lengths=[block]*(remaining//block)+(([remaining%block]) if allow_remainder and remaining%block else []) if remaining%block==0 or allow_remainder else None
        # The wizard may initialize a rule from the master offering's full
        # weekly requirement. In REBUILD_UNLOCKED, locked source placements
        # are preserved, so normalize that full pattern to the remaining
        # periods using the offering's configured block size.
        if lengths is not None and sum(lengths)==offering.weekly_periods and sum(lengths)!=remaining:
            lengths=([block]*(remaining//block)+[remaining%block] if allow_remainder and remaining%block else normalize_session_pattern(lengths,remaining,block))
        invalid_pattern = (
            lengths is None
            or sum(lengths) != remaining
            or any(int(value) != block for value in lengths[:-1])
            or (lengths and (int(lengths[-1]) <= 0 or int(lengths[-1]) > block))
            or (lengths and int(lengths[-1]) != block and not allow_remainder)
        )
        if invalid_pattern:
            errors.append({'code':'INVALID_SESSION_PATTERN','course_offering_id':str(offering.id),'course_code':offering.course.code,'course_name':offering.course.name,'section':offering.section.name,'activity_type':rule.get('entry_type',offering.default_class_type),'weekly_periods':offering.weekly_periods,'locked_periods':offering.weekly_periods-remaining,'remaining_periods':remaining,'required_block_size':block,'pattern':lengths,'provided_session_lengths':lengths,'message':f'{remaining} remaining periods cannot be represented using blocks of {block}.'})
            continue
        from rooms.services.eligibility import normalize_room_type_requirement
        normalized_room_type=normalize_room_type_requirement(offering.room_type_requirement)
        for i,length in enumerate(lengths):result.append(Requirement(f'{offering.id}:{i}',str(offering.id),str(offering.section_id),rule.get('entry_type',offering.default_class_type),int(length),tuple(FacultyAssignment(str(x['faculty_id']),x.get('role','PRIMARY')) for x in assign),normalized_room_type,offering.section.student_strength))
    return result,errors
def fixed_occupancy(version,mode,scope=None):
    from django.db.models import Prefetch
    from common.models import TimeSlot
    fixed=version.entries.select_related('start_slot__template').prefetch_related('faculty_assignments',Prefetch('start_slot__template__slots',queryset=TimeSlot.objects.order_by('order')));fixed=(fixed.filter(locked=True)|version.entries.exclude(section_id__in=scope)) if mode=='REBUILD_UNLOCKED' and scope else fixed.filter(locked=True) if mode=='REBUILD_UNLOCKED' else fixed;section=defaultdict(set);faculty=defaultdict(set);room=defaultdict(set)
    for e in fixed:
        ordered=list(e.start_slot.template.slots.all());pos=next((i for i,x in enumerate(ordered) if x.id==e.start_slot_id),-1)
        for s in ordered[pos:pos+e.block_length]:
            key=(e.weekday,str(s.id));section[(str(e.section_id),)+key].add(str(e.id));room[(str(e.room_id),)+key].add(str(e.id)) if e.room_id else None
            for a in e.faculty_assignments.all():faculty[(str(a.faculty_id),)+key].add(str(e.id))
    return section,faculty,room
def fixed_conflicts(version,mode,scope=None):
    from common.models import TimeSlot
    from faculty.models import Faculty
    from rooms.models import Room
    scope_ids=set(map(str,scope or []))
    fixed=list(version.entries.select_related('start_slot','section','course_offering__course','room').prefetch_related('faculty_assignments'))
    if mode=='REBUILD_UNLOCKED':fixed=[e for e in fixed if e.locked or (scope_ids and str(e.section_id) not in scope_ids)]
    slots_by_template=defaultdict(list)
    for slot in TimeSlot.objects.all().order_by('template_id','order'):slots_by_template[slot.template_id].append(slot)
    rooms={str(room.pk):room.code for room in Room.objects.filter(pk__in={e.room_id for e in fixed if e.room_id})}
    faculty_ids={assignment.faculty_id for e in fixed for assignment in e.faculty_assignments.all()}
    faculty_names={str(person.pk):{'name':person.name or person.initials or person.employee_code or 'Unnamed faculty','employee_code':person.employee_code} for person in Faculty.objects.filter(pk__in=faculty_ids)}
    seen=defaultdict(list);conflicts=[];emitted=set()
    def entry_detail(entry):return {'id':str(entry.pk),'section':entry.section.name,'course':entry.course_offering.course.code,'course_name':entry.course_offering.course.name,'locked':entry.locked}
    for entry in fixed:
        if entry.weekday not in WORKING_DAYS:
            day_name=WEEKDAY_NAMES[entry.weekday] if 0<=entry.weekday<len(WEEKDAY_NAMES) else str(entry.weekday)
            conflicts.append({'code':WEEKEND_ERROR_CODE,'entry_id':str(entry.pk),'weekday':entry.weekday,'day':day_name,'section':entry.section.name,'course':entry.course_offering.course.code,'message':'Classes can only be scheduled Monday through Friday.'})
            continue
        ordered=slots_by_template[entry.start_slot.template_id]
        position=next((index for index,slot in enumerate(ordered) if slot.pk==entry.start_slot_id),-1)
        if position<0:continue
        occupied=ordered[position:position+entry.block_length]
        resources=[('SECTION',str(entry.section_id))]+([('ROOM',str(entry.room_id))] if entry.room_id else [])+[('FACULTY',str(assignment.faculty_id)) for assignment in entry.faculty_assignments.all()]
        for kind,resource_id in resources:
            for slot in occupied:
                key=(kind,resource_id,entry.weekday,str(slot.pk))
                for previous in seen[key]:
                    pair=tuple(sorted((str(previous.pk),str(entry.pk))))
                    unique=(kind,resource_id,entry.weekday,str(slot.pk),pair)
                    if unique in emitted:continue
                    emitted.add(unique)
                    resource=rooms.get(resource_id,resource_id) if kind=='ROOM' else faculty_names.get(resource_id,{}).get('employee_code') or faculty_names.get(resource_id,{}).get('name',resource_id) if kind=='FACULTY' else entry.section.name
                    day=WEEKDAY_NAMES[entry.weekday] if 0<=entry.weekday<len(WEEKDAY_NAMES) else str(entry.weekday)
                    conflicts.append({'code':f'LOCKED_{kind}_CLASH','entry_ids':list(pair),'resource_id':resource_id,'resource':resource,'room':resource if kind=='ROOM' else None,'faculty':faculty_names.get(resource_id) if kind=='FACULTY' else None,'weekday':entry.weekday,'day':day,'slot_id':str(slot.pk),'time_slot':slot.label,'entry_1':entry_detail(previous),'entry_2':entry_detail(entry),'source_version':str(version.pk),'message':f'{kind.title()} {resource} is assigned to two preserved entries on {day} {slot.label}.'})
                seen[key].append(entry)
    return conflicts

def resource_capacity_diagnostics(version,requirements,config,statistics=None):
    """Check aggregate physical capacity for requirements that are always offline.

    Hybrid requirements choose online/offline at placement time. Counting those
    periods as physical demand here would recreate the false LAB shortage this
    hybrid model is intended to prevent; offline candidates remain room-limited.
    """
    from collections import defaultdict
    from common.models import TimeSlot
    from rooms.models import Room,RoomAvailability
    room_types=defaultdict(list); reserved_requirements=defaultdict(list)
    from academics.models import Section,CourseOffering
    section_info={str(section.pk):section for section in Section.objects.filter(pk__in={item.section_id for item in requirements}).select_related('program').prefetch_related('period_delivery_policies')}
    from academics.delivery import get_section_delivery_policy
    policies={section_id:get_section_delivery_policy(section) for section_id,section in section_info.items()}
    if statistics is not None:
        statistics['physical_required_periods']=sum(item.block_length for item in requirements if policies.get(item.section_id,('STANDARD',None))[0]!='HYBRID')
        statistics['hybrid_flexible_periods']=sum(item.block_length for item in requirements if policies.get(item.section_id,('STANDARD',None))[0]=='HYBRID')
    from rooms.services.eligibility import normalize_room_type_requirement
    from rooms.services.eligibility import room_satisfies_requirement
    for requirement in requirements:
        section=section_info.get(requirement.section_id)
        if section:
            from rooms.services.eligibility import required_mtech_room_code
            reserved_code=required_mtech_room_code(section)
            if reserved_code:reserved_requirements[(reserved_code,section.year,section.program_id)].append((requirement,section))
            elif policies.get(requirement.section_id,('STANDARD',None))[0]!='HYBRID':room_types[(section.year,normalize_room_type_requirement(requirement.room_type_requirement),section.program_id)].append((requirement,section))
    all_rooms=list(Room.objects.select_related('reserved_program').all()); rooms=[room for room in all_rooms if room.active]
    from rooms.models import RoomEligibilityException
    exceptions_by_room=defaultdict(list)
    for exception in RoomEligibilityException.objects.filter(room_id__in=[room.pk for room in all_rooms],active=True).select_related('program','course'):
        exceptions_by_room[str(exception.room_id)].append(exception)
    exception_cache={}
    def cached_allow_exception(room,section,course,required_room_code=None):
        if required_room_code and room.code!=required_room_code:return None
        key=(str(room.pk),str(section.pk),str(course.pk) if course else None,required_room_code)
        if key not in exception_cache:
            matches=[row for row in exceptions_by_room.get(str(room.pk),()) if row.allow and row.program_id==section.program_id and row.year==section.year and (row.course_id is None or (course and row.course_id==course.pk))]
            matches.sort(key=lambda row:row.course_id is not None,reverse=True)
            exception_cache[key]=matches[0] if matches else None
        return exception_cache[key]
    offering_courses={str(row.pk):row.course for row in CourseOffering.objects.filter(pk__in={item.course_offering_id for item in requirements}).select_related('course')}
    slots=list(TimeSlot.objects.filter(template__institution=version.timetable.institution,is_break=False).values_list('id',flat=True))
    blocked={(str(row.room_id),row.weekday,str(row.time_slot_id)) for row in RoomAvailability.objects.filter(room__in=rooms,status__in=['BLOCKED','MAINTENANCE'])}
    fixed_rooms=fixed_occupancy(version,config.get('mode','FILL_GAPS'),config.get('section_ids'))[2]
    hybrid_year_days={(section.year,policies[str(section.pk)][1]) for section in section_info.values() if policies[str(section.pk)][0]=='HYBRID' and policies[str(section.pk)][1] in WORKING_DAYS}
    if statistics is not None:
        statistics['hybrid_offline_room_capacity_periods']=sum(1 for year,weekday in hybrid_year_days for room in rooms if (room.allowed_year is None or room.allowed_year==year) and (not room.exclusive_reservation or any(section.year==year and policies[str(section.pk)][0]=='HYBRID' and section.program_id==room.reserved_program_id and room.reserved_year==year for section in section_info.values())) for slot_id in slots if (str(room.pk),weekday,str(slot_id)) not in blocked and (str(room.pk),weekday,str(slot_id)) not in fixed_rooms)
    errors=[]
    for (code,year,program_id),items in reserved_requirements.items():
        room=next((item for item in all_rooms if item.code==code),None)
        program=items[0][1].program
        if not room or not room.active or not room.exclusive_reservation or room.reserved_year!=year or room.reserved_program_id!=program_id:
            errors.append({'code':'MTECH_RESERVED_ROOM_UNAVAILABLE','room_code':code,'section_year':year,'program':program.name,'offering_count':len({req.course_offering_id for req,_ in items}),'message':f'Reserved Room {code} is unavailable or not configured for {program.name} Year {year}.'})
        elif room.capacity<max(section.student_strength for _,section in items):
            errors.append({'code':'MTECH_RESERVED_ROOM_CAPACITY','room_code':code,'section_year':year,'available_capacity':room.capacity,'required_capacity':max(section.student_strength for _,section in items),'message':f'Reserved Room {code} does not have enough capacity for all {program.name} Year {year} sections.'})
    offering_meta={str(item.pk):(item.course.code,item.section.name) for item in CourseOffering.objects.filter(pk__in={requirement.course_offering_id for items in room_types.values() for requirement,_section in items}).select_related('course','section')}
    for (year,room_type,program_id),items in room_types.items():
        required_capacity=max((requirement.required_capacity for requirement,_section in items),default=0)
        candidates_by_type=[room for room in rooms if room.capacity>=required_capacity and all(room_satisfies_requirement(room, requirement.room_type_requirement) for requirement,_section in items)]
        eligible=[]
        exceptions_used=[]
        for room in candidates_by_type:
            for requirement, section in items:
                course=offering_courses.get(requirement.course_offering_id)
                fixed_code=None
                from rooms.services.eligibility import required_course_room_code
                fixed_code=required_course_room_code(course) if course else None
                if fixed_code and room.code!=fixed_code:
                    continue
                year_rejected=room.allowed_year is not None and room.allowed_year!=section.year
                reservation_rejected=room.exclusive_reservation and (room.reserved_program_id!=section.program_id or room.reserved_year!=section.year)
                exception=cached_allow_exception(room,section,course,fixed_code) if year_rejected or reservation_rejected else None
                if not year_rejected and not reservation_rejected or exception:
                    if room not in eligible:eligible.append(room)
                    if exception:
                        detail={'room':room.code,'program':section.program.code,'year':section.year,'course':course.code,'exception_id':str(exception.pk)}
                        if detail not in exceptions_used:exceptions_used.append(detail)
                    break
        rejected_by_year=[{'code':room.code,'allowed_year':room.allowed_year,'section_year':year} for room in candidates_by_type if room.allowed_year is not None and room.allowed_year!=year and not any(cached_allow_exception(room,section,offering_courses.get(requirement.course_offering_id),required_course_room_code(offering_courses.get(requirement.course_offering_id)) if offering_courses.get(requirement.course_offering_id) else None) for requirement,section in items)]
        rejected_by_room_type=[{'code':room.code,'room_type':room.room_type} for room in rooms if room.capacity>=required_capacity and room_type and not room_satisfies_requirement(room,room_type)]
        # Report reservation conflicts independently of the year restriction;
        # one candidate can violate both, and both facts help explain the
        # master-data mismatch without weakening either eligibility rule.
        rejected_by_reservation=[{'code':room.code,'reserved_program':room.reserved_program.code if room.reserved_program_id else None,'reserved_year':room.reserved_year} for room in candidates_by_type if room.exclusive_reservation and (room.reserved_program_id!=program_id or room.reserved_year!=year) and not any(cached_allow_exception(room,section,offering_courses.get(requirement.course_offering_id),required_course_room_code(offering_courses.get(requirement.course_offering_id)) if offering_courses.get(requirement.course_offering_id) else None) for requirement,section in items)]
        example_requirement,example_section=items[0]
        example_course,example_name=offering_meta.get(example_requirement.course_offering_id,('',example_section.name))
        if exceptions_used and statistics is not None:
            statistics.setdefault('room_eligibility_exceptions_applied',[]).extend(item for item in exceptions_used if item not in statistics.get('room_eligibility_exceptions_applied',[]))
        if not eligible:
            errors.append({'code':'NO_ELIGIBLE_ROOM','section_year':year,'room_type':room_type or None,'requirement':room_type or 'Any','offering_count':len({requirement.course_offering_id for requirement,_section in items}),'example_course':example_course,'example_section':example_name,'candidate_rooms_by_type':[room.code for room in candidates_by_type],'rejected_by_year':rejected_by_year,'rejected_by_room_type':rejected_by_room_type,'rejected_by_reservation':rejected_by_reservation,'message':f'No room is eligible for Year {year} requirements with room type {room_type or "Any"}.'})
            continue
        available=sum(1 for room in eligible for weekday in WORKING_DAYS for slot_id in slots if (str(room.id),weekday,str(slot_id)) not in blocked and (str(room.id),weekday,str(slot_id)) not in fixed_rooms)
        required=sum(requirement.block_length for requirement,_section in items)
        if required>available:
            errors.append({'code':'INSUFFICIENT_ROOM_CAPACITY','section_year':year,'room_type':room_type or None,'required_periods':required,'available_periods':available,'shortfall_periods':required-available,'offering_count':len({requirement.course_offering_id for requirement,_section in items}),'message':f'Year {year} {room_type or "eligible rooms"} have capacity for {available} periods, but the selected requirements need {required} periods ({required-available} more than available).'})
    if reserved_requirements:
        slot_rows=list(TimeSlot.objects.filter(template__institution=version.timetable.institution,is_break=False).select_related('template').order_by('template_id','order'))
        by_template=defaultdict(list)
        for slot in slot_rows:by_template[slot.template_id].append(slot)
        for (code,year,program_id),items in reserved_requirements.items():
            room=next((item for item in all_rooms if item.code==code),None)
            if not room or not room.active or not room.exclusive_reservation or room.reserved_year!=year or room.reserved_program_id!=program_id:continue
            for requirement,section in items:
                policy_mode,offline_weekday=policies.get(str(section.pk),('STANDARD',None))
                days=[offline_weekday] if policy_mode=='HYBRID' else WORKING_DAYS
                possible=False
                for weekday in days:
                    for ordered in by_template.values():
                        for index in range(len(ordered)-requirement.block_length+1):
                            window=ordered[index:index+requirement.block_length]
                            if any(window[i].order+1!=window[i+1].order for i in range(len(window)-1)):continue
                            if any((str(room.pk),weekday,str(slot.pk)) in blocked or (str(room.pk),weekday,str(slot.pk)) in fixed_rooms for slot in window):continue
                            possible=True;break
                        if possible:break
                    if possible:break
                if not possible:
                    errors.append({'code':'MTECH_RESERVED_ROOM_UNAVAILABLE','room_code':code,'section_year':year,'section_id':str(section.pk),'course_offering_id':requirement.course_offering_id,'block_length':requirement.block_length,'message':f'Reserved Room {code} has no available {requirement.block_length}-period block for {section.name}.'})
    return errors

def build_candidates(version,requirements,config,profile=None):
    candidate_build_started=time.perf_counter()
    from academics.models import CourseOffering
    from common.models import TimeSlot
    from rooms.models import Room
    from faculty.models import FacultyAvailability
    from rooms.models import RoomAvailability,RoomEligibilityException
    # A timetable must only use its institution's slot templates. Loading every
    # institution's slots multiplies candidate placements and admits invalid slots.
    slots=list(TimeSlot.objects.filter(template__institution=version.timetable.institution).select_related('template').order_by('template_id','order'));by_template=defaultdict(list)
    for s in slots:by_template[str(s.template_id)].append(s)
    windows_by_block={}
    for block in {requirement.block_length for requirement in requirements}:
        windows=[]
        for template_slots in by_template.values():
            for index,start in enumerate(template_slots):
                window=template_slots[index:index+block]
                if len(window)==block and not any(slot.is_break for slot in window) and all(window[i].order+1==window[i+1].order for i in range(len(window)-1)):
                    windows.append((start,window))
        windows_by_block[block]=windows
    section,faculty,occupied=fixed_occupancy(version,config.get('mode','FILL_GAPS'),config.get('section_ids'));rooms=list(Room.objects.select_related('reserved_program').filter(active=True));sections={str(s.id):s for s in version.timetable.semester.sections.filter(id__in=[r.section_id for r in requirements]).select_related('program','semester__session').prefetch_related('period_delivery_policies','weekly_off_policies')};faculty_ids={str(a.faculty_id) for r in requirements for a in r.faculty};blocked_faculty={(str(a.faculty_id),a.weekday,str(a.time_slot_id)) for a in FacultyAvailability.objects.filter(faculty_id__in=faculty_ids,is_available=False)};blocked_rooms={(str(a.room_id),a.weekday,str(a.time_slot_id)) for a in RoomAvailability.objects.filter(room_id__in=[r.id for r in rooms],status__in=['BLOCKED','MAINTENANCE'])};out=defaultdict(list);diagnostics=[]
    existing_preferences=defaultdict(set)
    for entry in version.entries.filter(locked=False,room_id__isnull=False).values('room_id','course_offering_id','weekday','start_slot_id','block_length'):
        existing_preferences[str(entry['room_id'])].add((str(entry['course_offering_id']),entry['weekday'],str(entry['start_slot_id']),entry['block_length']))
    blocked_by_room=defaultdict(set);occupied_by_room=defaultdict(set)
    for room_id,weekday,slot_id in blocked_rooms:blocked_by_room[room_id].add((weekday,slot_id))
    for (room_id,weekday,slot_id),_entry_ids in occupied.items():occupied_by_room[room_id].add((weekday,slot_id))
    exception_rows=list(RoomEligibilityException.objects.filter(room_id__in=[room.id for room in rooms],active=True).select_related('course','program'))
    exceptions_by_room=defaultdict(list)
    for row in exception_rows:exceptions_by_room[str(row.room_id)].append(row)
    room_pool_started=time.perf_counter();room_groups={};room_group_by_room={}
    from rooms.services.eligibility import room_pool_signature
    for room in rooms:
        room_id=str(room.id)
        blocked_signature=frozenset(blocked_by_room.get(room_id,()))
        occupied_signature=frozenset(occupied_by_room.get(room_id,()))
        signature=(room_pool_signature(room,exceptions_by_room.get(room_id,())),blocked_signature,occupied_signature,frozenset(existing_preferences.get(room_id,())))
        room_groups.setdefault(signature,[]).append(room)
    room_group_members={}
    grouped_rooms=[]
    for members in room_groups.values():
        members.sort(key=lambda room:str(room.pk))
        group_id=f'ROOM_GROUP:{members[0].pk}'
        grouped_rooms.append((group_id,members[0],members))
        room_group_members[group_id]=[str(room.pk) for room in members]
        for room in members:room_group_by_room[str(room.pk)]=group_id
    room_pool_construction_seconds=time.perf_counter()-room_pool_started
    offering_rows=list(CourseOffering.objects.filter(pk__in={r.course_offering_id for r in requirements}).select_related('course','section','preferred_room'))
    offering_by_id={str(item.pk):item for item in offering_rows}
    offer_meta={str(item.pk):(item.course.code,item.section.name) for item in offering_rows}
    profile_rows=[];hybrid_candidate_construction_seconds=0;eligibility_cache={};window_eligibility_cache={};preferred_room_advisories=set();off_day_candidate_placements=0
    equivalent_requirement_cache={}
    for req_index,req in enumerate(requirements):
        if req_index % 100 == 0:
            print(f'[GEN RUN] candidate progress requirements={req_index}/{len(requirements)} elapsed={time.perf_counter()-candidate_build_started:.1f}s',flush=True)
        requirement_started=time.perf_counter()
        equivalent_key=(req.course_offering_id,req.section_id,req.entry_type,req.block_length,req.faculty,req.room_type_requirement,req.required_capacity)
        cached=equivalent_requirement_cache.get(equivalent_key)
        if cached is not None:
            cached_candidates,cached_profile,cached_diagnostics,cached_off_day_count=cached
            out[req.requirement_id]=[Candidate(req.requirement_id,item.course_offering_id,item.section_id,item.weekday,item.start_slot_id,item.occupied_slot_ids,item.block_length,item.room_id,item.faculty,item.entry_type,item.delivery_mode) for item in cached_candidates]
            profile_rows.append(cached_profile|{'requirement_id':req.requirement_id})
            diagnostics.extend((item|{'requirement_id':req.requirement_id}) if item.get('requirement_id') else item.copy() for item in cached_diagnostics)
            off_day_candidate_placements+=cached_off_day_count
            continue
        diagnostic_start=len(diagnostics);off_day_start=off_day_candidate_placements
        section_obj=sections.get(str(req.section_id));strength=section_obj.student_strength if section_obj else 0
        from academics.delivery import get_section_delivery_policy
        delivery_policy,offline_weekday=get_section_delivery_policy(section_obj)
        hybrid=bool(section_obj and delivery_policy=='HYBRID')
        if hybrid and offline_weekday is None:
            diagnostics.append({'code':'MISSING_OFFLINE_DAY','section_id':str(req.section_id),'course_offering_id':req.course_offering_id})
            continue
        from rooms.services.eligibility import required_mtech_room_code, required_course_room_code
        required_reserved_code=required_mtech_room_code(section_obj) if section_obj else None
        fixed_course_code=offer_meta.get(req.course_offering_id,('',))[0]
        offering=offering_by_id.get(req.course_offering_id)
        required_course_code=required_course_room_code(offering) if offering else None
        from rooms.services.eligibility import normalize_room_type_requirement
        required_room_type=normalize_room_type_requirement(req.room_type_requirement)
        from rooms.services.eligibility import room_eligibility_error
        eligible_rooms=[]
        for group_id,representative,members in grouped_rooms:
            # A course-specific fixed room is authoritative over a generic
            # room-type label on the offering (e.g. RBS5151 -> Room 105).
            eligibility_key=(group_id,req.course_offering_id,req.section_id,required_room_type,req.entry_type)
            if eligibility_key not in eligibility_cache:
                eligibility_cache[eligibility_key]=room_eligibility_error(representative,offering,section_obj,required_room_type=required_room_type,activity_type=req.entry_type,exceptions=exception_rows)
            error=eligibility_cache[eligibility_key]
            if error:continue
            exception=next((row for row in exceptions_by_room.get(str(representative.pk),()) if row.allow and row.program_id==section_obj.program_id and row.year==section_obj.year and (row.course_id is None or (offering and row.course_id==offering.course_id))),None)
            if exception and (representative.allowed_year not in (None,section_obj.year) or (representative.exclusive_reservation and (representative.reserved_program_id!=section_obj.program_id or representative.reserved_year!=section_obj.year))):
                diagnostics.append({'code':'ROOM_ELIGIBILITY_EXCEPTION_APPLIED','room_code':representative.code,'program':section_obj.program.code,'year':section_obj.year,'course_code':offering.course.code if offering else None,'exception_id':str(exception.pk),'message':f'Room eligibility exception applied: {representative.code}; Program: {section_obj.program.code}; Year: {section_obj.year}; Course: {offering.course.code if offering else "—"}.'})
            # An exact matching exclusive reservation is eligible. A mismatch
            # reaches here only when the scoped exception check above allowed it.
            eligible_rooms.append((group_id,representative,members))
        eligible_days=set();eligible_starts=set();online_candidates=0;offline_candidates=0
        for day in WORKING_DAYS:
            from academics.weekly_off import get_section_weekly_off_policy
            weekly_off_policy=get_section_weekly_off_policy(section_obj,version.timetable.academic_session,version.timetable.semester)
            if weekly_off_policy and weekly_off_policy.policy_type=='WEEKLY_OFF' and day==weekly_off_policy.weekday:
                # Count what would otherwise have been candidate placements without
                # constructing Candidate objects or solver variables for this day.
                delivery_mode='ONLINE' if hybrid and day!=offline_weekday else 'OFFLINE'
                for start,window in windows_by_block[req.block_length]:
                    ids=tuple(str(x.id) for x in window)
                    if any((str(req.section_id),day,s) in section for s in ids) or any((a.faculty_id,day,s) in faculty or (a.faculty_id,day,s) in blocked_faculty for a in req.faculty for s in ids):continue
                    if delivery_mode=='ONLINE':
                        off_day_candidate_placements+=1
                        continue
                    for group_id,room,_members in eligible_rooms:
                        window_key=(group_id,req.course_offering_id,req.section_id,required_room_type,req.entry_type,day,ids)
                        if window_key not in window_eligibility_cache:
                            window_eligibility_cache[window_key]=room_eligibility_error(room,offering,section_obj,required_room_type=required_room_type,activity_type=req.entry_type,weekday=day,slot_ids=ids,blocked_slots=blocked_rooms,occupied_slots=occupied,exceptions=exception_rows)
                        if not window_eligibility_cache[window_key]:off_day_candidate_placements+=1
                continue
            for start,window in windows_by_block[req.block_length]:
                ids=tuple(str(x.id) for x in window)
                if any((str(req.section_id),day,s) in section for s in ids) or any((a.faculty_id,day,s) in faculty for a in req.faculty for s in ids):continue
                if any((a.faculty_id,day,s) in blocked_faculty for a in req.faculty for s in ids):continue
                delivery_mode='ONLINE' if hybrid and day!=offline_weekday else 'OFFLINE'
                if delivery_mode=='ONLINE':
                    out[req.requirement_id].append(Candidate(req.requirement_id,req.course_offering_id,req.section_id,day,str(start.id),ids,req.block_length,'',req.faculty,req.entry_type,'ONLINE'))
                    eligible_days.add(day);eligible_starts.add((day,str(start.id)));online_candidates+=1
                    continue
                placement_added=False
                for group_id,room,_members in eligible_rooms:
                    # Every member in this group has the same solver-relevant
                    # occupancy/availability signature, so one choice represents
                    # the interchangeable physical rooms in the group.
                    window_key=(group_id,req.course_offering_id,req.section_id,required_room_type,req.entry_type,day,ids)
                    if window_key not in window_eligibility_cache:
                        window_eligibility_cache[window_key]=room_eligibility_error(room,offering,section_obj,required_room_type=required_room_type,activity_type=req.entry_type,weekday=day,slot_ids=ids,blocked_slots=blocked_rooms,occupied_slots=occupied,exceptions=exception_rows)
                    error=window_eligibility_cache[window_key]
                    if error:continue
                    out[req.requirement_id].append(Candidate(req.requirement_id,req.course_offering_id,req.section_id,day,str(start.id),ids,req.block_length,group_id,req.faculty,req.entry_type,'OFFLINE'))
                    placement_added=True;offline_candidates+=1
                if placement_added:
                    eligible_days.add(day);eligible_starts.add((day,str(start.id)))
        course_code,section_name=offer_meta.get(req.course_offering_id,('',req.section_id))
        preferred_room_id=str(offering.preferred_room_id) if offering and offering.preferred_room_id else None
        preferred_group_id=room_group_by_room.get(preferred_room_id) if preferred_room_id else None
        preferred_candidate_count=sum(candidate.delivery_mode!='ONLINE' and candidate.room_id==preferred_group_id for candidate in out[req.requirement_id]) if preferred_group_id else 0
        if preferred_room_id and not preferred_candidate_count and req.course_offering_id not in preferred_room_advisories:
            diagnostics.append({'code':'PREFERRED_ROOM_UNAVAILABLE','severity':'WARNING','course_offering_id':req.course_offering_id,'course_code':course_code,'section':section_name,'preferred_room':offering.preferred_room.code,'message':f'Preferred room {offering.preferred_room.code} is not eligible or available for this offering; other canonically eligible rooms remain available.'})
            preferred_room_advisories.add(req.course_offering_id)
        profile_rows.append({'requirement_id':req.requirement_id,'course_offering_id':req.course_offering_id,'course_code':course_code,'section':section_name,'activity_type':req.entry_type,'block_length':req.block_length,'delivery_policy':delivery_policy,'offline_weekday':offline_weekday,'online_candidate_count':online_candidates,'offline_candidate_count':offline_candidates,'eligible_room_count':sum(len(members) for _group_id,_room,members in eligible_rooms),'eligible_room_group_count':len(eligible_rooms),'eligible_day_count':len(eligible_days),'eligible_start_count':len(eligible_starts),'candidate_count':len(out[req.requirement_id]),'preferred_room_id':preferred_room_id,'preferred_room_group_id':preferred_group_id if preferred_candidate_count else None,'preferred_room_candidate_count':preferred_candidate_count})
        if hybrid:hybrid_candidate_construction_seconds+=time.perf_counter()-requirement_started
        if not out[req.requirement_id]:
            code='REQUIRED_ROOM_UNAVAILABLE' if required_course_code else 'NO_VALID_PLACEMENT'
            message=(f'Room {required_course_code} is required for {offering.course.name}; no eligible contiguous placement is available.' if required_course_code and offering else 'No eligible room and contiguous time placement is available for this requirement.')
            diagnostics.append({'code':code,'severity':'ERROR','requirement_id':req.requirement_id,'course_offering_id':req.course_offering_id,'course_code':course_code,'course_name':offering.course.name if offering else None,'section_id':req.section_id,'section':section_name,'activity_type':req.entry_type,'block_length':req.block_length,'required_room_code':required_course_code,'eligible_room_count':sum(len(members) for _group_id,_room,members in eligible_rooms),'candidate_count':0,'message':message})
        equivalent_requirement_cache[equivalent_key]=(out[req.requirement_id],profile_rows[-1],diagnostics[diagnostic_start:],off_day_candidate_placements-off_day_start)
    if profile is not None:
        counts=sorted(row['candidate_count'] for row in profile_rows)
        percentile=lambda fraction: counts[min(len(counts)-1,max(0,int((len(counts)-1)*fraction)))] if counts else 0
        hybrid_rows=[row for row in profile_rows if row['delivery_policy']=='HYBRID']
        counts_by_hybrid_group={'offerings':len({row['course_offering_id'] for row in hybrid_rows}),'requirements':len(hybrid_rows),'online_candidates':sum(row['online_candidate_count'] for row in hybrid_rows),'offline_candidates':sum(row['offline_candidate_count'] for row in hybrid_rows),'candidate_count':sum(row['candidate_count'] for row in hybrid_rows)}
        after_off_day_filtering=sum(row['candidate_count'] for row in profile_rows)
        profile.update({'requirements':profile_rows,'average_candidates_per_requirement':sum(counts)/len(counts) if counts else 0,'p50_candidates_per_requirement':percentile(.50),'p95_candidates_per_requirement':percentile(.95),'max_candidates_per_requirement':max(counts,default=0),'top_30':sorted(profile_rows,key=lambda row:row['candidate_count'],reverse=True)[:30],'hybrid_profile':counts_by_hybrid_group,'weekly_off_candidate_placements_before_filtering':after_off_day_filtering+off_day_candidate_placements,'weekly_off_candidate_placements_after_filtering':after_off_day_filtering,'weekly_off_candidate_placements_eliminated':off_day_candidate_placements,'hybrid_candidate_construction_seconds':hybrid_candidate_construction_seconds,'room_pool_construction_seconds':room_pool_construction_seconds,'candidate_construction_seconds':time.perf_counter()-candidate_build_started,'equivalent_requirement_cache_hits':len(requirements)-len(equivalent_requirement_cache),'equivalent_requirement_cache_groups':len(equivalent_requirement_cache),'eligibility_cache_entries':len(eligibility_cache)+len(window_eligibility_cache),'_room_group_members':room_group_members,'_room_group_by_room':room_group_by_room,'_room_group_sizes':{group_id:len(members) for group_id,_room,members in grouped_rooms},'_room_slot_orders':{str(slot.id):slot.order for slot in slots}})
    return out,diagnostics
def candidate_dict(c):return asdict(c)|{'faculty':[asdict(x) for x in c.faculty]}
