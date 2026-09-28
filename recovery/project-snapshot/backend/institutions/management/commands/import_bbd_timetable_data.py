from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction
from accounts.models import User, Role
from institutions.models import Institution, Department, Program
from academics.models import AcademicSession, Semester, Course
from faculty.models import Faculty
from .bbd_timetable_source_data import SOURCE_MAPPINGS

COURSES = '''
NBS4301|Complex Analysis and Integral Transforms
NCS4301|Discrete Mathematics
NCS4302|Data Structure using 'C'
NCS4303|Digital Logic Design
NCS4304|Core and Advance Java
NHS4301|Organizational Behavior
NCS4352|Data Structure Lab
NCS4353|Digital Logic Design Lab
NCS4354|Core and Advance Java Lab
NGP4301|General Proficiency
NHS4501|Engineering & Managerial Economics
NCS4502|Microprocessor and Interfacing
NCS4503|Computer Networks
NCS4504|Automata Theory and Formal Languages
NCS4505|Computer Graphics
NVC4501|Essence of Indian Knowledge Tradition
NCS4553|Computer Networks Lab
NCS4554|Minor Project-I
NCS4555|Computer Graphics Lab
NCS4701|Distributed Systems
NCS4702|Soft Computing
OE43101|Disaster Management
NCS4751|Distributed Systems Lab
NCS4753|Major Project-I
NCS4754|Industrial Training Evaluation
NAI4302|Artificial Intelligence in Mechanical Engineering Systems
NHS4302|Industrial Sociology
NCC4351|NSS/YOGA
NAI4501|Concepts of Data Science with Python
NAI4502|Artificial Neural Network
NAI4551|Data Science with Python Lab
NAI4552|Artificial Neural Network Lab
NAI4554|Minor Project-I
NAI4701|Natural Language Processing
NAI4702|Fuzzy Logic
NPEC43931|System Modelling & Simulation
OE43302|Non-Conventional Resources
NAI4751|Natural Language Processing Lab
NAI4754|Industrial Training Evaluation
NCCML4301|Fundamentals of Data Science
NCCML4302|Operating Systems
NCS4305|C Programming
NCCML4351|Data Science Lab
NCS4355|C Programming Lab
NCCML4501|Predictive Analytics
NCCML4502|Cloud Computing
NCCML4551|Predictive Analytics Lab
NCCML4552|Cloud Computing Lab
NCCML4553|Minor Project-I
NGP4501|General Proficiency
NCCML4701|Concept of Deep Learning
NPEC43824|Fuzzy Logic
NPEC43832|Computer Vision
NCCML4751|Deep Learning Lab
NCCML4753|Major Project-I
NCCML4754|Industrial Training Evaluation
NAIBC4301|Data Visualization with Python (IBM)
NAIBC4351|Data Visualization with Python Lab (IBM)
NITBC4502|Predictive Analytics
NITBC4501|Web Services
NITBC4552|Predictive Analytics Lab
NITBC4551|Web Services Lab
NITBC4553|Minor Project-I
NITBC4701|Privacy and Security in Internet of Things
NPEC44024|Cyber and Digital Forensics
NPEC44034|Computer Vision
NITBC4751|Privacy and Security in Internet of Things Lab
NITBC4753|Major Project-I
NITBC4754|Industrial Training Evaluation
NGP4701|General Proficiency
'''

FACULTY = '''Dr. Kashika Srivastava|KS
Dr. Vibhavari Srivastava|VS
Dr. R. K. Pandey|RKP
Mr. Anand Kumar Pandey|AKP
Mr. Yogendra Kumar Tripathi|YKT
Mr. Peeyush Kumar Shukla|PKS
Mr. Neeraj Baishwar|NB
Ms. Sapna Pal|SP
Mr. Naseem Ahamad Khan|NAK
Dr. Nitin Jain|NJ
Mr. Chanchal Nigam|CN
Mr. Amit Kumar Singh|AKS
Mr. Vivek Singh|VS2
Mr. Gaurav Kumar Srivastava|GKS
Mr. Anil Kumar|AK
Mr. Avinash Pandey|AP
Dr. Anshubhi Bahadur|AB
Dr. Mohd. Ahmer|MA
Ms. Neha Singh|NS
Mr. Neelabh Kumar|NK
Ms. Pooja Srivastava|PS
Dr. Kavita Shukla|KS2
Dr. Mrinalini Srivastava|MS
Dr. Priya Kumari|PK
Dr. Ramesh Vaishya|RV
Ms. Mala Chaturvedi|MC
Mr. Ritesh Kumar|RK
Ms. Nargis Siddiqui|Nargis
Ms. Sumaiya Rehan|SR
Ms. Deepti Razdan|DR
Ms. Shailja Chaurasiya|SC
Dr. Dhyan Chandra Yadav|DCY
Ms. Shalini Verma|SV
Ms. Kratika Chandra|KC
Mr. Gaurav Singh|GS
Mr. Saurabh Pandey|SP2
Mr. Tushar Giri|TG
Dr. Suman Sharma|SS
Dr. Priyanka Singh|PS2
Mr. Prashant Kumar Shukla|PKS2
Dr. Shikha Yadav|SY
Ms. Shraddha Tiwari|ST
Ms. Surabhi Mishra|SM
Ms. Jyoti Yadav|JY
Mr. Ravi Shankar Yadav|RSY
Dr. Amrit Anand Dosar|AAD
Mr. Shailesh Vishwakarma|SV2
Mr. Pradeep Kumar Gupta|PKG
Mr. Manoj Soni|MS2
Dr. Pooja Verma|PV
Mr. Dheeraj Kumar Yadav|DKY
Ms. Veena Dwivedi|VD
Ms. Sonam Gupta|SG
Dr. Yogita Yadav|YY
Mr. Sameer Rastogi|SR2
Dr. Harsh Dev|HD
Dr. Radhey Shyam|RS
Ms. Divya Agrahari|DA
Ms. Shubhranjali Nigam|SN
Ms. Vaishnavi Yadav|VY
Ms. Yadav Pratima Kedarnath|YPK
Mr. Satish Kumar Singh|SKS
Ms. Anshu Khare|AK2
Mr. Rakesh Sharma|RS2
Mr. Ahamad Raza|AR
Ms. Pooja Tiwari|PT
Mr. Sonu Kushwaha|SK
Ms. Hima Saxena|HS
Mr. Prabhdeep Singh|PS3
Ms. Bhavya Mishra|BM
Ms. Neeti Mishra|NM
Ms. Arti Singh|AS
Mr. Hemant Tripathi|HT
Ms. Sweeti|Sweeti
Mr. Utkarsh Pandey|UP
Ms. Akansha Shukla|AS2
Mr. Harshranjan|Harshranjan
Ms. Awantika|Awantika
Mr. Harendra|Harendra
Dr. Rajan Prasad|RP
Dr. Sujata Kushwaha|SK2
Ms. Sunita Sharma|SS2
Mr. Abhishek Sharma|AS3
Mr. Souvik|Souvik
Dr. Mohd. Afaque|MA2'''

def rows(value):
    return [tuple(x.strip() for x in line.split('|', 1)) for line in value.splitlines() if line.strip()]

class Command(BaseCommand):
    help = 'Import the supplied BBD timetable academic master data safely.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Persist changes; default is dry-run.')
        parser.add_argument('--dry-run', action='store_true', help='Validate and report without writes.')

    def handle(self, *args, **opts):
        apply = opts['apply'] and not opts['dry_run']
        if not apply:
            self.stdout.write('DRY RUN: no database writes will be made.')
            self.stdout.write(f'Courses in source: {len(rows(COURSES))}')
            self.stdout.write(f'Faculty in source: {len(rows(FACULTY))}')
            self.stdout.write('CourseOfferings skipped: section mapping and weekly periods were not supplied by the source text.')
            self.stdout.write('Ambiguous faculty rows skipped: source assignment metadata was not supplied.')
            self.stdout.write(f'Source mappings parsed: {len(SOURCE_MAPPINGS)}; preserved-only: {len(SOURCE_MAPPINGS)}')
            return
        with transaction.atomic() if apply else self._noop_transaction():
            inst, _ = Institution.objects.get_or_create(code='BBDU', defaults={'name':'Babu Banarasi Das University'})
            dept, _ = Department.objects.get_or_create(code='CSE', defaults={'institution':inst,'name':'Computer Science & Engineering'})
            session, _ = AcademicSession.objects.get_or_create(institution=inst, name='2026-27', defaults={'start_date':date(2026,7,1),'end_date':date(2027,6,30)})
            sem, _ = Semester.objects.get_or_create(session=session, number=1, defaults={'name':'Odd Semester','type':'ODD','start_date':date(2026,7,1),'end_date':date(2026,12,31)})
            removed = 0
            for old in list(Faculty.objects.filter(user__email__endswith='@invalid.local', employee_code__startswith='BBD-')):
                user = old.user
                if not user.created_timetables.exists() and not user.created_timetable_versions.exists() and not user.generation_runs.exists() and not user.audit_events.exists():
                    old.delete(); user.delete(); removed += 1
            self.stdout.write(f'Placeholder users safely removed: {removed}')
            for code,name in rows(COURSES):
                obj, created = Course.objects.get_or_create(code=code, defaults={'name':name,'short_code':code[:20],'credit':3})
                if not created and obj.name != name: self.stdout.write(f'COURSE_NAME_CONFLICT: {code} Existing: {obj.name} Source: {name}')
                self.stdout.write(f'COURSE {"create" if created else "existing"}: {code}')
            for full, initials in rows(FACULTY):
                first, *last = full.split()[1:]
                Faculty.objects.get_or_create(employee_code=f'BBD-{initials}', defaults={'user':None,'initials':initials,'department':dept})
                self.stdout.write(f'FACULTY: {full}')
            self.stdout.write(f'Source mappings parsed: {len(SOURCE_MAPPINGS)}; preserved-only: {len(SOURCE_MAPPINGS)}')
            self.stdout.write('SKIPPED_AMBIGUOUS_FACULTY: ' + ', '.join(x['metadata'] for x in SOURCE_MAPPINGS if not x['faculty_name']))
            self.stdout.write('CourseOfferings skipped: section mapping and weekly periods were not supplied by the source text.')
            if not apply: raise DryRunRollback()
        self.stdout.write(self.style.SUCCESS('BBD timetable master data import complete.'))

    class _noop_transaction:
        def __enter__(self): return self
        def __exit__(self, *args): return False

class DryRunRollback(Exception):
    pass
