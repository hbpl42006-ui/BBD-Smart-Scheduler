import csv, io
from django.db import transaction
from accounts.models import User, Role
from institutions.models import Department, Program
from academics.models import Semester, Section, Course, CourseOffering
from faculty.models import Faculty
from rooms.models import Room

SPECS={'rooms':['code','building','floor','capacity','room_type','active'],'courses':['code','name','short_code','credit','active'],'faculty':['email','first_name','last_name','employee_code','initials','department_code','max_daily_periods','max_weekly_periods','active'],'sections':['program_code','semester','year','name','student_strength','coordinator_email','active'],'course-offerings':['semester','section','course_code','weekly_periods','default_class_type','required_block_size','room_type_requirement','preferred_room_code','active']}
def rows_from_upload(upload):
    raw=upload.read()
    if upload.name.lower().endswith('.xlsx'):
        from openpyxl import load_workbook
        values=list(load_workbook(io.BytesIO(raw),read_only=True,data_only=True).active.values)
        return [dict(zip(values[0],r)) for r in values[1:] if any(v is not None for v in r)]
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
def validate(kind, rows):
    errors=[]; valid=[]; seen=set()
    for n, source in enumerate(rows,2):
        if kind=='rooms':
            headers={'room no.':'code','room no':'code','code':'code','building':'building','floor':'floor','capacity':'capacity','room type':'room_type','room_type':'room_type','active':'active'}
            source={headers.get(str(key).strip().lower(),str(key).strip()): value for key,value in source.items()}
        row={str(k).strip():('' if v is None else str(v).strip()) for k,v in source.items()}
        missing=[f for f in SPECS[kind] if f not in row]
        if missing:
            errors.extend({'row':n,'field':f,'message':'Missing required column'} for f in missing); continue
        try:
            if kind=='rooms':
                if not row['code']: raise ValueError('Room No. is required')
                room_types={label.lower():value for value,label in Room.RoomType.choices}; normalized=room_types.get(row['room_type'].lower()) or (row['room_type'].upper() if row['room_type'].upper() in dict(Room.RoomType.choices) else None)
                if not normalized: raise ValueError('Invalid room type')
                row['room_type']=normalized
                if row['code'] in seen or Room.objects.filter(code=row['code']).exists(): row['_skip']='Room No. already exists'
                seen.add(row['code'])
                if int(row['capacity'])<=0: raise ValueError('Capacity must be positive')
            elif kind=='courses' and Course.objects.filter(code=row['code']).exists(): raise ValueError('Course code already exists')
            elif kind=='faculty': row['_department_id']=str(Department.objects.get(code=row['department_code']).pk)
            elif kind=='sections': row['_program_id']=str(Program.objects.get(code=row['program_code']).pk); row['_semester_id']=str(Semester.objects.get(name=row['semester']).pk); int(row['student_strength'])
            elif kind=='course-offerings':
                row['_course_id']=str(Course.objects.get(code=row['course_code']).pk); row['_semester_id']=str(Semester.objects.get(name=row['semester']).pk); row['_section_id']=str(Section.objects.get(name=row['section'],semester_id=row['_semester_id']).pk)
        except Exception as exc: errors.append({'row':n,'field':'data','message':str(exc)})
        else: valid.append(row)
    return valid,errors
@transaction.atomic
def commit(kind, rows):
    valid,errors=validate(kind,rows)
    if errors: raise ValueError(errors)
    for row in valid:
        if kind=='rooms':
            if row.get('_skip'): continue
            Room.objects.create(code=row['code'],building=row['building'],floor=row['floor'],capacity=int(row['capacity']),room_type=row['room_type'],active=row['active'].lower()!='false')
        elif kind=='courses': Course.objects.create(code=row['code'],name=row['name'],short_code=row['short_code'],credit=row['credit'])
        elif kind=='faculty':
            user=User.objects.create_user(row['email'],'ChangeMe123!',first_name=row['first_name'],last_name=row['last_name'],role=Role.FACULTY); Faculty.objects.create(user=user,employee_code=row['employee_code'],initials=row['initials'],department_id=row['_department_id'],max_daily_periods=int(row['max_daily_periods']),max_weekly_periods=int(row['max_weekly_periods']))
        elif kind=='sections': Section.objects.create(program_id=row['_program_id'],semester_id=row['_semester_id'],year=int(row['year']),name=row['name'],student_strength=int(row['student_strength']))
        elif kind=='course-offerings': CourseOffering.objects.create(course_id=row['_course_id'],semester_id=row['_semester_id'],section_id=row['_section_id'],weekly_periods=int(row['weekly_periods']),default_class_type=row['default_class_type'],required_block_size=int(row['required_block_size']),room_type_requirement=row['room_type_requirement'])
    return {'created':len([row for row in valid if not row.get('_skip')]),'updated':0,'skipped':len([row for row in valid if row.get('_skip')])}
