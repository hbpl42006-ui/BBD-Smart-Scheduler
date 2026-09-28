from io import BytesIO
from openpyxl import Workbook
from academics.models import CourseOffering, Section
from common.models import TimeSlot
from faculty.models import Faculty
from rooms.models import Room
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
import logging
import time

logger = logging.getLogger(__name__)

ALIASES={'section':'section','section_name':'section','course_code':'course_code','course':'course_code','employee_code':'employee_code','faculty_code':'employee_code','day':'day','weekday':'day','time_slot':'time_slot','slot':'time_slot','time':'time_slot','room_no':'room','room_no.':'room','room_number':'room','room_code':'room','room':'room','faculty_role':'faculty_role','block_length':'block_length','entry_type':'entry_type'}
DAYS={x.lower():i for i,x in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])}

def _rows(rows):
    return [{ALIASES.get(str(k).strip().lower().replace(' ','_').replace('-','_'),str(k).strip().lower()):v for k,v in row.items()} for row in rows]

def parse(version, rows):
    started=time.monotonic(); logger.info('Timetable preview started')
    rows=_rows(rows); result={'total':len(rows),'valid':0,'invalid':0,'warnings':0,'rows':[],'errors':[]}; grouped={}; seen=set()
    semester=version.timetable.semester
    sections={x.name.casefold():x for x in Section.objects.filter(semester=semester).select_related('program')}
    offerings={(x.section_id,x.course.code.casefold()):x for x in CourseOffering.objects.filter(semester=semester).select_related('course').prefetch_related('faculties__faculty')}
    faculties={x.employee_code.casefold():x for x in Faculty.objects.all() if x.employee_code}
    slots={x.label.casefold():x for x in TimeSlot.objects.filter(is_break=False)}
    break_slots={x.label.casefold() for x in TimeSlot.objects.filter(is_break=True)}
    rooms={x.code.casefold():x for x in Room.objects.all()}
    all_slots=list(TimeSlot.objects.all()); slot_by_template={}
    for item in all_slots: slot_by_template.setdefault(item.template_id,[]).append(item)
    for values in slot_by_template.values(): values.sort(key=lambda item:item.order)
    section_busy=set(); faculty_busy=set(); room_busy=set()
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
            section_name=str(row.get('section','') or '').strip(); course_code=str(row.get('course_code','') or '').strip(); employee=str(row.get('employee_code','') or '').strip(); room_code=str(row.get('room','') or '').strip(); day=str(row.get('day','') or '').strip(); label=str(row.get('time_slot','') or '').strip()
            day_value=int(day) if day.isdigit() else DAYS.get(day.lower(),-1)
            section=sections.get(section_name.casefold())
            if not section: raise ValueError(f'Section "{section_name}" not found in this timetable scope.')
            offering=offerings.get((section.pk,course_code.casefold()))
            if not offering: raise ValueError(f'No Course Offering found for {course_code} / {section_name}.')
            faculty=faculties.get(employee.casefold())
            if not faculty: raise ValueError(f'Faculty "{employee}" not found.')
            if not any(x.faculty_id==faculty.pk for x in offering.faculties.all()): raise ValueError(f'{employee} is not assigned to {course_code} / {section_name}.')
            if day_value not in range(7) or day_value>5: raise ValueError(f'Invalid day "{day}".')
            slot=slots.get(label.casefold())
            if not slot:
                if label.casefold() in break_slots: raise ValueError(f'{label} is a break slot.')
                raise ValueError(f'Time slot "{label}" not found.')
            room=rooms.get(room_code.casefold())
            if not room: raise ValueError(f'Room "{room_code}" not found.')
            block=int(float(str(row.get('block_length',1) or 1))); entry_type=str(row.get('entry_type','LECTURE') or 'LECTURE').upper()
            if block<1: raise ValueError('Block Length must be at least 1.')
            sequence=slot_by_template.get(slot.template_id,[]); window=[item for item in sequence if slot.order <= item.order < slot.order + block]
            if len(window)!=block or any(item.is_break for item in window): raise ValueError('The requested block crosses a break or exceeds the timetable template.')
            for item in window:
                if (section.pk,day_value,item.pk) in section_busy: raise ValueError('Section is already occupied in this period.')
                if (faculty.pk,day_value,item.pk) in faculty_busy: raise ValueError('Faculty is already occupied in this period.')
                if (room.pk,day_value,item.pk) in room_busy: raise ValueError('Room is already occupied in this period.')
            identity=(section.pk,offering.pk,day_value,slot.pk,room.pk,block,entry_type)
            assignment_key=identity+(faculty.pk,)
            if assignment_key in seen: result['warnings']+=1; result['rows'].append({'row':number,'status':'SKIPPED','message':'Duplicate spreadsheet row.'}); continue
            seen.add(assignment_key); grouped.setdefault(identity,[]).append((number,faculty,row.get('faculty_role','PRIMARY') or 'PRIMARY'))
            for item in window:
                section_busy.add((section.pk,day_value,item.pk)); faculty_busy.add((faculty.pk,day_value,item.pk)); room_busy.add((room.pk,day_value,item.pk))
            result['rows'].append({'row':number,'status':'VALID','section':section_name,'course_code':course_code,'employee_code':employee,'day':day,'time_slot':label,'room':room_code})
        except Exception as exc: result['invalid']+=1; result['errors'].append({'row':number,'message':str(exc)})
    result['valid']=len([x for x in result['rows'] if x.get('status')=='VALID']); logger.info('Validation finished in %.2fs',time.monotonic()-started); return result,grouped

def commit(version, grouped):
    created=0; assignments=0
    for identity, faculty_rows in grouped.items():
        section_id,offering_id,day,slot_id,room_id,block,entry_type=identity
        entry=ScheduleEntry.objects.create(version=version,section_id=section_id,course_offering_id=offering_id,weekday=day,start_slot_id=slot_id,room_id=room_id,block_length=block,entry_type=entry_type)
        for _,faculty,role in faculty_rows: ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=faculty,role=role); assignments+=1
        created+=1
    return {'entries_created':created,'faculty_assignments_created':assignments,'skipped':0,'failed':0,'warnings':0,'errors':[]}

def template():
    workbook=Workbook(); sheet=workbook.active; sheet.append(['Section','Course Code','Employee Code','Day','Time Slot','Room No.']); sheet.append(['CS 1A','CS101','FAC001','Monday','09:00-10:00','401']); stream=BytesIO(); workbook.save(stream); return stream.getvalue()
