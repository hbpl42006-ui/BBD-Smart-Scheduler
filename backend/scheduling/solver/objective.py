from collections import defaultdict

DEFAULT_OBJECTIVE_WEIGHTS={'spread_course_days':10,'balance_section_load':5,'minimize_section_gaps':4,'minimize_faculty_gaps':2,'preserve_existing':8}
def internal_gap_count(occupied_orders,break_orders=()):
    occupied=set(occupied_orders);breaks=set(break_orders)
    if len(occupied)<2:return 0
    lo,hi=min(occupied),max(occupied);return sum(1 for order in range(lo,hi+1) if order not in occupied and order not in breaks)
def weights(config):
    raw=config.get('soft_constraints',{}) or {};return {k:max(0,min(100,int(raw.get(k,v)))) for k,v in DEFAULT_OBJECTIVE_WEIGHTS.items()}
def build_objective_context(version,room_group_by_room=None):
    """Load schedule entries and assignments once for all candidate scoring."""
    entries=list(version.entries.select_related('start_slot').prefetch_related('faculty_assignments').order_by('pk'))
    entries_by_offering=defaultdict(list)
    entries_by_faculty_day=defaultdict(list)
    for entry in entries:
        entries_by_offering[str(entry.course_offering_id)].append(entry)
        for assignment in entry.faculty_assignments.all():
            entries_by_faculty_day[(str(assignment.faculty_id),entry.weekday)].append(entry)
    room_group_by_room=room_group_by_room or {}
    preserved_placements={(str(entry.course_offering_id),entry.weekday,str(entry.start_slot_id),entry.block_length,room_group_by_room.get(str(entry.room_id),str(entry.room_id)) if entry.room_id else '') for entry in entries if not entry.locked and (entry.room_id or entry.delivery_mode=='ONLINE')}
    return {'entries_by_offering':entries_by_offering,'entries_by_faculty_day':entries_by_faculty_day,'preserved_placements':preserved_placements,'base_score_cache':{},'objective_profile':{'room_preference':0.0,'course_spread':0.0,'section_gaps':0.0,'faculty_gaps':0.0,'daily_balance':0.0,'other_penalties':0.0}}

def _base_candidate_score(candidate,config,entries,context):
    from time import perf_counter
    w=weights(config);score=0
    started=perf_counter()
    used={entry.weekday for entry in entries}
    score += w['spread_course_days'] if candidate.weekday not in used else -w['spread_course_days']
    context['objective_profile']['course_spread']+=perf_counter()-started

    started=perf_counter()
    day_load=sum(entry.block_length for entry in entries if entry.weekday==candidate.weekday)
    score -= day_load*w['balance_section_load']
    context['objective_profile']['daily_balance']+=perf_counter()-started

    started=perf_counter()
    if w['minimize_section_gaps']:
        day_entries=[entry for entry in entries if entry.weekday==candidate.weekday]
        first_order=day_entries[0].start_slot.order if day_entries else candidate.block_length
        existing={entry.start_slot.order+i for entry in day_entries for i in range(entry.block_length)}
        existing.update(range(first_order, (day_entries[0].start_slot.order if day_entries else 0)+candidate.block_length))
        ordered=sorted(existing)
        gap_count=sum(1 for left,right in zip(ordered,ordered[1:]) if right-left>1)
        score -= w['minimize_section_gaps']*gap_count
    context['objective_profile']['section_gaps']+=perf_counter()-started

    started=perf_counter()
    if w['minimize_faculty_gaps']:
        for assignment in candidate.faculty:
            faculty_entries=context['entries_by_faculty_day'].get((str(assignment.faculty_id),candidate.weekday),[])
            occupied=[entry.start_slot.order+i for entry in faculty_entries for i in range(entry.block_length)]
            occupied.append(candidate.weekday+candidate.block_length)
            ordered=sorted(occupied)
            gap_count=sum(1 for left,right in zip(ordered,ordered[1:]) if right-left>1) if len(ordered)>=2 else 0
            score -= w['minimize_faculty_gaps']*gap_count
    context['objective_profile']['faculty_gaps']+=perf_counter()-started
    return score

def candidate_score(candidate,version,config,context=None):
    w=weights(config);score=0
    if context is not None:
        faculty_key=tuple((str(item.faculty_id),item.role) for item in candidate.faculty)
        base_key=(str(candidate.course_offering_id),candidate.weekday,candidate.block_length,faculty_key)
        if base_key not in context['base_score_cache']:
            entries=context['entries_by_offering'].get(str(candidate.course_offering_id),[])
            context['base_score_cache'][base_key]=_base_candidate_score(candidate,config,entries,context)
        score=context['base_score_cache'][base_key]
        if config.get('mode')=='REBUILD_UNLOCKED' and config.get('soft_constraints',{}).get('preserve_existing',True):
            if (str(candidate.course_offering_id),candidate.weekday,str(candidate.start_slot_id),candidate.block_length,str(candidate.room_id)) in context['preserved_placements']:
                score+=w['preserve_existing']
        return score
    if context is None:
        entries=list(version.entries.filter(course_offering_id=candidate.course_offering_id).select_related('start_slot').prefetch_related('faculty_assignments').order_by('pk'))
    else:
        entries=context['entries_by_offering'].get(str(candidate.course_offering_id),[])
    used={e.weekday for e in entries};score += w['spread_course_days'] if candidate.weekday not in used else -w['spread_course_days']
    day_load=sum(e.block_length for e in entries if e.weekday==candidate.weekday);score -= day_load*w['balance_section_load']
    if config.get('mode')=='REBUILD_UNLOCKED' and config.get('soft_constraints',{}).get('preserve_existing',True):
        if any(e.weekday==candidate.weekday and str(e.start_slot_id)==candidate.start_slot_id and e.block_length==candidate.block_length and str(e.room_id)==candidate.room_id for e in entries if not e.locked):score += w['preserve_existing']
    def gap_penalty(keys):
        if len(keys)<2:return 0
        ordered=sorted(keys);return sum(1 for a,b in zip(ordered,ordered[1:]) if b-a>1)
    if w['minimize_section_gaps']:
        existing={e.start_slot.order+i for e in entries if e.weekday==candidate.weekday for i in range(e.block_length)};existing.update(range(next((e.start_slot.order for e in entries if e.weekday==candidate.weekday),candidate.block_length),next((e.start_slot.order for e in entries if e.weekday==candidate.weekday),0)+candidate.block_length));score-=w['minimize_section_gaps']*gap_penalty(existing)
    if w['minimize_faculty_gaps']:
        for assignment in candidate.faculty:
            if context is None:
                fed=list(version.entries.filter(faculty_assignments__faculty_id=assignment.faculty_id,weekday=candidate.weekday).select_related('start_slot').order_by('pk'))
            else:
                fed=context['entries_by_faculty_day'].get((str(assignment.faculty_id),candidate.weekday),[])
            occupied=[e.start_slot.order+i for e in fed for i in range(e.block_length)];occupied.extend([candidate.weekday+candidate.block_length]);score-=w['minimize_faculty_gaps']*gap_penalty(occupied)
    return score
