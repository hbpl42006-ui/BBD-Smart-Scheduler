from collections import defaultdict

DEFAULT_OBJECTIVE_WEIGHTS={'spread_course_days':10,'balance_section_load':5,'minimize_section_gaps':4,'minimize_faculty_gaps':2,'preserve_existing':8,'preferred_room':10}
def internal_gap_count(occupied_orders,break_orders=()):
    occupied=set(occupied_orders);breaks=set(break_orders)
    if len(occupied)<2:return 0
    lo,hi=min(occupied),max(occupied);return sum(1 for order in range(lo,hi+1) if order not in occupied and order not in breaks)
def weights(config):
    raw=config.get('soft_constraints',{}) or {};return {k:max(0,min(100,int(raw.get(k,v)))) for k,v in DEFAULT_OBJECTIVE_WEIGHTS.items()}
def build_objective_context(version,room_group_by_room=None,slot_orders=None,break_orders=None,preferred_room_groups=None):
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
    if slot_orders is None or break_orders is None:
        from common.models import TimeSlot
        slots=list(TimeSlot.objects.all())
        if slot_orders is None:slot_orders={str(slot.pk):slot.order for slot in slots}
        if break_orders is None:break_orders={str(slot.pk):slot.order for slot in slots if slot.is_break}
    break_order_values=set(break_orders.values()) if hasattr(break_orders,'values') else set(break_orders)
    return {'entries_by_offering':entries_by_offering,'entries_by_faculty_day':entries_by_faculty_day,'preserved_placements':preserved_placements,'slot_orders':slot_orders,'break_orders':break_order_values,'slot_count':max(slot_orders.values(),default=0)+1,'preferred_room_groups':preferred_room_groups or {},'base_score_cache':{},'objective_profile':{'room_preference':0.0,'course_spread':0.0,'section_gaps':0.0,'faculty_gaps':0.0,'daily_balance':0.0,'other_penalties':0.0}}

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
    start_order=context['slot_orders'].get(str(candidate.start_slot_id),0)

    started=perf_counter()
    if w['minimize_section_gaps']:
        day_entries=[entry for entry in entries if entry.weekday==candidate.weekday]
        existing={entry.start_slot.order+i for entry in day_entries for i in range(entry.block_length)}
        existing.update(range(start_order,start_order+candidate.block_length))
        gap_count=internal_gap_count(existing,context['break_orders'])
        score -= w['minimize_section_gaps']*gap_count
    context['objective_profile']['section_gaps']+=perf_counter()-started

    started=perf_counter()
    if w['minimize_faculty_gaps']:
        for assignment in candidate.faculty:
            faculty_entries=context['entries_by_faculty_day'].get((str(assignment.faculty_id),candidate.weekday),[])
            occupied={entry.start_slot.order+i for entry in faculty_entries for i in range(entry.block_length)}
            occupied.update(range(start_order,start_order+candidate.block_length))
            gap_count=internal_gap_count(occupied,context['break_orders'])
            score -= w['minimize_faculty_gaps']*gap_count
    context['objective_profile']['faculty_gaps']+=perf_counter()-started
    return score

def candidate_score(candidate,version,config,context=None):
    w=weights(config);score=0
    if context is None:context=build_objective_context(version)
    faculty_key=tuple((str(item.faculty_id),item.role) for item in candidate.faculty)
    base_key=(str(candidate.course_offering_id),candidate.weekday,candidate.start_slot_id,candidate.block_length,faculty_key)
    if base_key not in context['base_score_cache']:
        entries=context['entries_by_offering'].get(str(candidate.course_offering_id),[])
        context['base_score_cache'][base_key]=_base_candidate_score(candidate,config,entries,context)
    # Preserve the existing objective's relative weights and use a deterministic
    # tie-breaker only when those weights produce equal-quality placements.
    score=context['base_score_cache'][base_key]*100
    slot_order=context['slot_orders'].get(str(candidate.start_slot_id),0)
    score-=candidate.weekday*context['slot_count']+slot_order
    preferred_group=context.get('preferred_room_groups',{}).get(str(candidate.course_offering_id))
    if candidate.delivery_mode!='ONLINE' and preferred_group and candidate.room_id==preferred_group:
        score+=w['preferred_room']*100
    if config.get('mode')=='REBUILD_UNLOCKED' and config.get('soft_constraints',{}).get('preserve_existing',True):
        if (str(candidate.course_offering_id),candidate.weekday,str(candidate.start_slot_id),candidate.block_length,str(candidate.room_id)) in context['preserved_placements']:score+=w['preserve_existing']*100
    return score
