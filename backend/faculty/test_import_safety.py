from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Role, User
from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from common.import_services import faculty_import_safe
from faculty.models import Faculty, FacultyAvailability
from institutions.models import Department, Institution, Program
from common.models import TimeSlotTemplate, TimeSlot
from common.imports import rows_from_upload
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
from openpyxl import Workbook


class FacultyImportSafetyTests(TestCase):
    def setUp(self):
        self.institution=Institution.objects.create(name='Test University',code='TU')
        self.department=Department.objects.create(institution=self.institution,name='Computer Science',code='CSE')
        self.admin=User.objects.create_user('admin@test.local','pass',role=Role.SUPER_ADMIN)
        self.slot_template=TimeSlotTemplate.objects.create(institution=self.institution,name='Default')
        self.slot=TimeSlot.objects.create(template=self.slot_template,label='09:00-10:00',start_time='09:00',end_time='10:00',order=1,is_break=False)
        self.rows=lambda **changes: [{**{'Faculty Name':'Dr. Test Faculty','Department':'CSE','Employee Code':'','Email':'','Initials':''},**changes}]

    def test_optional_identity_fields_create_without_user(self):
        result=faculty_import_safe(self.rows(),commit=True,mode='UPSERT')
        faculty=Faculty.objects.get(name='Dr. Test Faculty')
        self.assertEqual(result['created'],1); self.assertIsNone(faculty.user_id); self.assertIsNone(faculty.employee_code); self.assertEqual(faculty.initials,'')

    def test_name_department_upsert_preserves_pk_and_blank_fields(self):
        faculty=Faculty.objects.create(name='Dr. Test Faculty',employee_code='BBD-T',initials='TF',department=self.department)
        old_id=faculty.pk
        result=faculty_import_safe(self.rows(**{'Faculty Name':'  dr.   test faculty  '}),commit=True,mode='UPSERT')
        faculty.refresh_from_db(); self.assertEqual(result['updated'],1); self.assertEqual(faculty.pk,old_id); self.assertEqual(faculty.employee_code,'BBD-T'); self.assertEqual(faculty.initials,'TF')

    def test_linked_user_is_preserved_when_identity_fields_are_blank(self):
        user=User.objects.create_user('faculty@test.local','secret',role=Role.FACULTY)
        faculty=Faculty.objects.create(user=user,name='Linked Faculty',employee_code='LINK',initials='LF',department=self.department)
        faculty_import_safe([{'Faculty Name':'Linked Faculty','Department':'CSE','Employee Code':'','Email':'','Initials':''}],commit=True,mode='UPSERT')
        faculty.refresh_from_db(); user.refresh_from_db()
        self.assertEqual(faculty.user_id,user.pk); self.assertEqual(user.email,'faculty@test.local'); self.assertEqual(user.role,Role.FACULTY); self.assertTrue(user.check_password('secret')); self.assertEqual(faculty.employee_code,'LINK'); self.assertEqual(faculty.initials,'LF')

    def test_create_only_skips_existing_and_creates_new(self):
        Faculty.objects.create(name='Existing',employee_code='EX',department=self.department)
        rows=[{'Faculty Name':'Existing','Department':'CSE','Employee Code':'EX'},{'Faculty Name':'New','Department':'CSE','Employee Code':'NEW'}]
        result=faculty_import_safe(rows,commit=True,mode='CREATE_ONLY')
        self.assertEqual(result['skipped'],1); self.assertEqual(result['created'],1); self.assertEqual(Faculty.objects.filter(employee_code='EX').count(),1); self.assertTrue(Faculty.objects.filter(employee_code='NEW').exists())

    def test_preview_is_non_destructive_and_repeat_upsert_has_no_duplicates(self):
        existing=Faculty.objects.create(name='Existing',employee_code='EX',department=self.department)
        rows=[{'Faculty Name':'Existing','Department':'CSE','Employee Code':'EX'},{'Faculty Name':'New','Department':'CSE','Employee Code':'NEW'}]
        before=Faculty.objects.count(); preview=faculty_import_safe(rows,commit=False,mode='UPSERT')
        self.assertEqual(preview['created'],1); self.assertEqual(preview['updated'],1); self.assertEqual(Faculty.objects.count(),before)
        faculty_import_safe(rows,commit=True,mode='UPSERT'); faculty_import_safe(rows,commit=True,mode='UPSERT')
        self.assertEqual(Faculty.objects.filter(employee_code='NEW').count(),1); self.assertEqual(Faculty.objects.get(employee_code='EX').pk,existing.pk)

    def test_ambiguous_name_is_invalid(self):
        Faculty.objects.create(name='Same Name',department=self.department)
        Faculty.objects.create(name='Same Name',department=self.department)
        result=faculty_import_safe([{'Faculty Name':'Same Name','Department':'CSE','Employee Code':'','Email':''}],commit=False)
        self.assertEqual(result['failed'],1); self.assertIn('Multiple faculty records',result['errors'][0]['message'])

    def test_replace_archives_without_deleting_availability(self):
        faculty=Faculty.objects.create(name='Old Faculty',employee_code='OLD',department=self.department)
        FacultyAvailability.objects.create(faculty=faculty,weekday=0,time_slot=self.slot,is_available=True)
        result=faculty_import_safe(self.rows(**{'Faculty Name':'New Faculty'}),commit=True,mode='REPLACE')
        faculty.refresh_from_db(); self.assertEqual(result['archive'],1); self.assertFalse(faculty.active); self.assertTrue(FacultyAvailability.objects.filter(faculty=faculty).exists())

    def test_referenced_delete_returns_conflict_and_archive_preserves_fk(self):
        session=AcademicSession.objects.create(institution=self.institution,name='2026-27',start_date='2026-01-01',end_date='2026-12-31')
        semester=Semester.objects.create(session=session,name='Odd',number=1,type='ODD',start_date='2026-01-01',end_date='2026-06-30')
        program=Program.objects.create(department=self.department,name='B.Tech',code='BT',duration_years=4)
        section=Section.objects.create(program=program,semester=semester,year=1,name='A')
        course=Course.objects.create(code='CS101',name='Course',short_code='C',credit=3)
        offering=CourseOffering.objects.create(semester=semester,section=section,course=course,weekly_periods=1)
        faculty=Faculty.objects.create(name='Referenced',employee_code='REF',department=self.department)
        from faculty.models import CourseOfferingFaculty
        CourseOfferingFaculty.objects.create(course_offering=offering,faculty=faculty)
        client=APIClient(); client.force_authenticate(self.admin)
        response=client.delete(f'/api/faculty/{faculty.pk}/')
        faculty.refresh_from_db(); self.assertEqual(response.status_code,409); self.assertTrue(response.data['can_archive']); self.assertTrue(faculty.active)
        archive=client.post(f'/api/faculty/{faculty.pk}/archive/'); faculty.refresh_from_db()
        self.assertEqual(archive.status_code,200); self.assertFalse(faculty.active); self.assertTrue(CourseOfferingFaculty.objects.filter(faculty=faculty).exists())
        restore=client.post(f'/api/faculty/{faculty.pk}/restore/'); faculty.refresh_from_db()
        self.assertEqual(restore.status_code,200); self.assertTrue(faculty.active)

    def test_unused_faculty_delete_succeeds(self):
        faculty=Faculty.objects.create(name='Unused',employee_code='UNUSED',department=self.department)
        client=APIClient(); client.force_authenticate(self.admin)
        response=client.delete(f'/api/faculty/{faculty.pk}/')
        self.assertEqual(response.status_code,204); self.assertFalse(Faculty.objects.filter(pk=faculty.pk).exists())

    def test_real_faculty_xlsx_headers_and_blank_optional_fields_are_parsed(self):
        workbook=Workbook(); workbook.active.title='Cover'; workbook.create_sheet('Faculty Import')
        sheet=workbook['Faculty Import']; sheet.append(['Faculty Name','Employee Code','Initials','Email','Department','Login'])
        sheet.append(['Dr. Shikha Yadav','','','','CSE',''])
        sheet.append(['Ms. Hima Saxena','','HS','','CSE',''])
        sheet.append(['Mr. Hemant Tripathi','','','','CSE',''])
        stream=BytesIO(); workbook.save(stream)
        rows=rows_from_upload(SimpleUploadedFile('BBD_Faculty_Real_Data.xlsx',stream.getvalue()))
        self.assertEqual(len(rows),3); self.assertEqual(rows[0]['Faculty Name'],'Dr. Shikha Yadav')
        result=faculty_import_safe(rows,commit=False,mode='UPSERT')
        self.assertEqual(result['created'],3); self.assertEqual(result['failed'],0)

    def test_api_create_and_patch_persist_contact_email_without_creating_user(self):
        client=APIClient(); client.force_authenticate(self.admin)
        response=client.post('/api/faculty/', {'name':'Contact Faculty','employee_code':'CONTACT','initials':'CF','department':str(self.department.pk),'email':'contact@example.com'}, format='json')
        self.assertEqual(response.status_code, 201)
        faculty=Faculty.objects.get(employee_code='CONTACT')
        self.assertEqual(faculty.email,'contact@example.com'); self.assertIsNone(faculty.user_id)
        response=client.patch(f'/api/faculty/{faculty.pk}/', {'email':'updated@example.com'}, format='json')
        self.assertEqual(response.status_code, 200)
        faculty.refresh_from_db(); self.assertEqual(faculty.email,'updated@example.com'); self.assertIsNone(faculty.user_id)
        self.assertEqual(response.data['email'],'updated@example.com')

    def test_email_does_not_create_login_and_explicit_login_requires_password(self):
        client=APIClient(); client.force_authenticate(self.admin)
        response=client.post('/api/faculty/', {'name':'Email Only','employee_code':'EMAILONLY','initials':'EO','department':str(self.department.pk),'email':'email-only@example.com'}, format='json')
        self.assertEqual(response.status_code,201); faculty=Faculty.objects.get(employee_code='EMAILONLY')
        self.assertIsNone(faculty.user_id)
        response=client.post('/api/faculty/', {'name':'Needs Password','employee_code':'NEEDSPASSWORD','initials':'NP','department':str(self.department.pk),'email':'needs-password@example.com','create_login':True}, format='json')
        self.assertEqual(response.status_code,400); self.assertIn('password',response.data); self.assertFalse(Faculty.objects.filter(employee_code='NEEDSPASSWORD').exists())
        response=client.post('/api/faculty/', {'name':'With Login','employee_code':'WITHLOGIN','initials':'WL','department':str(self.department.pk),'email':'with-login@example.com','password':'StrongPass123!','create_login':True}, format='json')
        self.assertEqual(response.status_code,201); faculty=Faculty.objects.get(employee_code='WITHLOGIN')
        self.assertIsNotNone(faculty.user_id); self.assertEqual(faculty.user.email,'with-login@example.com')

    def test_linked_faculty_email_sync_and_conflict_are_atomic(self):
        user=User.objects.create_user('linked-email@example.com','secret',role=Role.FACULTY)
        faculty=Faculty.objects.create(user=user,name='Linked Email',employee_code='LE',department=self.department,email=user.email)
        client=APIClient(); client.force_authenticate(self.admin)
        response=client.patch(f'/api/faculty/{faculty.pk}/', {'email':'new-linked@example.com'}, format='json')
        self.assertEqual(response.status_code, 200)
        faculty.refresh_from_db(); user.refresh_from_db(); self.assertEqual(faculty.email,user.email)
        other=User.objects.create_user('taken@example.com','secret',role=Role.FACULTY)
        before=faculty.email
        response=client.patch(f'/api/faculty/{faculty.pk}/', {'email':other.email}, format='json')
        self.assertEqual(response.status_code, 400); self.assertIn('email', response.data)
        faculty.refresh_from_db(); user.refresh_from_db(); self.assertEqual(faculty.email,before); self.assertEqual(user.email,before)

    def test_import_upsert_updates_email_and_blank_preserves_it(self):
        faculty=Faculty.objects.create(name='Import Email',employee_code='IE',email='old@example.com',department=self.department)
        faculty_import_safe([{'Faculty Name':'Import Email','Employee Code':'IE','Email':'new@example.com','Initials':'IE','Department':'CSE'}],commit=True,mode='UPSERT')
        faculty.refresh_from_db(); self.assertEqual(faculty.email,'new@example.com')
        faculty_import_safe([{'Faculty Name':'Import Email','Employee Code':'IE','Email':'','Initials':'IE','Department':'CSE'}],commit=True,mode='UPSERT')
        faculty.refresh_from_db(); self.assertEqual(faculty.email,'new@example.com'); self.assertEqual(User.objects.filter(email='new@example.com').count(),0)
