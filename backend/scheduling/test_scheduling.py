import pytest
from datetime import date,time
from rest_framework.test import APIClient
from accounts.models import User,Role
from institutions.models import Institution,Department,Program
from academics.models import AcademicSession,Semester,Section,Course,CourseOffering
from faculty.models import Faculty,FacultyAvailability
from rooms.models import Room,RoomAvailability
from common.models import TimeSlotTemplate,TimeSlot
from scheduling.models import Timetable,TimetableVersion,ScheduleEntry,ScheduleEntryFaculty
from audit.models import AuditEvent

@pytest.fixture
def data(db):
    admin=User.objects.create_user('scheduler@test.local','Pass12345!',role=Role.TIMETABLE_COORDINATOR)
    institution=Institution.objects.create(name='Test University',code='TU'); department=Department.objects.create(institution=institution,name='Computer Science',code='CSE'); program=Program.objects.create(department=department,name='B.Tech CSE',code='BT',duration_years=4)
    session=AcademicSession.objects.create(institution=institution,name='2027-28',start_date=date(2027,7,1),end_date=date(2028,6,30)); semester=Semester.objects.create(session=session,name='Odd',number=1,type='ODD',start_date=date(2027,7,1),end_date=date(2027,12,31)); section=Section.objects.create(program=program,semester=semester,year=1,name='A',student_strength=40)
    course=Course.objects.create(code='CS101',name='Operating Systems',short_code='OS',credit=3); offering=CourseOffering.objects.create(semester=semester,section=section,course=course,weekly_periods=3,default_class_type='LECTURE',required_block_size=1)
    user=User.objects.create_user('faculty@test.local','Pass12345!',role=Role.FACULTY,first_name='Test',last_name='Faculty'); faculty=Faculty.objects.create(user=user,employee_code='F001',initials='TF',department=department)
    room=Room.objects.create(code='R101',building='Main',floor='1',capacity=50,room_type='CLASSROOM'); template=TimeSlotTemplate.objects.create(institution=institution,name='Standard'); slots=[TimeSlot.objects.create(template=template,label=f'{h}:00-{h+1}:00',start_time=time(h),end_time=time(h+1),order=i) for i,h in enumerate([9,10,11,12],1)]
    timetable=Timetable.objects.create(institution=institution,academic_session=session,semester=semester,department=department,title='Test Timetable',created_by=admin); version=TimetableVersion.objects.create(timetable=timetable,version_no=1,created_by=admin)
    return locals()
@pytest.fixture
def client(data):
    c=APIClient(); c.force_authenticate(data['admin']); return c
def payload(d,**over): return {'section':str(d['section'].pk),'course_offering':str(d['offering'].pk),'weekday':0,'start_slot':str(d['slots'][0].pk),'block_length':1,'room':str(d['room'].pk),**over}
def test_timetable_and_version(data): assert TimetableVersion.objects.filter(timetable=data['timetable'],version_no=1,status='DRAFT').exists()
def test_entry_create_and_audit(client,data):
    r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json'); assert r.status_code==201; assert AuditEvent.objects.filter(event_type='ENTRY_CREATED').exists()
def test_section_clash(client,data):
    client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json'); r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json'); assert any(x['type']=='SECTION_CLASH' for x in r.data['conflicts'])
def test_room_clash(client,data):
    other=Section.objects.create(program=data['program'],semester=data['semester'],year=1,name='B',student_strength=20); client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json'); r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data,section=str(other.pk)),format='json'); assert any(x['type']=='ROOM_CLASH' for x in r.data['conflicts'])
def test_faculty_clash(client,data):
    e=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room']); ScheduleEntryFaculty.objects.create(schedule_entry=e,faculty=data['faculty']); r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data,faculty_ids=[str(data['faculty'].pk)]),format='json'); assert any(x['type']=='FACULTY_CLASH' for x in r.data['conflicts'])
def test_capacity_failure(client,data):
    data['section'].student_strength=100;data['section'].save();r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json');assert any(x['type']=='ROOM_CAPACITY_EXCEEDED' for x in r.data['conflicts'])
def test_room_blocked(client,data):
    RoomAvailability.objects.create(room=data['room'],weekday=0,time_slot=data['slots'][0],status='BLOCKED');r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json');assert any(x['type']=='ROOM_BLOCKED' for x in r.data['conflicts'])
def test_break_overlap(client,data):
    data['slots'][1].is_break=True;data['slots'][1].save();r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data,block_length=2),format='json');assert any(x['type']=='BREAK_OVERLAP' for x in r.data['conflicts'])
def test_validate_entry_does_not_persist(client,data):
    r=client.post(f"/api/versions/{data['version'].pk}/validate-entry/",payload(data),format='json');assert r.data['valid'] is True;assert not ScheduleEntry.objects.exists()
def test_full_validation_completion(client,data):
    r=client.post(f"/api/versions/{data['version'].pk}/validate/",{},format='json');assert r.status_code==200;assert r.data['course_completion'][0]['remaining']==3
def test_lock_blocks_update_and_unlock_allows(client,data):
    e=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'],locked=True);r=client.patch(f"/api/versions/{data['version'].pk}/entries/{e.pk}/",{'note':'x'},format='json');assert r.status_code==409;client.post(f"/api/entries/{e.pk}/unlock/");assert client.patch(f"/api/versions/{data['version'].pk}/entries/{e.pk}/",{'note':'x'},format='json').status_code==200
def test_projection_and_rooms(client,data):
    ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room']);assert client.get(f"/api/versions/{data['version'].pk}/section-timetable/?section={data['section'].pk}").status_code==200;assert client.get(f"/api/versions/{data['version'].pk}/room-allocation/").status_code==200
def test_clone_copies_entries(client,data):
    ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'],locked=True);r=client.post(f"/api/timetables/{data['timetable'].pk}/versions/",{'source_version':str(data['version'].pk)},format='json');assert r.status_code==201;assert ScheduleEntry.objects.filter(version_id=r.data['id'],locked=True).exists()
def test_read_only_cannot_create(data):
    u=User.objects.create_user('viewer@test.local','Pass12345!',role=Role.READ_ONLY_VIEWER);c=APIClient();c.force_authenticate(u);assert c.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json').status_code==403

def test_entry_delete_and_audit(client,data):
    e=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room']);r=client.delete(f"/api/versions/{data['version'].pk}/entries/{e.pk}/");assert r.status_code==204;assert not ScheduleEntry.objects.filter(pk=e.pk).exists();assert AuditEvent.objects.filter(event_type='ENTRY_DELETED',entity_id=e.pk).exists()
def test_maintenance_room_rejected(client,data):
    RoomAvailability.objects.create(room=data['room'],weekday=0,time_slot=data['slots'][0],status='MAINTENANCE');r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json');assert any(x['type']=='ROOM_MAINTENANCE' for x in r.data['conflicts'])
def test_faculty_unavailable_rejected(client,data):
    FacultyAvailability.objects.create(faculty=data['faculty'],weekday=0,time_slot=data['slots'][0],is_available=False);r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data,faculty_ids=[str(data['faculty'].pk)]),format='json');assert any(x['type']=='FACULTY_UNAVAILABLE' for x in r.data['conflicts'])
def test_exact_capacity_and_wrong_type(client,data):
    data['section'].student_strength=50;data['section'].save();assert client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json').status_code==201;data['offering'].room_type_requirement='COMPUTER_LAB';data['offering'].save();r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data,start_slot=str(data['slots'][1].pk)),format='json');assert any(x['type']=='ROOM_TYPE_MISMATCH' for x in r.data['conflicts'])
def test_invalid_end_of_day_block(client,data):
    r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data,start_slot=str(data['slots'][-1].pk),block_length=2),format='json');assert any(x['type']=='INVALID_BLOCK' for x in r.data['conflicts'])
def test_block_is_single_row(client,data):
    r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data,block_length=2),format='json');assert r.status_code==201;assert ScheduleEntry.objects.filter(version=data['version']).count()==1
def test_validate_conflict_and_exclude(client,data):
    e=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room']);r=client.post(f"/api/versions/{data['version'].pk}/validate-entry/",{**payload(data),'exclude_entry_id':str(e.pk)},format='json');assert r.data['valid'] is True
def test_version_immutable(client,data):
    data['version'].status='PUBLISHED';data['version'].save();r=client.post(f"/api/versions/{data['version'].pk}/entries/",payload(data),format='json');assert r.status_code==409
def test_faculty_projection_filters(client,data):
    e=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room']);ScheduleEntryFaculty.objects.create(schedule_entry=e,faculty=data['faculty']);r=client.get(f"/api/versions/{data['version'].pk}/faculty-timetable/?faculty={data['faculty'].pk}");assert len(r.data['entries'])==1
def test_available_room_block_filter(client,data):
    RoomAvailability.objects.create(room=data['room'],weekday=0,time_slot=data['slots'][0],status='BLOCKED');r=client.get(f"/api/versions/{data['version'].pk}/available-rooms/?weekday=0&start_slot={data['slots'][0].pk}&block_length=1&capacity=40");assert not any(x['id']==str(data['room'].pk) for x in r.data)
def test_audit_lock_unlock(client,data):
    e=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room']);client.post(f"/api/entries/{e.pk}/lock/");client.post(f"/api/entries/{e.pk}/unlock/");assert AuditEvent.objects.filter(event_type='ENTRY_LOCKED',entity_id=e.pk).exists();assert AuditEvent.objects.filter(event_type='ENTRY_UNLOCKED',entity_id=e.pk).exists()
