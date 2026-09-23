from io import BytesIO
from openpyxl import Workbook
from academics.models import CourseOffering, Section
from common.models import TimeSlot
from faculty.models import Faculty
from rooms.models import Room
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty, TimetableVersion
from scheduling.services.validation import validate_entry

ALIASES={'section':'section','section_name':'section','course_code':'course_code','course':'course_code','employee_code':'employee_code','faculty_code':'employee_code','day':'day','weekday':'day','time_slot':'time_slot','slot':'time_slot','time':'time_slot','room_no':'room','room_no.':'room','room_number':'room','room_code':'room','room':'room','faculty_role':'faculty_role','block_length':'block_length','entry_type':'entry_type'}
DAYS={x.lower():i for i,x in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])}

def _rows(rows):
    return [{ALIASES.get(str(k).strip().lower().replace(' ','_').replace('-','_'),str(k).strip().lower()):v for k,v in row.items()} for row in rows]

def parse(version, rows):
    rows=_rows(rows); result={'total':len(rows),'valid':0,'invalid':0,'warnings':0,'rows':[],'errors':[]}; grouped={}; seen=set()
    required={'section','course_code','employee_code','day','time_slot','room'}
    missing=required-set(rows[0]) if rows else required
    if missing: result['invalid']=1; result['errors']=[{'row':1,'message':'Missing required columns: '+', '.join(sorted(missing))}]; return result,[]
    for number,row in enumerate(rows,2):
        try:
            section_name=str(row.get('section','') or '').strip(); course_code=str(row.get('course_code','') or '').strip(); employee=str(row.get('employee_code','') or '').strip(); room_code=str(row.get('room','') or '').strip(); day=str(row.get('day','') or '').strip(); label=str(row.get('time_slot','') or '').strip()
            day_value=int(day) if day.isdigit() else DAYS.get(day.lower(),-1)
            section=Section.objects.filter(semester=version.timetable.semester,name__iexact=section_name).select_related('program').first()
            if not section: raise ValueError(f'Section "{section_name}" not found in this timetable scope.')
            offering=CourseOffering.objects.filter(section=section,semester=version.timetable.semester,course__code__iexact=course_code).select_related('course').first()
            if not offering: raise ValueError(f'No Course Offering found for {course_code} / {section_name}.')
            faculty=Faculty.objects.filter(employee_code__iexact=employee).first()
            if not faculty: raise ValueError(f'Faculty "{employee}" not found.')
            if not offering.faculties.filter(faculty=faculty).exists(): raise ValueError(f'{employee} is not assigned to {course_code} / {section_name}.')
            if day_value not in range(7) or day_value>5: raise ValueError(f'Invalid day "{day}".')
            slot=TimeSlot.objects.filter(label__iexact=label,is_break=False).first()
            if not slot:
                if TimeSlot.objects.filter(label__iexact=label,is_break=True).exists(): raise ValueError(f'{label} is a break slot.')
                raise ValueError(f'Time slot "{label}" not found.')
            room=Room.objects.filter(code__iexact=room_code).first()
            if not room: raise ValueError(f'Room "{room_code}" not found.')
            block=int(float(str(row.get('block_length',1) or 1))); entry_type=str(row.get('entry_type','LECTURE') or 'LECTURE').upper()
            if block<1: raise ValueError('Block Length must be at least 1.')
            conflicts=validate_entry(version,{'weekday':day_value,'start_slot':slot.pk,'block_length':block,'section':section.pk,'course_offering':offering.pk,'room':room.pk,'faculty_ids':[faculty.pk]})
            if conflicts: raise ValueError(conflicts[0]['message'])
            identity=(section.pk,offering.pk,day_value,slot.pk,room.pk,block,entry_type)
            assignment_key=identity+(faculty.pk,)
            if assignment_key in seen: result['warnings']+=1; result['rows'].append({'row':number,'status':'SKIPPED','message':'Duplicate spreadsheet row.'}); continue
            seen.add(assignment_key); grouped.setdefault(identity,[]).append((number,faculty,row.get('faculty_role','PRIMARY') or 'PRIMARY'))
            result['rows'].append({'row':number,'status':'VALID','section':section_name,'course_code':course_code,'employee_code':employee,'day':day,'time_slot':label,'room':room_code})
        except Exception as exc: result['invalid']+=1; result['errors'].append({'row':number,'message':str(exc)})
    result['valid']=len([x for x in result['rows'] if x.get('status')=='VALID']); return result,grouped

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
