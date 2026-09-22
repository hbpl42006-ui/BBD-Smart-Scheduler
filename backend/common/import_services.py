from decimal import Decimal
from django.db import transaction
from accounts.models import User
from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from faculty.models import CourseOfferingFaculty, Faculty
from institutions.models import Department, Program

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

def faculty_import(rows):
    result={'created':0,'skipped':0,'failed':0,'errors':[]}; seen=set()
    for number,source in enumerate(rows,2):
        try:
            employee=value(source,['employee_code','Employee Code'],True)
            if employee in seen or Faculty.objects.filter(employee_code=employee).exists(): result['skipped']+=1; continue
            seen.add(employee); name=value(source,['faculty_name','name','Faculty Name'],True); dept=department(value(source,['department','Department'],True))
            if not dept: raise ValueError('Department not found.')
            load=int(value(source,['max_weekly_load','max_weekly_periods','Max Weekly Load']) or '16')
            if load<0: raise ValueError('Max weekly load must be non-negative.')
            email=value(source,['email','Email']); user=None
            if email:
                user=User.objects.filter(email__iexact=email).first()
                if user and hasattr(user,'faculty_profile'): raise ValueError('Email is already linked to another Faculty.')
            initials=''.join(part[0] for part in name.split()).upper()[:10] or employee[:10]
            Faculty.objects.create(user=user,name=name,employee_code=employee,initials=initials,department=dept,max_weekly_periods=load); result['created']+=1
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
