from dataclasses import dataclass,asdict
from collections import defaultdict
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
        for i,length in enumerate(lengths):result.append(Requirement(f'{offering.id}:{i}',str(offering.id),str(offering.section_id),rule.get('entry_type',offering.default_class_type),int(length),tuple(FacultyAssignment(str(x['faculty_id']),x.get('role','PRIMARY')) for x in assign),offering.room_type_requirement,offering.section.student_strength))
    return result,errors
def fixed_occupancy(version,mode,scope=None):
    fixed=version.entries.select_related('start_slot__template').prefetch_related('faculty_assignments');fixed=(fixed.filter(locked=True)|version.entries.exclude(section_id__in=scope)) if mode=='REBUILD_UNLOCKED' and scope else fixed.filter(locked=True) if mode=='REBUILD_UNLOCKED' else fixed;section=defaultdict(set);faculty=defaultdict(set);room=defaultdict(set)
    for e in fixed:
        ordered=list(e.start_slot.template.slots.order_by('order'));pos=next((i for i,x in enumerate(ordered) if x.id==e.start_slot_id),-1)
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
                    day=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][entry.weekday] if 0<=entry.weekday<7 else str(entry.weekday)
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
    room_types=defaultdict(list)
    from academics.models import Section
    section_policies={str(section.pk):section.delivery_policy for section in Section.objects.filter(pk__in={item.section_id for item in requirements}).only('id','delivery_policy')}
    if statistics is not None:
        statistics['physical_required_periods']=sum(item.block_length for item in requirements if section_policies.get(item.section_id)!='HYBRID')
        statistics['hybrid_flexible_periods']=sum(item.block_length for item in requirements if section_policies.get(item.section_id)=='HYBRID')
    for requirement in requirements:
        if section_policies.get(requirement.section_id)!='HYBRID':room_types[requirement.room_type_requirement or ''].append(requirement)
    rooms=list(Room.objects.filter(active=True))
    slots=list(TimeSlot.objects.filter(template__institution=version.timetable.institution,is_break=False).values_list('id',flat=True))
    blocked={(str(row.room_id),row.weekday,str(row.time_slot_id)) for row in RoomAvailability.objects.filter(room__in=rooms,status__in=['BLOCKED','MAINTENANCE'])}
    fixed_rooms=fixed_occupancy(version,config.get('mode','FILL_GAPS'),config.get('section_ids'))[2]
    hybrid_days=set(Section.objects.filter(pk__in={item.section_id for item in requirements},delivery_policy='HYBRID',offline_weekday__isnull=False).values_list('offline_weekday',flat=True))
    if statistics is not None:
        statistics['hybrid_offline_room_capacity_periods']=sum(1 for room in rooms for weekday in hybrid_days for slot_id in slots if (str(room.pk),weekday,str(slot_id)) not in blocked and (str(room.pk),weekday,str(slot_id)) not in fixed_rooms)
    errors=[]
    for room_type,items in room_types.items():
        eligible=[room for room in rooms if room.capacity>=max((item.required_capacity for item in items),default=0) and (not room_type or room.room_type==room_type)]
        if not eligible:continue
        available=sum(1 for room in eligible for weekday in range(6) for slot_id in slots if (str(room.id),weekday,str(slot_id)) not in blocked and (str(room.id),weekday,str(slot_id)) not in fixed_rooms)
        required=sum(item.block_length for item in items)
        if required>available:
            errors.append({'code':'INSUFFICIENT_ROOM_CAPACITY','room_type':room_type or None,'required_periods':required,'available_periods':available,'shortfall_periods':required-available,'offering_count':len({item.course_offering_id for item in items}),'message':f'{room_type or "Eligible rooms"} have capacity for {available} periods, but the selected requirements need {required} periods ({required-available} more than available).'})
    return errors

def build_candidates(version,requirements,config,profile=None):
    from academics.models import CourseOffering
    from common.models import TimeSlot
    from rooms.models import Room
    from faculty.models import FacultyAvailability
    from rooms.models import RoomAvailability
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
    section,faculty,occupied=fixed_occupancy(version,config.get('mode','FILL_GAPS'),config.get('section_ids'));rooms=list(Room.objects.filter(active=True));sections={str(s.id):s for s in version.timetable.semester.sections.filter(id__in=[r.section_id for r in requirements])};faculty_ids={str(a.faculty_id) for r in requirements for a in r.faculty};blocked_faculty={(str(a.faculty_id),a.weekday,str(a.time_slot_id)) for a in FacultyAvailability.objects.filter(faculty_id__in=faculty_ids,is_available=False)};blocked_rooms={(str(a.room_id),a.weekday,str(a.time_slot_id)) for a in RoomAvailability.objects.filter(room_id__in=[r.id for r in rooms],status__in=['BLOCKED','MAINTENANCE'])};out=defaultdict(list);diagnostics=[]
    existing_preferences=defaultdict(set)
    for entry in version.entries.filter(locked=False,room_id__isnull=False).values('room_id','course_offering_id','weekday','start_slot_id','block_length'):
        existing_preferences[str(entry['room_id'])].add((str(entry['course_offering_id']),entry['weekday'],str(entry['start_slot_id']),entry['block_length']))
    blocked_by_room=defaultdict(set);occupied_by_room=defaultdict(set)
    for room_id,weekday,slot_id in blocked_rooms:blocked_by_room[room_id].add((weekday,slot_id))
    for (room_id,weekday,slot_id),_entry_ids in occupied.items():occupied_by_room[room_id].add((weekday,slot_id))
    room_groups={};room_group_by_room={}
    for room in rooms:
        room_id=str(room.id)
        blocked_signature=frozenset(blocked_by_room.get(room_id,()))
        occupied_signature=frozenset(occupied_by_room.get(room_id,()))
        signature=(room.room_type,room.capacity,blocked_signature,occupied_signature,frozenset(existing_preferences.get(room_id,())))
        room_groups.setdefault(signature,[]).append(room)
    room_group_members={}
    grouped_rooms=[]
    for members in room_groups.values():
        members.sort(key=lambda room:str(room.pk))
        group_id=f'ROOM_GROUP:{members[0].pk}'
        grouped_rooms.append((group_id,members[0],members))
        room_group_members[group_id]=[str(room.pk) for room in members]
        for room in members:room_group_by_room[str(room.pk)]=group_id
    offer_meta={str(item.pk):(item.course.code,item.section.name) for item in CourseOffering.objects.filter(pk__in={r.course_offering_id for r in requirements}).select_related('course','section')}
    profile_rows=[]
    for req in requirements:
        section_obj=sections.get(str(req.section_id));strength=section_obj.student_strength if section_obj else 0
        hybrid=bool(section_obj and section_obj.delivery_policy=='HYBRID')
        if hybrid and section_obj.offline_weekday is None:
            diagnostics.append({'code':'MISSING_OFFLINE_DAY','section_id':str(req.section_id),'course_offering_id':req.course_offering_id})
            continue
        eligible_rooms=[(group_id,representative,members) for group_id,representative,members in grouped_rooms if representative.capacity>=strength and (not req.room_type_requirement or representative.room_type==req.room_type_requirement)]
        eligible_days=set();eligible_starts=set();online_candidates=0;offline_candidates=0
        for day in range(6):
            for start,window in windows_by_block[req.block_length]:
                ids=tuple(str(x.id) for x in window)
                if any((str(req.section_id),day,s) in section for s in ids) or any((a.faculty_id,day,s) in faculty for a in req.faculty for s in ids):continue
                if any((a.faculty_id,day,s) in blocked_faculty for a in req.faculty for s in ids):continue
                delivery_mode='ONLINE' if hybrid and day!=section_obj.offline_weekday else 'OFFLINE'
                if delivery_mode=='ONLINE':
                    out[req.requirement_id].append(Candidate(req.requirement_id,req.course_offering_id,req.section_id,day,str(start.id),ids,req.block_length,'',req.faculty,req.entry_type,'ONLINE'))
                    eligible_days.add(day);eligible_starts.add((day,str(start.id)));online_candidates+=1
                    continue
                placement_added=False
                for group_id,room,_members in eligible_rooms:
                    # Every member in this group has the same solver-relevant
                    # occupancy/availability signature, so one choice represents
                    # the interchangeable physical rooms in the group.
                    if any((str(room.id),day,slot_id) in occupied for slot_id in ids):continue
                    if any((str(room.id),day,slot_id) in blocked_rooms for slot_id in ids):continue
                    out[req.requirement_id].append(Candidate(req.requirement_id,req.course_offering_id,req.section_id,day,str(start.id),ids,req.block_length,group_id,req.faculty,req.entry_type,'OFFLINE'))
                    placement_added=True;offline_candidates+=1
                if placement_added:
                    eligible_days.add(day);eligible_starts.add((day,str(start.id)))
        course_code,section_name=offer_meta.get(req.course_offering_id,('',req.section_id))
        profile_rows.append({'course_code':course_code,'section':section_name,'activity_type':req.entry_type,'block_length':req.block_length,'delivery_policy':'HYBRID' if hybrid else 'STANDARD','online_candidate_count':online_candidates,'offline_candidate_count':offline_candidates,'eligible_room_count':sum(len(members) for _group_id,_room,members in eligible_rooms),'eligible_room_group_count':len(eligible_rooms),'eligible_day_count':len(eligible_days),'eligible_start_count':len(eligible_starts),'candidate_count':len(out[req.requirement_id])})
        if not out[req.requirement_id]:diagnostics.append({'code':'NO_VALID_PLACEMENT','requirement_id':req.requirement_id,'course_offering_id':req.course_offering_id,'section_id':req.section_id,'block_length':req.block_length})
    if profile is not None:
        counts=sorted(row['candidate_count'] for row in profile_rows)
        percentile=lambda fraction: counts[min(len(counts)-1,max(0,int((len(counts)-1)*fraction)))] if counts else 0
        profile.update({'requirements':profile_rows,'average_candidates_per_requirement':sum(counts)/len(counts) if counts else 0,'p50_candidates_per_requirement':percentile(.50),'p95_candidates_per_requirement':percentile(.95),'max_candidates_per_requirement':max(counts,default=0),'top_30':sorted(profile_rows,key=lambda row:row['candidate_count'],reverse=True)[:30],'_room_group_members':room_group_members,'_room_group_by_room':room_group_by_room,'_room_group_sizes':{group_id:len(members) for group_id,_room,members in grouped_rooms},'_room_slot_orders':{str(slot.id):slot.order for slot in slots}})
    return out,diagnostics
def candidate_dict(c):return asdict(c)|{'faculty':[asdict(x) for x in c.faculty]}
