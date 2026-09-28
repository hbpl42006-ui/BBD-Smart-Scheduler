DEFAULT_OBJECTIVE_WEIGHTS={'spread_course_days':10,'balance_section_load':5,'minimize_section_gaps':4,'minimize_faculty_gaps':2,'preserve_existing':8}
def internal_gap_count(occupied_orders,break_orders=()):
    occupied=set(occupied_orders);breaks=set(break_orders)
    if len(occupied)<2:return 0
    lo,hi=min(occupied),max(occupied);return sum(1 for order in range(lo,hi+1) if order not in occupied and order not in breaks)
def weights(config):
    raw=config.get('soft_constraints',{}) or {};return {k:max(0,min(100,int(raw.get(k,v)))) for k,v in DEFAULT_OBJECTIVE_WEIGHTS.items()}
def candidate_score(candidate,version,config):
    w=weights(config);score=0
    entries=list(version.entries.filter(course_offering_id=candidate.course_offering_id))
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
            fed=list(version.entries.filter(faculty_assignments__faculty_id=assignment.faculty_id,weekday=candidate.weekday).select_related('start_slot'));occupied=[e.start_slot.order+i for e in fed for i in range(e.block_length)];occupied.extend([candidate.weekday+candidate.block_length]);score-=w['minimize_faculty_gaps']*gap_penalty(occupied)
    return score
