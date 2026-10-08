import csv, io
from django.db import transaction
from institutions.models import Department, Program
from academics.models import Semester, Section, Course, CourseOffering
from faculty.models import Faculty
from rooms.models import Room
from django.core.exceptions import ValidationError

SPECS={'rooms':['code','building','floor','capacity','room_type','active'],'courses':['code','name','short_code','credit','active'],'faculty':['email','first_name','last_name','employee_code','initials','department_code','max_daily_periods','max_weekly_periods','active'],'sections':['program_code','semester','year','name','student_strength','coordinator_email','active'],'course-offerings':['semester','section','course_code','weekly_periods','default_class_type','required_block_size','room_type_requirement','preferred_room_code','active']}

class InvalidRoomType(ValueError):
    pass

def _room_type_key(value):
    return ''.join(character for character in str(value).casefold() if character.isalnum())

def _normalize_room_type(value):
    supported={_room_type_key(choice): choice for choice, _label in Room.RoomType.choices}
    supported.update({_room_type_key(label): choice for choice, label in Room.RoomType.choices})
    normalized=supported.get(_room_type_key(value))
    if not normalized:
        raise InvalidRoomType(f'Unsupported room type: {value}')
    return normalized

def rows_from_upload(upload):
    raw=upload.read()
    if upload.name.lower().endswith('.xlsx'):
        from openpyxl import load_workbook
        workbook=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
        for sheet in workbook.worksheets:
            values=list(sheet.values)
            if values and any(v is not None and str(v).strip() for v in values[0]):
                headers=[str(v).strip() if v is not None else '' for v in values[0]]
                return [dict(zip(headers,r)) for r in values[1:] if any(v is not None and str(v).strip() for v in r)]
        return []
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
def _normalize_course_rows(rows):
    aliases={'code':'code','course code':'code','course_code':'code','name':'name','course name':'name','course_name':'name','short code':'short_code','short code':'short_code','short_code':'short_code','credit':'credit','active':'active'}
    return [{aliases.get(str(k).strip().lower(),str(k).strip().lower().replace(' ','_')):v for k,v in row.items()} for row in rows]
def validate(kind, rows, mode='CREATE_ONLY'):
    if kind=='courses': rows=_normalize_course_rows(rows)
    errors=[]; valid=[]; seen=set()
    for n, source in enumerate(rows,2):
        if kind=='rooms':
            headers={'room no.':'code','room no':'code','code':'code','building':'building','floor':'floor','capacity':'capacity','room type':'room_type','room_type':'room_type','projector':'has_projector','projector available':'has_projector','active':'active','allowed year':'allowed_year','allowed_year':'allowed_year'}
            source={headers.get(str(key).strip().lower(),str(key).strip()): value for key,value in source.items()}
        row={str(k).strip():('' if v is None else str(v).strip()) for k,v in source.items()}
        missing=[f for f in SPECS[kind] if f not in row]
        if missing:
            errors.extend({'row':n,'field':f,'message':'Missing required column'} for f in missing); continue
        try:
            if kind=='rooms':
                if not row['code']: raise ValueError('Room No. is required')
                row['room_type']=_normalize_room_type(row['room_type'])
                allowed_year=row.get('allowed_year','').strip().upper()
                if allowed_year in ('','ALL'):
                    row['allowed_year']=None
                elif allowed_year not in ('1','2','3','4'):
                    raise ValueError('Allowed Year must be ALL or 1, 2, 3, or 4.')
                else:
                    row['allowed_year']=int(allowed_year)
                if row['code'] in seen or Room.objects.filter(code=row['code']).exists(): row['_skip']='Room No. already exists'
                seen.add(row['code'])
                if int(row['capacity'])<=0: raise ValueError('Capacity must be positive')
                if 'has_projector' in row:
                    projector={'yes':True,'true':True,'1':True,'no':False,'false':False,'0':False}.get(row['has_projector'].lower())
                    if projector is None: raise ValueError('Projector must be Yes, No, TRUE, FALSE, 1, or 0')
                    row['has_projector']=projector
            elif kind=='courses':
                row['code']=row['code'].strip().upper()
                if row['code'] in seen: raise ValueError(f'Duplicate course code {row["code"]} in uploaded file.')
                seen.add(row['code'])
                active_value={'true':True,'yes':True,'1':True,'false':False,'no':False,'0':False}.get(row['active'].lower())
                if active_value is None: raise ValueError('Active must be TRUE, FALSE, Yes, No, 1, or 0.')
                row['active']=active_value
                if mode=='CREATE_ONLY' and Course.objects.filter(code__iexact=row['code']).exists(): row['_skip']='Course code already exists'
                elif mode in ('UPSERT','REPLACE') and Course.objects.filter(code__iexact=row['code']).exists(): row['_update_id']=str(Course.objects.get(code__iexact=row['code']).pk)
            elif kind=='faculty': row['_department_id']=str(Department.objects.get(code=row['department_code']).pk)
            elif kind=='sections': row['_program_id']=str(Program.objects.get(code=row['program_code']).pk); row['_semester_id']=str(Semester.objects.get(name=row['semester']).pk); int(row['student_strength'])
            elif kind=='course-offerings':
                row['_course_id']=str(Course.objects.get(code=row['course_code']).pk); row['_semester_id']=str(Semester.objects.get(name=row['semester']).pk); row['_section_id']=str(Section.objects.get(name=row['section'],semester_id=row['_semester_id']).pk)
        except Exception as exc:
            error={'row':n,'field':'room_type' if isinstance(exc,InvalidRoomType) else 'data','message':str(exc)}
            if isinstance(exc,InvalidRoomType):error['code']='INVALID_ROOM_TYPE'
            errors.append(error)
        else: valid.append(row)
    return valid,errors
@transaction.atomic
def commit(kind, rows, mode='CREATE_ONLY'):
    valid,errors=validate(kind,rows,mode)
    if errors: raise ValueError(errors)
    for row in valid:
        if kind=='rooms':
            if row.get('_skip'): continue
            Room.objects.create(code=row['code'],building=row['building'],floor=row['floor'],capacity=int(row['capacity']),room_type=row['room_type'],active=row['active'].lower()!='false',allowed_year=row.get('allowed_year'),**({'has_projector':row['has_projector']} if 'has_projector' in row else {}))
        elif kind=='courses':
            values={'name':row['name'],'short_code':row.get('short_code','')[:20],'credit':row['credit'],'active':True if mode=='REPLACE' else row['active']}
            if row.get('_update_id'): Course.objects.filter(pk=row['_update_id']).update(**values)
            elif not row.get('_skip'): Course.objects.create(code=row['code'],**values)
        elif kind=='faculty':
            name=f"{row['first_name']} {row['last_name']}".strip()
            Faculty.objects.create(name=name,email=row['email'] or None,employee_code=row['employee_code'] or None,initials=row['initials'],department_id=row['_department_id'],max_daily_periods=int(row['max_daily_periods']),max_weekly_periods=int(row['max_weekly_periods']))
        elif kind=='sections': Section.objects.create(program_id=row['_program_id'],semester_id=row['_semester_id'],year=int(row['year']),name=row['name'],student_strength=int(row['student_strength']))
        elif kind=='course-offerings': CourseOffering.objects.create(course_id=row['_course_id'],semester_id=row['_semester_id'],section_id=row['_section_id'],weekly_periods=int(row['weekly_periods']),default_class_type=row['default_class_type'],required_block_size=int(row['required_block_size']),room_type_requirement=row['room_type_requirement'])
    archived=0
    if kind=='courses' and mode=='REPLACE':
        uploaded={row['code'] for row in valid}
        archived=Course.objects.filter(active=True).exclude(code__in=uploaded).update(active=False)
    return {'created':len([row for row in valid if not row.get('_skip')]),'updated':len([row for row in valid if row.get('_update_id')]),'skipped':len([row for row in valid if row.get('_skip')]),'archive':archived}
