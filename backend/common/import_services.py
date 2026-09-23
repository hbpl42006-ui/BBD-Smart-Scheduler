from decimal import Decimal
from django.db import transaction
from accounts.models import User
from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from faculty.models import CourseOfferingFaculty, Faculty
from faculty.models import FacultyAvailability
from common.models import TimeSlot
from institutions.models import Department, Program
import re

def value(source, aliases, required=False):
    normalized={str(k).strip().lower().replace(' ','_'):v for k,v in source.items()}
    for alias in aliases:
        key=alias.lower().replace(' ','_')
        if key in normalized: return '' if normalized[key] is None else str(normalized[key]).strip()
    if required: raise ValueError(f'{aliases[0]} is required')
    return ''

def department(value_):
    return Department.objects.filter(code__iexact=value_).first() or Department.objects.filter(name__iexact=value_).first()

def semester(value_, session=None):
    qs=Semester.objects.filter(session=session) if session else Semester.objects.all()
    return qs.filter(name__iexact=value_).first() or (qs.filter(number=int(value_)).first() if str(value_).isdigit() else None)

def faculty_import(rows, commit=True):
    result={'created':0,'skipped':0,'failed':0,'errors':[],'warnings':0,'warning_details':[]}; seen=set(); used_emails=set()
    for number,source in enumerate(rows,2):
        try:
            employee=value(source,['employee_code','Employee Code'],True)
            if employee in seen or Faculty.objects.filter(employee_code=employee).exists(): result['skipped']+=1; continue
            seen.add(employee); name=value(source,['faculty_name','name','Faculty Name'],True); dept=department(value(source,['department','Department'],True))
            if not dept: raise ValueError('Department not found.')
            load=int(value(source,['max_weekly_load','max_weekly_periods','Max Weekly Load']) or '16')
            if load<0: raise ValueError('Max weekly load must be non-negative.')
            email=value(source,['email','Email']).strip().lower(); user=None
            if email in {'-','—','–','â€”','â€“','Ã¢â‚¬â€','n/a','na','null','none'}: email=''; result['warnings']+=1; result['warning_details'].append({'row':number,'identifier':employee,'message':'Email placeholder ignored; Faculty will be imported without login.'})
            if email and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email): raise ValueError('Invalid email address.')
            if email:
                if email in used_emails: result['warnings']+=1; result['warning_details'].append({'row':number,'identifier':employee,'message':'Duplicate email in file; Faculty will be imported without login.'}); email=''
                used_emails.add(email)
                user=User.objects.filter(email__iexact=email).first()
                if user and hasattr(user,'faculty_profile'): result['warnings']+=1; result['warning_details'].append({'row':number,'identifier':employee,'message':'Email already belongs to another Faculty login; imported without login account.'}); user=None
            initials=''.join(part[0] for part in name.split()).upper()[:10] or employee[:10]
            if commit: Faculty.objects.create(user=user,name=name,employee_code=employee,initials=initials,department=dept,max_weekly_periods=load)
            result['created']+=1
        except Exception as exc: result['failed']+=1; result['errors'].append({'row':number,'identifier':source.get('Employee Code',source.get('employee_code','')),'message':str(exc)})
    return result

def course_import(rows):
    result={'created':0,'skipped':0,'failed':0,'errors':[]}; seen=set()
    for number,source in enumerate(rows,2):
        try:
            code=value(source,['course_code','code','Course Code'],True); name=value(source,['course_name','name','Course Name'],True)
            if code in seen or Course.objects.filter(code__iexact=code).exists(): result['skipped']+=1; continue
            seen.add(code); program=value(source,['program','Program'],True); semester_value=value(source,['semester','Semester'],True)
            if not (Program.objects.filter(code__iexact=program).exists() or Program.objects.filter(name__iexact=program).exists()): raise ValueError('Program not found.')
            if not semester(semester_value): raise ValueError('Semester not found.')
            hours=[]
            for aliases in (['lecture_hours','Lecture Hours'],['tutorial_hours','Tutorial Hours'],['practical_hours','Practical Hours']):
                parsed=int(value(source,aliases) or '0')
                if parsed<0: raise ValueError('Course hours must be non-negative.')
                hours.append(parsed)
            Course.objects.create(code=code,name=name,short_code=code[:20],credit=Decimal(sum(hours)),lecture_hours=hours[0],tutorial_hours=hours[1],practical_hours=hours[2]); result['created']+=1
        except Exception as exc: result['failed']+=1; result['errors'].append({'row':number,'identifier':source.get('Course Code',source.get('course_code','')),'message':str(exc)})
    return result

@transaction.atomic
def mapping_import(rows):
    result={'offerings_created':0,'faculty_assignments_created':0,'skipped':0,'failed':0,'errors':[]}; seen=set()
    for number,source in enumerate(rows,2):
        try:
            employee=value(source,['employee_code','Employee Code'],True); code=value(source,['course_code','Course Code'],True); section_name=value(source,['section','Section'],True); session_name=value(source,['session','Session'],True); semester_value=value(source,['semester','Semester'],True); role=value(source,['faculty_role','role','Faculty Role']) or 'PRIMARY'
            faculty=Faculty.objects.filter(employee_code=employee).first(); session=AcademicSession.objects.filter(name__iexact=session_name).first(); course=Course.objects.filter(code__iexact=code).first()
            if not faculty: raise ValueError('Faculty not found.')
            if not session: raise ValueError('Academic session not found.')
            if not course: raise ValueError('Course not found.')
            sem=semester(semester_value,session); section=Section.objects.filter(name__iexact=section_name,semester=sem).first() if sem else None
            if not sem: raise ValueError('Semester not found for session.')
            if not section: raise ValueError('Section not found for semester.')
            key=(str(faculty.pk),str(course.pk),str(section.pk))
            if key in seen: result['skipped']+=1; continue
            seen.add(key); offering,created=CourseOffering.objects.get_or_create(semester=sem,section=section,course=course,defaults={'weekly_periods':max(course.lecture_hours+course.tutorial_hours+course.practical_hours,1)})
            result['offerings_created']+=int(created); assignment,created=CourseOfferingFaculty.objects.get_or_create(course_offering=offering,faculty=faculty,defaults={'role':role}); result['faculty_assignments_created']+=int(created); result['skipped']+=int(not created)
        except Exception as exc: result['failed']+=1; result['errors'].append({'row':number,'identifier':source.get('Employee Code',source.get('employee_code','')),'message':str(exc)})
    return result

def section_import(rows, commit=True):
    result={'created':0,'skipped':0,'failed':0,'errors':[],'warnings':0}; seen=set()
    required={'program','semester','year','name'}
    if rows:
        aliases={'program':'program','program_name':'program','program_code':'program','semester_name':'semester','academic_year':'year','study_year':'year','section':'name','section_name':'name','student_strength':'student_strength','strength':'student_strength','capacity':'student_strength'}
        rows=[{aliases.get(str(k).strip().lower().replace('-','_'),'_unknown' if not str(k).strip() else str(k).strip().lower().replace(' ','_')):v for k,v in row.items()} for row in rows]
        missing=required-set(rows[0])
        if missing: return {'created':0,'skipped':0,'failed':1,'errors':[{'row':1,'identifier':'','message':'Missing required columns: '+', '.join(sorted(missing))}],'warnings':0}
    for number,row in enumerate(rows,2):
        try:
            program_value=str(row.get('program','') or '').strip(); semester_value=str(row.get('semester','') or '').strip(); name=str(row.get('name','') or '').strip()
            if not program_value: raise ValueError('Program is required.')
            if not semester_value: raise ValueError('Semester is required.')
            if not name: raise ValueError('Name is required.')
            program=Program.objects.filter(code__iexact=program_value).first() or Program.objects.filter(name__iexact=program_value).first()
            if not program: raise ValueError(f'Program "{program_value}" not found.')
            sem=Semester.objects.filter(name__iexact=semester_value).first() or (Semester.objects.filter(number=int(float(semester_value))).first() if semester_value.replace('.','',1).isdigit() else None)
            if not sem: raise ValueError(f'Semester "{semester_value}" could not be resolved.')
            year_value=float(str(row.get('year','')).strip()); year=int(year_value)
            if year<=0 or year!=year_value or year>program.duration_years: raise ValueError('Year is outside the valid program range.')
            strength_value=str(row.get('student_strength','') or '').strip(); strength=60 if not strength_value else int(float(strength_value))
            if strength<=0 or (strength_value and float(strength_value)!=strength): raise ValueError('Student Strength must be a positive integer.')
            key=(program.pk,sem.pk,year,name.lower())
            if key in seen or Section.objects.filter(program=program,semester=sem,year=year,name__iexact=name).exists(): result['skipped']+=1; continue
            seen.add(key)
            if commit: Section.objects.create(program=program,semester=sem,year=year,name=name,student_strength=strength)
            result['created']+=1
        except Exception as exc: result['failed']+=1; result['errors'].append({'row':number,'identifier':row.get('name',''),'message':str(exc)})
    return result

def availability_import(rows, commit=True):
    result={'created':0,'updated':0,'skipped':0,'failed':0,'warnings':0,'errors':[]}; seen=set(); days={x.lower():i for i,x in enumerate(['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'])}
    for number,source in enumerate(rows,2):
        try:
            employee=value(source,['employee_code','faculty_code','Employee Code'],True); day=value(source,['weekday','day','Weekday'],True); slot_label=value(source,['time_slot','slot','Time Slot'],True); available=value(source,['available','is_available','availability'],True).lower(); weight=int(value(source,['preference_weight','weight','Preference Weight']) or 0)
            faculty=Faculty.objects.filter(employee_code__iexact=employee).first()
            if not faculty: raise ValueError(f'Faculty with employee code "{employee}" not found.')
            weekday=int(day) if day.isdigit() else days.get(day.lower(),-1)
            if weekday not in range(7): raise ValueError(f'Invalid weekday "{day}".')
            slot=TimeSlot.objects.filter(label__iexact=slot_label,is_break=False).first()
            if not slot:
                if TimeSlot.objects.filter(label__iexact=slot_label,is_break=True).exists(): raise ValueError(f'{slot_label} is a break slot and cannot be used for Faculty availability.')
                raise ValueError(f'Time slot "{slot_label}" not found.')
            if available not in {'yes','true','1','no','false','0'}: raise ValueError('Available must be Yes/No, True/False, or 1/0.')
            is_available=available in {'yes','true','1'}; key=(faculty.pk,weekday,slot.pk)
            if key in seen: result['skipped']+=1; continue
            seen.add(key); existing=FacultyAvailability.objects.filter(faculty=faculty,weekday=weekday,time_slot=slot).first(); created=existing is None
            if commit: FacultyAvailability.objects.update_or_create(faculty=faculty,weekday=weekday,time_slot=slot,defaults={'is_available':is_available,'preference_weight':weight})
            result['created' if created else 'updated']+=1
        except Exception as exc: result['failed']+=1; result['errors'].append({'row':number,'identifier':source.get('Employee Code',source.get('employee_code','')),'message':str(exc)})
    return result
