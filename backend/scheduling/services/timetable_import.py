from io import BytesIO
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from openpyxl import Workbook
from academics.models import CourseOffering, Section
from common.models import TimeSlot
from faculty.models import Faculty
from rooms.models import Room
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
import logging
import time
from scheduling.weekdays import WORKING_DAYS, WEEKEND_DAYS, WEEKEND_ERROR_CODE, WEEKEND_ERROR_MESSAGE
from scheduling.services.faculty_names import faculty_name_match_kind

logger = logging.getLogger(__name__)

ALIASES={'section':'section','section_name':'section','course_code':'course_code','course':'course_code','credit':'credit','course_credit':'credit','faculty_name':'faculty_name','name':'faculty_name','employee_code':'employee_code','faculty_code':'employee_code','day':'day','weekday':'day','time_slot':'time_slot','slot':'time_slot','time':'time_slot','room_no':'room','room_no.':'room','room_number':'room','room_code':'room','room':'room','faculty_role':'faculty_role','block_length':'block_length','entry_type':'entry_type'}
DAYS={x.lower():i for i,x in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])}
ONLINE_ROOM_VALUES={'online','virtual'}

class TimetableImportError(ValueError):
    def __init__(self, code, message, **details):
        self.code = code
        self.details = details
        super().__init__(message)

def _rows(rows):
    return [{ALIASES.get(str(k).strip().lower().replace(' ','_').replace('-','_'),str(k).strip().lower()):v for k,v in row.items()} for row in rows]

def _faculty_display_name(faculty):
    linked=f'{faculty.user.first_name} {faculty.user.last_name}'.strip() if faculty.user_id else ''
    return linked or faculty.name or faculty.initials or faculty.employee_code or ''


def _practical_block_errors(grouped, slot_by_template):
    """Validate practical sessions after row-level parsing has grouped team teaching."""
    practical_identities=[identity for identity in grouped if identity[6]=='PRACTICAL']
    offerings=CourseOffering.objects.in_bulk({identity[1] for identity in practical_identities})
    slots={slot.pk:slot for template_slots in slot_by_template.values() for slot in template_slots}
    sessions=defaultdict(list)
    for identity in practical_identities:
        section_id,offering_id,day,slot_id,room_id,block,_,_=identity
        offering=offerings[offering_id]
        if offering.required_block_size<=1 or offering.allow_remainder_period:
            continue
        faculty_ids=tuple(sorted(str(faculty.pk) for _,faculty,_ in grouped[identity]))
        start=slots[slot_id]
        occupied=tuple(range(start.order,start.order+block))
        sessions[(offering_id,day,room_id,faculty_ids)].append((identity,occupied))
    bad={}
    for (offering_id,day,room_id,_faculty_ids),items in sessions.items():
        offering=offerings[offering_id]
        by_order={order:identity for identity,orders in items for order in orders}
        runs=[]
        for order in sorted(by_order):
            if not runs or order!=runs[-1][-1]+1:runs.append([order])
            else:runs[-1].append(order)
        for run in runs:
            if len(run)%offering.required_block_size:
                for identity in {by_order[order] for order in run}:
                    bad[identity]=(offering.required_block_size,len(run),day,room_id)
    return bad

def parse(version, rows):
    started=time.monotonic(); logger.info('Timetable preview started')
    rows=_rows(rows); result={'total':len(rows),'valid':0,'invalid':0,'warnings':0,'rows':[],'errors':[]}; grouped={}; seen=set()
    imported_periods=defaultdict(set)
    for source_row in rows:
        if not str(source_row.get('entry_type','') or '').strip():
            key=(str(source_row.get('section','') or '').strip().casefold(),str(source_row.get('course_code','') or '').strip().casefold())
            imported_periods[key].add((str(source_row.get('day','') or '').strip().casefold(),str(source_row.get('time_slot','') or '').strip().casefold()))
    semester=version.timetable.semester
    sections={x.name.casefold():x for x in Section.objects.filter(semester=semester).select_related('program','semester__session').prefetch_related('period_delivery_policies')}
    offerings=defaultdict(list)
    for offering_record in CourseOffering.objects.filter(semester=semester,active=True).select_related('course').prefetch_related('faculties__faculty'):
        offerings[(offering_record.section_id,offering_record.course.code.casefold())].append(offering_record)
    faculties={x.employee_code.casefold():x for x in Faculty.objects.all() if x.employee_code}
    slots={x.label.casefold():x for x in TimeSlot.objects.filter(is_break=False)}
    break_slots={x.label.casefold() for x in TimeSlot.objects.filter(is_break=True)}
    from common.course_offering_import import canonical_room_code
    rooms=defaultdict(list)
    for room_record in Room.objects.select_related('reserved_program').all():
        rooms[canonical_room_code(room_record.code)].append(room_record)
    all_slots=list(TimeSlot.objects.all()); slot_by_template={}
    for item in all_slots: slot_by_template.setdefault(item.template_id,[]).append(item)
    for values in slot_by_template.values(): values.sort(key=lambda item:item.order)
    section_busy=set(); faculty_busy=set(); room_busy=set(); imported_section_busy={}; imported_room_busy={}; imported_section_rows={}; imported_room_rows={}; imported_faculty_rows={}
    for entry in ScheduleEntry.objects.filter(version=version).select_related('start_slot','room').prefetch_related('faculty_assignments'):
        sequence=slot_by_template.get(entry.start_slot.template_id,[])
        window=[item for item in sequence if entry.start_slot.order <= item.order < entry.start_slot.order + entry.block_length]
        for item in window:
            section_busy.add((entry.section_id,entry.weekday,item.pk));
            if entry.room_id: room_busy.add((entry.room_id,entry.weekday,item.pk))
            for assignment in entry.faculty_assignments.all(): faculty_busy.add((assignment.faculty_id,entry.weekday,item.pk))
    logger.info('Workbook parsed: rows=%s',len(rows)); logger.info('Reference data loaded sections=%s offerings=%s faculty=%s rooms=%s slots=%s existing_entries=%s',len(sections),len(offerings),len(faculties),len(rooms),len(slots),ScheduleEntry.objects.filter(version=version).count())
    required={'section','course_code','employee_code','day','time_slot','room'}
    missing=required-set(rows[0]) if rows else required
    if missing: result['invalid']=1; result['errors']=[{'row':1,'message':'Missing required columns: '+', '.join(sorted(missing))}]; return result,[]
    for number,row in enumerate(rows,2):
        try:
            section_name=str(row.get('section','') or '').strip(); course_code=str(row.get('course_code','') or '').strip(); employee=str(row.get('employee_code','') or '').strip(); room_code=str(row.get('room','') or '').strip(); day=str(row.get('day','') or '').strip(); label=str(row.get('time_slot','') or '').strip(); imported_entry_type=str(row.get('entry_type','') or '').strip().upper()
            day_value=int(day) if day.isdigit() else DAYS.get(day.lower(),-1)
            if day_value in WEEKEND_DAYS:
                raise TimetableImportError(WEEKEND_ERROR_CODE, f'{day or "Unknown day"}: Saturday and Sunday are configured as off days.')
            if day_value not in WORKING_DAYS: raise ValueError(f'Invalid day "{day}".')
            section=sections.get(section_name.casefold())
            if not section: raise ValueError(f'Section "{section_name}" not found in this timetable scope.')
            offering_candidates=offerings.get((section.pk,course_code.casefold()),[])
            if not offering_candidates:
                from academics.models import Course
                if not Course.objects.filter(code__iexact=course_code).exists():
                    raise TimetableImportError('COURSE_NOT_FOUND', f'Course Code {course_code} was not found.')
                raise ValueError(f'No Course Offering found for {course_code} / {section_name}.')
            explicit_online=room_code.casefold() in ONLINE_ROOM_VALUES
            room_matches=[] if explicit_online or not room_code else rooms.get(canonical_room_code(room_code),[])
            if len(room_matches)>1:
                raise TimetableImportError('AMBIGUOUS_ROOM_CODE',f'Room "{room_code}" matches multiple canonical Room records.')
            room=None if explicit_online or not room_matches else room_matches[0]
            if imported_entry_type:
                matching_offerings=[candidate for candidate in offering_candidates if candidate.default_class_type==imported_entry_type]
                if len(matching_offerings)==1:
                    offering=matching_offerings[0]
                elif len(matching_offerings)>1:
                    raise TimetableImportError('AMBIGUOUS_COURSE_OFFERING',f'Multiple {imported_entry_type} Course Offerings found for {course_code} / {section_name}.')
                elif len(offering_candidates)==1:
                    offering=offering_candidates[0]
                else:
                    available=', '.join(sorted({candidate.default_class_type for candidate in offering_candidates}))
                    raise TimetableImportError('COURSE_OFFERING_ACTIVITY_MISMATCH',f'No {imported_entry_type} Course Offering found for {course_code} / {section_name}. Available activity types: {available}.')
            elif len(offering_candidates)==1:
                offering=offering_candidates[0]
            else:
                preferred=[candidate for candidate in offering_candidates if room and candidate.preferred_room_id==room.pk]
                if len(preferred)==1:
                    offering=preferred[0]
                else:
                    from rooms.services.eligibility import required_course_room_code, room_section_error
                    eligible=[candidate for candidate in offering_candidates if room and not room_section_error(
                        room,section,candidate,required_course_room_code(candidate))]
                    if len(eligible)==1:
                        offering=eligible[0]
                    else:
                        imported_count=len(imported_periods[(section_name.casefold(),course_code.casefold())])
                        period_matches=[candidate for candidate in eligible if candidate.weekly_periods==imported_count]
                        if len(period_matches)==1:
                            offering=period_matches[0]
                        else:
                            raise TimetableImportError('AMBIGUOUS_COURSE_OFFERING',f'Room {room_code or "(online)"} and {imported_count} imported weekly periods do not uniquely identify a Course Offering for {course_code} / {section_name}; provide Entry Type.')
            entry_type=imported_entry_type or offering.default_class_type
            course_credit=offering.course.credit
            imported_credit=row.get('credit')
            if imported_credit not in (None, ''):
                try:
                    credit_value=Decimal(str(imported_credit).strip())
                    expected_credit=Decimal(str(course_credit))
                    if not credit_value.is_finite() or credit_value < 0: raise InvalidOperation
                except (InvalidOperation, ValueError):
                    raise TimetableImportError('INVALID_CREDIT', 'Credit must be a valid non-negative numeric value.')
                if credit_value != expected_credit:
                    raise TimetableImportError('CREDIT_MISMATCH', f'Credit mismatch for {course_code}. Imported {imported_credit}; expected {course_credit}.')
            faculty=faculties.get(employee.casefold())
            if not faculty: raise TimetableImportError('FACULTY_NOT_FOUND', f'Faculty with Employee Code {employee} was not found.')
            faculty_name=str(row.get('faculty_name','') or '').strip()
            canonical_name=_faculty_display_name(faculty)
            match_kind=faculty_name_match_kind(faculty_name, canonical_name) if faculty_name else 'EXACT'
            if match_kind=='MISMATCH':
                raise TimetableImportError('FACULTY_NAME_MISMATCH', f'Faculty Name "{faculty_name}" does not match Employee Code {employee} ({canonical_name}).')
            name_warning=(f'Faculty name "{faculty_name}" matched canonical faculty "{canonical_name}" by Employee Code {employee}.') if match_kind=='ABBREVIATED' else None
            if not any(x.faculty_id==faculty.pk for x in offering.faculties.all()): raise ValueError(f'{employee} is not assigned to {course_code} / {section_name}.')
            slot=slots.get(label.casefold())
            if not slot:
                if label.casefold() in break_slots: raise ValueError(f'{label} is a break slot.')
                raise ValueError(f'Time slot "{label}" not found.')
            from academics.delivery import get_section_delivery_policy
            section_policy,offline_weekday=get_section_delivery_policy(section,semester)
            # Imported Year-4 timetables encode actual delivery in the Room
            # column. The configured hybrid weekday is a scheduling policy,
            # not evidence that this particular imported class has a room.
            online=(not room_code or explicit_online) if section.year==4 else (
                explicit_online or (section_policy=='HYBRID' and day_value!=offline_weekday)
            )
            # ONLINE/VIRTUAL are delivery markers, never physical room codes.
            from rooms.services.eligibility import required_course_room_code
            required_room_code=required_course_room_code(offering)
            if required_room_code and not online and room_code != required_room_code:
                raise TimetableImportError('REQUIRED_ROOM_MISMATCH','Quantum Physics and Advanced Functional Materials Lab must use Room 105.')
            if not online and not room: raise ValueError(f'Room "{room_code}" not found.')
            if online and room:raise TimetableImportError('ONLINE_ROOM_NOT_ALLOWED','Online classes must not reserve a physical room.')
            if room:
                if not room.active:raise TimetableImportError('ROOM_INACTIVE',f'Room {room.code} is inactive.')
                from rooms.services.eligibility import room_section_error
                eligibility_error=room_section_error(room,section,offering,required_room_code)
                if eligibility_error:raise TimetableImportError(eligibility_error['code'],eligibility_error['message'])
            block=int(float(str(row.get('block_length',1) or 1)))
            if block<1: raise ValueError('Block Length must be at least 1.')
            sequence=slot_by_template.get(slot.template_id,[]); window=[item for item in sequence if slot.order <= item.order < slot.order + block]
            if len(window)!=block or any(item.is_break for item in window): raise ValueError('The requested block crosses a break or exceeds the timetable template.')
            delivery_mode='ONLINE' if online else 'OFFLINE'
            identity=(section.pk,offering.pk,day_value,slot.pk,room.pk if room else None,block,entry_type,delivery_mode)
            assignment_key=identity+(faculty.pk,)
            if assignment_key in seen: result['warnings']+=1; result['rows'].append({'row':number,'status':'SKIPPED','message':'Duplicate spreadsheet row.'}); continue
            for item in window:
                section_key=(section.pk,day_value,item.pk); room_key=(room.pk,day_value,item.pk) if room else None
                if section_key in section_busy and imported_section_busy.get(section_key)!=identity:
                    raise TimetableImportError('SECTION_CLASH','Section is already occupied in this period.',conflicting_row=imported_section_rows.get(section_key))
                faculty_key=(faculty.pk,day_value,item.pk)
                if faculty_key in faculty_busy:
                    raise TimetableImportError('FACULTY_CLASH','Faculty is already occupied in this period.',conflicting_row=imported_faculty_rows.get(faculty_key))
                if room_key and room_key in room_busy and imported_room_busy.get(room_key)!=identity:
                    raise TimetableImportError('ROOM_CLASH','Room is already occupied in this period.',conflicting_row=imported_room_rows.get(room_key))
            seen.add(assignment_key); grouped.setdefault(identity,[]).append((number,faculty,row.get('faculty_role','PRIMARY') or 'PRIMARY'))
            for item in window:
                section_key=(section.pk,day_value,item.pk);section_busy.add(section_key);imported_section_busy[section_key]=identity;imported_section_rows.setdefault(section_key,number)
                faculty_key=(faculty.pk,day_value,item.pk);faculty_busy.add(faculty_key);imported_faculty_rows.setdefault(faculty_key,number)
                if room:
                    room_key=(room.pk,day_value,item.pk);room_busy.add(room_key);imported_room_busy[room_key]=identity;imported_room_rows.setdefault(room_key,number)
            valid_row={'row':number,'status':'VALID','section':section_name,'course_code':course_code,'credit':str(course_credit),'faculty_name':canonical_name,'employee_code':employee,'day':day,'time_slot':label,'room':None if online else room_code,'delivery_mode':delivery_mode}
            if name_warning:
                valid_row['message']=name_warning
                result['warnings']+=1
            result['rows'].append(valid_row)
        except Exception as exc:
            result['invalid']+=1
            error={'row':number,'message':str(exc)}
            if isinstance(exc,TimetableImportError):
                error['code']=exc.code
                error.update(exc.details)
            result['errors'].append(error)
            result['rows'].append({'row':number,'status':'INVALID','section':str(row.get('section','') or ''),'course_code':str(row.get('course_code','') or ''),'credit':str(row.get('credit','') or ''),'faculty_name':str(row.get('faculty_name','') or ''),'employee_code':str(row.get('employee_code','') or ''),'day':str(row.get('day','') or ''),'time_slot':str(row.get('time_slot','') or ''),'room':str(row.get('room','') or ''),'message':str(exc),'code':getattr(exc,'code',None)})
    bad_blocks=_practical_block_errors(grouped,slot_by_template)
    if bad_blocks:
        bad_rows={}
        for identity,(required_size,actual_size,_day,_room_id) in bad_blocks.items():
            for row_number,_faculty,_role in grouped.pop(identity):
                bad_rows[row_number]=(required_size,actual_size)
        for item in result['rows']:
            if item.get('row') in bad_rows and item.get('status')=='VALID':
                required_size,actual_size=bad_rows[item['row']]
                item['status']='INVALID';item['code']='PRACTICAL_BLOCK_INCOMPLETE'
                item['message']=f'Practical session has {actual_size} contiguous period(s); required block size is {required_size}. Keep the same offering, faculty, and room for the complete block.'
                result['errors'].append({'row':item['row'],'code':item['code'],'message':item['message']})
    result['valid']=len([x for x in result['rows'] if x.get('status')=='VALID']);result['invalid']=len([x for x in result['rows'] if x.get('status')=='INVALID']); logger.info('Validation finished in %.2fs',time.monotonic()-started); return result,grouped

def commit(version, grouped):
    if any(identity[2] not in WORKING_DAYS for identity in grouped):
        raise TimetableImportError(WEEKEND_ERROR_CODE, WEEKEND_ERROR_MESSAGE)
    created=0; assignments=0
    from academics.models import Section
    sections=Section.objects.filter(pk__in={identity[0] for identity in grouped}).in_bulk()
    for identity, faculty_rows in grouped.items():
        section_id,offering_id,day,slot_id,room_id,block,entry_type,delivery_mode=identity
        if day not in WORKING_DAYS:
            raise TimetableImportError(WEEKEND_ERROR_CODE, WEEKEND_ERROR_MESSAGE)
        section=sections[section_id]
        from academics.delivery import YEAR4_IMPORT_ROOM_DELIVERY_NOTE
        entry=ScheduleEntry.objects.create(version=version,section_id=section_id,course_offering_id=offering_id,weekday=day,start_slot_id=slot_id,room_id=room_id,block_length=block,entry_type=entry_type,delivery_mode=delivery_mode,note=YEAR4_IMPORT_ROOM_DELIVERY_NOTE if section.year==4 else '')
        for _,faculty,role in faculty_rows: ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=faculty,role=role); assignments+=1
        created+=1
    return {'entries_created':created,'faculty_assignments_created':assignments,'skipped':0,'failed':0,'warnings':0,'errors':[]}

def template():
    workbook=Workbook(); sheet=workbook.active; sheet.append(['Section','Course Code','Credit','Employee Code','Faculty Name','Day','Time Slot','Room No.']); sheet.append(['CS 1A','CS101',4,'FAC001','Aarav Sharma','Monday','09:00-10:00','401']); stream=BytesIO(); workbook.save(stream); return stream.getvalue()
