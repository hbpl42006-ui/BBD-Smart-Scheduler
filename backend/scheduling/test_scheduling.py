import pytest
from datetime import date,time
from unittest.mock import patch
from rest_framework.test import APIClient
from accounts.models import User,Role
from institutions.models import Institution,Department,Program
from academics.models import AcademicSession,Semester,Section,Course,CourseOffering
from faculty.models import Faculty,FacultyAvailability
from rooms.models import Room,RoomAvailability
from common.models import TimeSlotTemplate,TimeSlot
from scheduling.models import Timetable,TimetableVersion,ScheduleEntry,ScheduleEntryFaculty
from audit.models import AuditEvent
from scheduling.solver.engine import Requirement,FacultyAssignment,resolve_slots,build_requirements,build_candidates
from scheduling.solver.objective import weights,internal_gap_count
from notifications.services import publish_diff,notify_published_diff
from notifications.models import Notification,NotificationDelivery,NotificationPreference

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

def test_section_timetable_serializes_linked_and_unlinked_faculty(client,data):
    unlinked=Faculty.objects.create(user=None,employee_code='F002',initials='UF',department=data['department'])
    entry=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'])
    ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'],role='PRIMARY')
    ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=unlinked,role='CO_FACULTY')
    before=User.objects.count()
    response=client.get(f"/api/versions/{data['version'].pk}/section-timetable/?section={data['section'].pk}")
    assert response.status_code==200
    assignments=response.data['entries'][0]['faculty_assignments']
    assert {item['faculty_id'] for item in assignments}=={str(data['faculty'].pk),str(unlinked.pk)}
    assert all({'faculty_id','role','name','initials'} <= set(item) for item in assignments)
    assert {item['name'] for item in assignments}=={'Test Faculty','UF'}
    assert User.objects.count()==before

def test_faculty_can_retrieve_only_own_timetable(client,data):
    data['version'].status='PUBLISHED'; data['version'].save(update_fields=['status'])
    data['faculty'].user.role=Role.FACULTY; data['faculty'].user.save(update_fields=['role'])
    other_user=User.objects.create_user('other-faculty@test.local','Pass12345!',role=Role.FACULTY)
    other=Faculty.objects.create(user=other_user,employee_code='F002',initials='OF',department=data['department'])
    entry=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'])
    ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'])
    other_entry=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=1,start_slot=data['slots'][1],room=data['room'])
    ScheduleEntryFaculty.objects.create(schedule_entry=other_entry,faculty=other)
    client.force_authenticate(data['faculty'].user)
    response=client.get('/api/me/faculty-timetable/')
    assert response.status_code==200
    assert {item['id'] for item in response.data['entries']}=={str(entry.pk)}
    assert response.data['faculty']['id']==str(data['faculty'].pk)
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

def _update_entry(data, **changes):
    entry=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'])
    ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'])
    return entry, {'room':str(changes.pop('room',data['room']).pk), **changes}

def _other_section_and_offering(data, name='B', code='CS102'):
    section=Section.objects.create(program=data['program'],semester=data['semester'],year=1,name=name,student_strength=20)
    course=Course.objects.create(code=code,name='Second Course',short_code='SC',credit=3)
    offering=CourseOffering.objects.create(semester=data['semester'],section=section,course=course,weekly_periods=1)
    return section,offering

def test_update_same_slot_room_change_excludes_current_entry_from_all_self_clashes(client,data):
    entry,changes=_update_entry(data,room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    response=client.patch(f'/api/versions/{data["version"].pk}/entries/{entry.pk}/',changes,format='json')
    assert response.status_code==200
    assert not any(item['type'] in {'SECTION_CLASH','FACULTY_CLASH','ROOM_CLASH'} for item in response.data.get('conflicts',[]))

def test_update_insufficient_capacity_keeps_only_real_capacity_error(client,data):
    entry,changes=_update_entry(data,room=Room.objects.create(code='R404',building='Main',floor='4',capacity=1,room_type='CLASSROOM'))
    response=client.patch(f'/api/versions/{data["version"].pk}/entries/{entry.pk}/',changes,format='json')
    types={item['type'] for item in response.data['conflicts']}
    assert response.status_code==400 and 'ROOM_CAPACITY_EXCEEDED' in types and not types & {'SECTION_CLASH','FACULTY_CLASH','ROOM_CLASH'}

def test_update_to_another_section_slot_keeps_section_clash(client,data):
    _,other_offering=_other_section_and_offering(data)
    other_room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM')
    ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=other_offering,weekday=0,start_slot=data['slots'][1],room=other_room)
    entry,changes=_update_entry(data,room=Room.objects.create(code='R103',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    changes['start_slot']=str(data['slots'][1].pk)
    response=client.patch(f'/api/versions/{data["version"].pk}/entries/{entry.pk}/',changes,format='json')
    assert response.status_code==400 and any(item['type']=='SECTION_CLASH' for item in response.data['conflicts'])

def test_update_to_slot_with_faculty_on_another_entry_keeps_faculty_clash(client,data):
    other_section,other_offering=_other_section_and_offering(data)
    other_room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM')
    other=ScheduleEntry.objects.create(version=data['version'],section=other_section,course_offering=other_offering,weekday=0,start_slot=data['slots'][1],room=other_room)
    ScheduleEntryFaculty.objects.create(schedule_entry=other,faculty=data['faculty'])
    entry,_=_update_entry(data,room=Room.objects.create(code='R103',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    response=client.patch(f'/api/versions/{data["version"].pk}/entries/{entry.pk}/',{'start_slot':str(data['slots'][1].pk),'faculty_ids':[str(data['faculty'].pk)]},format='json')
    assert response.status_code==400 and any(item['type']=='FACULTY_CLASH' for item in response.data['conflicts'])

def test_update_to_room_occupied_by_another_entry_keeps_room_clash(client,data):
    other_section,other_offering=_other_section_and_offering(data)
    other_room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM')
    ScheduleEntry.objects.create(version=data['version'],section=other_section,course_offering=other_offering,weekday=0,start_slot=data['slots'][0],room=other_room)
    entry,_=_update_entry(data,room=Room.objects.create(code='R103',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    response=client.patch(f'/api/versions/{data["version"].pk}/entries/{entry.pk}/',{'room':str(other_room.pk)},format='json')
    assert response.status_code==400 and any(item['type']=='ROOM_CLASH' for item in response.data['conflicts'])

def test_update_preserves_multiple_faculty_assignments(client,data):
    second=Faculty.objects.create(user=None,employee_code='F002',initials='SF',department=data['department'])
    entry,changes=_update_entry(data,room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    changes['faculty_assignments']=[{'faculty_id':str(data['faculty'].pk),'role':'PRIMARY'},{'faculty_id':str(second.pk),'role':'CO_FACULTY'}]
    response=client.patch(f'/api/versions/{data["version"].pk}/entries/{entry.pk}/',changes,format='json')
    assert response.status_code==200 and {x['faculty_id'] for x in response.data['faculty_assignments']}=={str(data['faculty'].pk),str(second.pk)}

def _validate_entry_payload(data,entry,**changes):
    def identifier(value): return str(getattr(value,'pk',value))
    return {'id':str(entry.pk),'section':identifier(changes.pop('section',entry.section_id)),'course_offering':identifier(changes.pop('course_offering',entry.course_offering_id)),'weekday':changes.pop('weekday',entry.weekday),'start_slot':identifier(changes.pop('start_slot',entry.start_slot_id)),'block_length':changes.pop('block_length',entry.block_length),'room':identifier(changes.pop('room',entry.room_id)),'faculty_ids':changes.pop('faculty_ids',[str(data['faculty'].pk)]),**changes}

def test_validate_entry_endpoint_excludes_current_entry_from_self_conflicts(client,data):
    entry,_=_update_entry(data,room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    response=client.post(f'/api/versions/{data["version"].pk}/validate-entry/',_validate_entry_payload(data,entry),format='json')
    assert response.status_code==200 and response.data['valid'] is True
    assert not any(item.get('conflicting_entry_id')==str(entry.pk) for item in response.data['conflicts'])
    assert not {item['type'] for item in response.data['conflicts']} & {'SECTION_CLASH','FACULTY_CLASH','ROOM_CLASH'}

def test_validate_entry_endpoint_keeps_capacity_error_without_self_clashes(client,data):
    room=Room.objects.create(code='R404',building='Main',floor='4',capacity=1,room_type='CLASSROOM');entry,_=_update_entry(data,room=room)
    response=client.post(f'/api/versions/{data["version"].pk}/validate-entry/',_validate_entry_payload(data,entry,room=room),format='json')
    types={item['type'] for item in response.data['conflicts']}
    assert response.status_code==200 and 'ROOM_CAPACITY_EXCEEDED' in types and not types & {'SECTION_CLASH','FACULTY_CLASH','ROOM_CLASH'}

def test_validate_entry_endpoint_reports_other_section_entry(client,data):
    _,other_offering=_other_section_and_offering(data)
    other=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=other_offering,weekday=0,start_slot=data['slots'][1],room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    entry,_=_update_entry(data,room=Room.objects.create(code='R103',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    response=client.post(f'/api/versions/{data["version"].pk}/validate-entry/',_validate_entry_payload(data,entry,start_slot=data['slots'][1]),format='json')
    clashes=[item for item in response.data['conflicts'] if item['type']=='SECTION_CLASH']
    assert response.status_code==200 and clashes and clashes[0]['conflicting_entry_id']==str(other.pk)

def test_validate_entry_endpoint_reports_other_faculty_entry(client,data):
    _,other_offering=_other_section_and_offering(data)
    other=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=other_offering,weekday=0,start_slot=data['slots'][1],room=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    ScheduleEntryFaculty.objects.create(schedule_entry=other,faculty=data['faculty'])
    entry,_=_update_entry(data,room=Room.objects.create(code='R103',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    response=client.post(f'/api/versions/{data["version"].pk}/validate-entry/',_validate_entry_payload(data,entry,start_slot=data['slots'][1]),format='json')
    clashes=[item for item in response.data['conflicts'] if item['type']=='FACULTY_CLASH']
    assert response.status_code==200 and clashes and clashes[0]['conflicting_entry_id']==str(other.pk)

def test_validate_entry_endpoint_reports_other_room_entry(client,data):
    _,other_offering=_other_section_and_offering(data)
    occupied=Room.objects.create(code='R102',building='Main',floor='1',capacity=60,room_type='CLASSROOM')
    other=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=other_offering,weekday=0,start_slot=data['slots'][1],room=occupied)
    entry,_=_update_entry(data,room=Room.objects.create(code='R103',building='Main',floor='1',capacity=60,room_type='CLASSROOM'))
    response=client.post(f'/api/versions/{data["version"].pk}/validate-entry/',_validate_entry_payload(data,entry,start_slot=data['slots'][1],room=occupied),format='json')
    clashes=[item for item in response.data['conflicts'] if item['type']=='ROOM_CLASH']
    assert response.status_code==200 and clashes and clashes[0]['conflicting_entry_id']==str(other.pk)

def timetable_payload(d, **over):
    return {'institution':str(d['institution'].pk),'title':'Generated Timetable','academic_session':str(d['session'].pk),'semester':str(d['semester'].pk),'department':str(d['department'].pk),'effective_date':'2027-07-01',**over}

def test_timetable_create_assigns_authenticated_creator_and_version(client,data):
    r=client.post('/api/timetables/',timetable_payload(data),format='json')
    assert r.status_code==201
    created=Timetable.objects.get(pk=r.data['id'])
    assert created.created_by_id==data['admin'].id
    assert TimetableVersion.objects.filter(timetable=created,version_no=1,status='DRAFT',created_by=data['admin']).exists()

def test_timetable_created_by_is_read_only(client,data):
    other=User.objects.create_user('other@test.local','Pass12345!',role=Role.TIMETABLE_COORDINATOR)
    r=client.post('/api/timetables/',timetable_payload(data,created_by=str(other.pk)),format='json')
    assert r.status_code==201
    assert Timetable.objects.get(pk=r.data['id']).created_by_id==data['admin'].id

def test_timetable_create_requires_authentication(data):
    c=APIClient();assert c.post('/api/timetables/',timetable_payload(data),format='json').status_code in (401,403)

def test_timetable_create_denies_read_only_role(data):
    u=User.objects.create_user('viewer-timetable@test.local','Pass12345!',role=Role.READ_ONLY_VIEWER);c=APIClient();c.force_authenticate(u)
    assert c.post('/api/timetables/',timetable_payload(data),format='json').status_code==403

def test_solver_default_requirements_without_faculty_reports_diagnostic(data):
    requirements,errors=build_requirements(data['version'],{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)]})
    assert requirements==[]
    assert errors[0]['code']=='NO_FACULTY_ASSIGNMENT'

def test_solver_explicit_session_pattern_validation(data):
    requirements,errors=build_requirements(data['version'],{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}],'session_lengths':[2]}]})
    assert not requirements and errors[0]['code']=='INVALID_SESSION_PATTERN'

def test_solver_resolves_consecutive_slots(data):
    grouped={str(data['slots'][0].template_id):data['slots']}
    assert [x.id for x in resolve_slots(data['slots'][0],2,grouped)]==[data['slots'][0].id,data['slots'][1].id]

def test_solver_rejects_break_crossing(data):
    data['slots'][1].is_break=True;data['slots'][1].save();grouped={str(data['slots'][0].template_id):list(data['slots'])}
    assert resolve_slots(data['slots'][0],2,grouped)==[]

def test_solver_rebuild_unlocked_ignores_replaceable_entry(data):
    e=ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'],locked=False)
    requirements,errors=build_requirements(data['version'],{'mode':'REBUILD_UNLOCKED','section_ids':[str(data['section'].pk)],'offering_rules':[{'course_offering_id':str(data['offering'].pk),'faculty':[{'faculty_id':str(data['faculty'].pk)}]}]})
    assert errors==[] and sum(x.block_length for x in requirements)==data['offering'].weekly_periods

def test_solver_fixed_demand_excess_diagnostic(data):
    data['offering'].weekly_periods=1;data['offering'].save();ScheduleEntry.objects.create(version=data['version'],section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'],block_length=2)
    _,errors=build_requirements(data['version'],{'mode':'FILL_GAPS','section_ids':[str(data['section'].pk)]})
    assert errors[0]['code']=='FIXED_PERIODS_EXCEED_REQUIREMENT'

def test_solver_objective_weights_are_bounded():
    values=weights({'soft_constraints':{'spread_course_days':999,'balance_section_load':-5}})
    assert values['spread_course_days']==100 and values['balance_section_load']==0

def test_solver_objective_defaults_are_centralized():
    values=weights({})
    assert set(values)=={'spread_course_days','balance_section_load','minimize_section_gaps','minimize_faculty_gaps','preserve_existing'}

def test_section_gap_ignores_leading_and_trailing_free_slots():
    assert internal_gap_count([1,3])==1
    assert internal_gap_count([3,4])==0

def test_gap_objective_ignores_configured_breaks():
    assert internal_gap_count([1,3],[2])==0

def test_multi_period_occupancy_has_no_internal_gap():
    assert internal_gap_count([1,2,3])==0

def test_create_draft_from_published_copies_entries_and_faculty(client,data):
    source=data['version']; source.status='PUBLISHED'; source.save(update_fields=['status'])
    e=ScheduleEntry.objects.create(version=source,section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'],locked=True)
    ScheduleEntryFaculty.objects.create(schedule_entry=e,faculty=data['faculty'],role='PRIMARY')
    response=client.post(f'/api/versions/{source.pk}/create-draft/')
    assert response.status_code==201, response.data
    draft=TimetableVersion.objects.get(pk=response.data['id'])
    assert draft.status=='DRAFT' and draft.previous_version_id==source.pk and source.status=='PUBLISHED'
    copied=ScheduleEntry.objects.get(version=draft); assert copied.section_id==e.section_id and copied.course_offering_id==e.course_offering_id and copied.start_slot_id==e.start_slot_id and copied.room_id==e.room_id and copied.locked
    assignment=ScheduleEntryFaculty.objects.get(schedule_entry=copied); assert assignment.faculty_id==data['faculty'].pk and assignment.role=='PRIMARY'

def test_create_draft_uses_max_version_and_rejects_unpublished(client,data):
    source=data['version']; source.status='PUBLISHED'; source.version_no=5; source.save(update_fields=['status','version_no'])
    TimetableVersion.objects.create(timetable=source.timetable,version_no=7,created_by=source.created_by)
    response=client.post(f'/api/versions/{source.pk}/create-draft/')
    assert response.status_code==201 and response.data['version_no']==8
    draft=TimetableVersion.objects.filter(timetable=source.timetable).order_by('-version_no').first(); draft.status='DRAFT'; draft.save(update_fields=['status'])
    assert client.post(f'/api/versions/{draft.pk}/create-draft/').status_code==400

def test_create_draft_rbac_and_compare_and_history(client,data):
    source=data['version']; source.status='PUBLISHED'; source.save(update_fields=['status'])
    created=client.post(f'/api/versions/{source.pk}/create-draft/'); assert created.status_code==201
    draft=TimetableVersion.objects.get(pk=created.data['id'])
    compared=client.get(f'/api/versions/compare/?from={source.pk}&to={draft.pk}'); assert compared.status_code==200 and compared.data['summary']['added']==0
    history=client.get(f'/api/versions/{draft.pk}/history/'); assert history.status_code==200 and any(x['event_type']=='TIMETABLE_DRAFT_CREATED' for x in history.data)

@pytest.mark.parametrize('state', ['DRAFT','IN_REVIEW','APPROVED','ARCHIVED'])
def test_create_draft_rejects_every_non_published_status(client,data,state):
    source=data['version']; source.status=state; source.save(update_fields=['status']); before=TimetableVersion.objects.filter(timetable=source.timetable).count()
    response=client.post(f'/api/versions/{source.pk}/create-draft/')
    assert response.status_code==400 and TimetableVersion.objects.filter(timetable=source.timetable).count()==before

def test_create_draft_rbac_rejects_faculty_and_reviewer(client,data):
    source=data['version']; source.status='PUBLISHED'; source.save(update_fields=['status'])
    for index,role in enumerate((Role.FACULTY,Role.HOD_OR_DEAN_APPROVER,Role.READ_ONLY_VIEWER)):
        user=User.objects.create_user(f'{role.lower()}{index}@test.local','Pass12345!',role=role); client.force_authenticate(user)
        assert client.post(f'/api/versions/{source.pk}/create-draft/').status_code==403
    client.force_authenticate(data['admin'])

def test_compare_detects_added_and_removed_entries(client,data):
    source=data['version']; source.status='PUBLISHED'; source.save(update_fields=['status'])
    entry=ScheduleEntry.objects.create(version=source,section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'])
    draft=TimetableVersion.objects.create(timetable=source.timetable,version_no=2,created_by=data['admin'],previous_version=source)
    result=client.get(f'/api/versions/compare/?from={source.pk}&to={draft.pk}'); assert result.data['summary']['removed']==1
    ScheduleEntry.objects.create(version=draft,section=data['section'],course_offering=data['offering'],weekday=0,start_slot=data['slots'][0],room=data['room'])
    result=client.get(f'/api/versions/compare/?from={source.pk}&to={draft.pk}'); assert result.data['summary']['added']==0 and result.data['summary']['removed']==0

def test_compare_and_history_are_not_available_to_faculty(client,data):
    source=data['version']; source.status='PUBLISHED'; source.save(update_fields=['status']); user=data['faculty'].user; client.force_authenticate(user)
    assert client.get(f'/api/versions/compare/?from={source.pk}&to={source.pk}').status_code==403
    assert client.get(f'/api/versions/{source.pk}/history/').status_code==403

def _entry(version, d, **over):
    values={'version':version,'section':d['section'],'course_offering':d['offering'],'weekday':0,'start_slot':d['slots'][0],'room':d['room']}
    values.update(over)
    return ScheduleEntry.objects.create(**values)

def test_lifecycle_history_records_submit_and_rejection_reason(client,data):
    entry=_entry(data['version'],data); ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'])
    assert client.post(f'/api/versions/{data["version"].pk}/submit-for-approval/').status_code==200
    approver=User.objects.create_user('approver@test.local','Pass12345!',role=Role.HOD_OR_DEAN_APPROVER); client.force_authenticate(approver)
    assert client.post(f'/api/versions/{data["version"].pk}/reject/',{'reason':'Room allocation needs correction'},format='json').status_code==200
    history=client.get(f'/api/versions/{data["version"].pk}/audit/'); assert history.status_code==200
    rejected=next(item for item in history.data if item['event_type']=='TIMETABLE_REJECTED')
    assert rejected['metadata']['reason']=='Room allocation needs correction'
    assert rejected['actor_name']=='approver@test.local'

def test_lifecycle_history_records_approval_and_publish(client,data):
    entry=_entry(data['version'],data); ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'])
    assert client.post(f'/api/versions/{data["version"].pk}/submit-for-approval/').status_code==200
    approver=User.objects.create_user('approver2@test.local','Pass12345!',role=Role.ACADEMIC_ADMIN); client.force_authenticate(approver)
    assert client.post(f'/api/versions/{data["version"].pk}/approve/').status_code==200
    assert client.post(f'/api/versions/{data["version"].pk}/publish/').status_code==200
    event_types={item['event_type'] for item in client.get(f'/api/versions/{data["version"].pk}/audit/').data}
    assert {'TIMETABLE_SUBMITTED','TIMETABLE_APPROVED','TIMETABLE_PUBLISHED'} <= event_types

def test_publish_archives_previous_version_and_records_archive_event(client,data):
    old=data['version']; old.status='PUBLISHED'; old.save(update_fields=['status'])
    newer=TimetableVersion.objects.create(timetable=data['timetable'],version_no=2,created_by=data['admin'])
    entry=_entry(newer,data); ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'])
    assert client.post(f'/api/versions/{newer.pk}/submit-for-approval/').status_code==200
    approver=User.objects.create_user('admin2@test.local','Pass12345!',role=Role.ACADEMIC_ADMIN); client.force_authenticate(approver)
    assert client.post(f'/api/versions/{newer.pk}/approve/').status_code==200
    assert client.post(f'/api/versions/{newer.pk}/publish/').status_code==200
    old.refresh_from_db(); newer.refresh_from_db(); assert old.status=='ARCHIVED' and newer.status=='PUBLISHED'
    assert any(item['event_type']=='TIMETABLE_ARCHIVED' for item in client.get(f'/api/versions/{old.pk}/audit/').data)

def test_draft_created_history_contains_lineage_metadata(client,data):
    source=data['version']; source.status='PUBLISHED'; source.save(update_fields=['status'])
    response=client.post(f'/api/versions/{source.pk}/create-draft/'); draft=response.data['id']
    event=next(item for item in client.get(f'/api/versions/{draft}/audit/').data if item['event_type']=='TIMETABLE_DRAFT_CREATED')
    assert event['metadata']['source_version_id']==str(source.pk) and event['metadata']['new_version_id']==draft

def test_compare_detects_room_time_duration_and_faculty_mutations(client,data):
    source=data['version']; source.status='PUBLISHED'; source.save(update_fields=['status'])
    source_entry=_entry(source,data); ScheduleEntryFaculty.objects.create(schedule_entry=source_entry,faculty=data['faculty'])
    room2=Room.objects.create(code='R102',building='Main',floor='1',capacity=50,room_type='CLASSROOM')
    faculty2=Faculty.objects.create(user=None,employee_code='F002',initials='UF',department=data['department'])
    target=TimetableVersion.objects.create(timetable=data['timetable'],version_no=2,created_by=data['admin'])
    target_entry=_entry(target,data,weekday=1,start_slot=data['slots'][1],room=room2,block_length=2)
    ScheduleEntryFaculty.objects.create(schedule_entry=target_entry,faculty=faculty2)
    result=client.get(f'/api/versions/compare/?from={source.pk}&to={target.pk}')
    assert result.status_code==200 and result.data['summary']=={'added':0,'removed':0,'changed':1}
    changed=result.data['changed'][0]; assert changed['before']['room']!=changed['after']['room']; assert changed['before']['start_slot']!=changed['after']['start_slot']; assert changed['before']['block_length']!=changed['after']['block_length']; assert changed['before']['faculty']!=changed['after']['faculty']

@pytest.mark.parametrize('mutation',[
    {'room':'other'}, {'weekday':1}, {'start_slot':'other'}, {'block_length':2},
])
def test_publish_diff_classifies_single_logical_change(data,mutation):
    old=data['version']; old.status='PUBLISHED';old.save(update_fields=['status']);e=_entry(old,data);ScheduleEntryFaculty.objects.create(schedule_entry=e,faculty=data['faculty'])
    target=TimetableVersion.objects.create(timetable=data['timetable'],version_no=2,created_by=data['admin'])
    values={'weekday':0,'start_slot':data['slots'][0],'room':data['room'],'block_length':1}
    if mutation.get('room')=='other': values['room']=Room.objects.create(code='R405',building='Main',floor='1',capacity=50,room_type='CLASSROOM')
    if mutation.get('weekday'): values['weekday']=mutation['weekday']
    if mutation.get('start_slot')=='other': values['start_slot']=data['slots'][1]
    if mutation.get('block_length'): values['block_length']=mutation['block_length']
    ne=_entry(target,data,**values);ScheduleEntryFaculty.objects.create(schedule_entry=ne,faculty=data['faculty']);changes,grouped=publish_diff(old,target)
    assert len(changes)==1 and changes[0]['type']=='CHANGED' and str(data['faculty'].pk) in grouped

def test_publish_diff_identical_clones_added_removed_and_reassignment(data):
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);e=_entry(old,data);ScheduleEntryFaculty.objects.create(schedule_entry=e,faculty=data['faculty'])
    same=TimetableVersion.objects.create(timetable=data['timetable'],version_no=2,created_by=data['admin']);se=_entry(same,data);ScheduleEntryFaculty.objects.create(schedule_entry=se,faculty=data['faculty']);assert publish_diff(old,same)==([],{})
    added=TimetableVersion.objects.create(timetable=data['timetable'],version_no=3,created_by=data['admin']);ae=_entry(added,data);ScheduleEntryFaculty.objects.create(schedule_entry=ae,faculty=data['faculty']);second=Course.objects.create(code='CS102',name='Data Structures',short_code='DS',credit=3);off=CourseOffering.objects.create(semester=data['semester'],section=data['section'],course=second,weekly_periods=1);extra=_entry(added,data,course_offering=off);ScheduleEntryFaculty.objects.create(schedule_entry=extra,faculty=data['faculty']);changes,_=publish_diff(same,added);assert any(x['type']=='ADDED' for x in changes)
    removed=TimetableVersion.objects.create(timetable=data['timetable'],version_no=4,created_by=data['admin']);re=_entry(removed,data);ScheduleEntryFaculty.objects.create(schedule_entry=re,faculty=data['faculty']);changes,_=publish_diff(added,removed);assert any(x['type']=='REMOVED' for x in changes)
    reassigned=TimetableVersion.objects.create(timetable=data['timetable'],version_no=5,created_by=data['admin']);rf=Faculty.objects.create(user=None,employee_code='F009',initials='R',department=data['department']);rentry=_entry(reassigned,data);ScheduleEntryFaculty.objects.create(schedule_entry=rentry,faculty=rf);changes,grouped=publish_diff(old,reassigned);assert {x['type'] for x in changes}=={'ADDED','REMOVED'} and str(data['faculty'].pk) in grouped and str(rf.pk) in grouped

def test_publish_diff_consolidates_multiple_changes_for_one_faculty(data):
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);first=_entry(old,data);ScheduleEntryFaculty.objects.create(schedule_entry=first,faculty=data['faculty']);target=TimetableVersion.objects.create(timetable=data['timetable'],version_no=2,created_by=data['admin']);updated=_entry(target,data,room=Room.objects.create(code='R406',building='Main',floor='1',capacity=50,room_type='CLASSROOM'));ScheduleEntryFaculty.objects.create(schedule_entry=updated,faculty=data['faculty']);changes,grouped=publish_diff(old,target);assert len(changes)==1 and len(grouped[str(data['faculty'].pk)])==1

def test_real_publish_api_creates_one_faculty_publication_notification(client,data,django_capture_on_commit_callbacks):
    entry=_entry(data['version'],data);ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty']);NotificationPreference.objects.update_or_create(user=data['faculty'].user,defaults={'email_enabled':False,'whatsapp_enabled':False})
    assert client.post(f'/api/versions/{data["version"].pk}/submit-for-approval/').status_code==200
    reviewer=User.objects.create_user('publish-reviewer@test.local','Pass12345!',role=Role.ACADEMIC_ADMIN);client.force_authenticate(reviewer)
    assert client.post(f'/api/versions/{data["version"].pk}/approve/').status_code==200
    with django_capture_on_commit_callbacks(execute=True):
        assert client.post(f'/api/versions/{data["version"].pk}/publish/').status_code==200
    data['version'].refresh_from_db();assert data['version'].status=='PUBLISHED'
    assert Notification.objects.filter(recipient=data['faculty'].user,event_type='TIMETABLE_PUBLISHED').count()==1
    notification=Notification.objects.get(recipient=data['faculty'].user,event_type='TIMETABLE_PUBLISHED');assert notification.action_url=='/my-timetable'

def _notification_version(data,number,status='DRAFT'):
    return TimetableVersion.objects.create(timetable=data['timetable'],version_no=number,status=status,created_by=data['admin'])

def _notification_entry(version,data,faculty=None,**values):
    entry=_entry(version,data,**values);ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=faculty or data['faculty']);return entry

def _notification_reviewer(data):
    return User.objects.create_user(f'reviewer-{User.objects.count()}@test.local','Pass12345!',role=Role.ACADEMIC_ADMIN)

def _publish_through_lifecycle(client,data,version,django_capture_on_commit_callbacks):
    client.force_authenticate(data['admin']);assert client.post(f'/api/versions/{version.pk}/submit-for-approval/').status_code==200
    reviewer=_notification_reviewer(data);client.force_authenticate(reviewer);assert client.post(f'/api/versions/{version.pk}/approve/').status_code==200
    with django_capture_on_commit_callbacks(execute=True): response=client.post(f'/api/versions/{version.pk}/publish/')
    assert response.status_code==200;version.refresh_from_db();assert version.status=='PUBLISHED';return reviewer

def _enable_faculty_channels(user):
    user.whatsapp_number='+919876543210';user.save(update_fields=['whatsapp_number'])
    NotificationPreference.objects.update_or_create(user=user,defaults={'email_enabled':True,'whatsapp_enabled':True,'whatsapp_opt_in':True})

def _official_notifications(user,event): return Notification.objects.filter(recipient=user,event_type=event)

def test_publish_api_first_publish_consolidates_multiple_entries_and_channels(client,data,settings,django_capture_on_commit_callbacks):
    _notification_entry(data['version'],data);second=Course.objects.create(code='CS102',name='Networks',short_code='CN',credit=3);offering=CourseOffering.objects.create(semester=data['semester'],section=data['section'],course=second,weekly_periods=1);_notification_entry(data['version'],data,course_offering=offering,start_slot=data['slots'][1])
    _enable_faculty_channels(data['faculty'].user);settings.WHATSAPP_ENABLED=True
    with patch('notifications.services.send_delivery'),patch('notifications.services.send_whatsapp'):
        _publish_through_lifecycle(client,data,data['version'],django_capture_on_commit_callbacks)
    qs=_official_notifications(data['faculty'].user,'TIMETABLE_PUBLISHED');assert qs.count()==1
    notification=qs.get();assert notification.action_url=='/my-timetable' and len(notification.metadata['changes'])==2
    assert NotificationDelivery.objects.filter(notification=notification,channel='EMAIL').count()==1
    assert NotificationDelivery.objects.filter(notification=notification,channel='WHATSAPP').count()==1
    client.force_authenticate(data['faculty'].user);response=client.get('/api/me/faculty-timetable/');assert response.status_code==200 and response.data['version']['status']=='PUBLISHED'

def test_publish_api_identical_republish_archives_old_without_update(client,data,django_capture_on_commit_callbacks):
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);_notification_entry(old,data)
    new=_notification_version(data,2);_notification_entry(new,data);_enable_faculty_channels(data['faculty'].user)
    _publish_through_lifecycle(client,data,new,django_capture_on_commit_callbacks);old.refresh_from_db();assert old.status=='ARCHIVED'
    assert _official_notifications(data['faculty'].user,'TIMETABLE_UPDATED').count()==0
    assert NotificationDelivery.objects.filter(notification__recipient=data['faculty'].user,notification__event_type='TIMETABLE_UPDATED').count()==0

def test_publish_api_notifies_only_affected_faculty_with_room_metadata(client,data,settings,django_capture_on_commit_callbacks):
    user_b=User.objects.create_user('faculty-b@test.local','Pass12345!',role=Role.FACULTY);faculty_b=Faculty.objects.create(user=user_b,employee_code='F002',initials='FB',department=data['department']);_enable_faculty_channels(user_b);_enable_faculty_channels(data['faculty'].user);settings.WHATSAPP_ENABLED=True
    other_course=Course.objects.create(code='CS102',name='Networks',short_code='CN',credit=3);other=CourseOffering.objects.create(semester=data['semester'],section=data['section'],course=other_course,weekly_periods=1)
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);_notification_entry(old,data);_notification_entry(old,data,faculty_b,course_offering=other,start_slot=data['slots'][1])
    new=_notification_version(data,2);_notification_entry(new,data);room405=Room.objects.create(code='405',building='Main',floor='4',capacity=50,room_type='CLASSROOM');_notification_entry(new,data,faculty_b,course_offering=other,start_slot=data['slots'][1],room=room405)
    with patch('notifications.services.send_delivery'),patch('notifications.services.send_whatsapp'):
        _publish_through_lifecycle(client,data,new,django_capture_on_commit_callbacks)
    assert _official_notifications(data['faculty'].user,'TIMETABLE_UPDATED').count()==0
    qs=_official_notifications(user_b,'TIMETABLE_UPDATED');assert qs.count()==1;notification=qs.get();change=notification.metadata['changes'][0]
    assert change['old']['room_label']=='R101' and change['new']['room_label']=='405'
    assert NotificationDelivery.objects.filter(notification=notification,channel='EMAIL').count()==1 and NotificationDelivery.objects.filter(notification=notification,channel='WHATSAPP').count()==1

def test_publish_api_multiple_changes_are_one_faculty_notification(client,data,settings,django_capture_on_commit_callbacks):
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);_notification_entry(old,data)
    extra_course=Course.objects.create(code='CS102',name='Networks',short_code='CN',credit=3);extra=CourseOffering.objects.create(semester=data['semester'],section=data['section'],course=extra_course,weekly_periods=1);_notification_entry(old,data,course_offering=extra,start_slot=data['slots'][1])
    new=_notification_version(data,2);room=Room.objects.create(code='405',building='Main',floor='4',capacity=50,room_type='CLASSROOM');_notification_entry(new,data,room=room,start_slot=data['slots'][2]);added_course=Course.objects.create(code='CS103',name='AI',short_code='AI',credit=3);added=CourseOffering.objects.create(semester=data['semester'],section=data['section'],course=added_course,weekly_periods=1);_notification_entry(new,data,course_offering=added,start_slot=data['slots'][3])
    _enable_faculty_channels(data['faculty'].user);settings.WHATSAPP_ENABLED=True
    with patch('notifications.services.send_delivery'),patch('notifications.services.send_whatsapp'):
        _publish_through_lifecycle(client,data,new,django_capture_on_commit_callbacks)
    qs=_official_notifications(data['faculty'].user,'TIMETABLE_UPDATED');assert qs.count()==1;notification=qs.get();types={item['type'] for item in notification.metadata['changes']};assert types=={'CHANGED','REMOVED','ADDED'}
    assert NotificationDelivery.objects.filter(notification=notification,channel='EMAIL').count()==1 and NotificationDelivery.objects.filter(notification=notification,channel='WHATSAPP').count()==1

@pytest.mark.parametrize(('old_has','new_has','expected'),[(False,True,'ADDED'),(True,False,'REMOVED')])
def test_publish_api_added_and_removed_assignment_metadata(client,data,django_capture_on_commit_callbacks,old_has,new_has,expected):
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);new=_notification_version(data,2)
    if old_has:_notification_entry(old,data)
    if new_has:_notification_entry(new,data)
    if not old.entries.exists():
        spare=Faculty.objects.create(employee_code='SPARE',initials='SP',department=data['department']);_notification_entry(old,data,spare)
    if not new.entries.exists():
        spare=Faculty.objects.create(employee_code='SPARE2',initials='SP2',department=data['department']);_notification_entry(new,data,spare)
    NotificationPreference.objects.update_or_create(user=data['faculty'].user,defaults={'email_enabled':False,'whatsapp_enabled':False})
    _publish_through_lifecycle(client,data,new,django_capture_on_commit_callbacks)
    notification=_official_notifications(data['faculty'].user,'TIMETABLE_UPDATED').get();change=notification.metadata['changes'][0];assert change['type']==expected
    if expected=='REMOVED': assert change['old']['room_label']=='R101' and change['old']['start_label']==data['slots'][0].label

def test_publish_api_faculty_reassignment_notifies_old_and_new_once(client,data,django_capture_on_commit_callbacks):
    user_b=User.objects.create_user('reassigned@test.local','Pass12345!',role=Role.FACULTY);faculty_b=Faculty.objects.create(user=user_b,employee_code='F003',initials='RB',department=data['department'])
    for user in (data['faculty'].user,user_b):NotificationPreference.objects.update_or_create(user=user,defaults={'email_enabled':False,'whatsapp_enabled':False})
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);_notification_entry(old,data);new=_notification_version(data,2);_notification_entry(new,data,faculty_b)
    _publish_through_lifecycle(client,data,new,django_capture_on_commit_callbacks)
    old_n=_official_notifications(data['faculty'].user,'TIMETABLE_UPDATED');new_n=_official_notifications(user_b,'TIMETABLE_UPDATED');assert old_n.count()==new_n.count()==1
    assert old_n.get().metadata['changes'][0]['type']=='REMOVED' and new_n.get().metadata['changes'][0]['type']=='ADDED'

def test_unpublished_lifecycle_states_never_send_official_faculty_updates(client,data):
    _notification_entry(data['version'],data);user=data['faculty'].user
    assert _official_notifications(user,'TIMETABLE_PUBLISHED').count()==0 and _official_notifications(user,'TIMETABLE_UPDATED').count()==0
    assert client.post(f'/api/versions/{data["version"].pk}/submit-for-approval/').status_code==200
    assert not _official_notifications(user,'TIMETABLE_PUBLISHED').exists() and not _official_notifications(user,'TIMETABLE_UPDATED').exists()
    reviewer=_notification_reviewer(data);client.force_authenticate(reviewer);assert client.post(f'/api/versions/{data["version"].pk}/approve/').status_code==200
    assert not _official_notifications(user,'TIMETABLE_PUBLISHED').exists() and not _official_notifications(user,'TIMETABLE_UPDATED').exists()
    assert not NotificationDelivery.objects.filter(notification__recipient=user,notification__event_type__in=['TIMETABLE_PUBLISHED','TIMETABLE_UPDATED']).exists()

def test_provider_failure_during_real_publish_does_not_rollback_lifecycle(client,data,settings,django_capture_on_commit_callbacks):
    old=data['version'];old.status='PUBLISHED';old.save(update_fields=['status']);_notification_entry(old,data);new=_notification_version(data,2);room=Room.objects.create(code='405',building='Main',floor='4',capacity=50,room_type='CLASSROOM');_notification_entry(new,data,room=room)
    _enable_faculty_channels(data['faculty'].user);settings.WHATSAPP_ENABLED=True;settings.WHATSAPP_ACCESS_TOKEN='secret-token';settings.WHATSAPP_PHONE_NUMBER_ID='phone-id';settings.WHATSAPP_TEMPLATE_TIMETABLE_UPDATED='updated'
    with patch('urllib.request.urlopen',side_effect=TimeoutError('provider unavailable')):
        _publish_through_lifecycle(client,data,new,django_capture_on_commit_callbacks)
    old.refresh_from_db();new.refresh_from_db();assert old.status=='ARCHIVED' and new.status=='PUBLISHED'
    notification=_official_notifications(data['faculty'].user,'TIMETABLE_UPDATED').get();delivery=NotificationDelivery.objects.get(notification=notification,channel='WHATSAPP');assert delivery.status=='FAILED' and delivery.attempts==1

def test_reports_are_derived_from_selected_version_and_filters(client,data):
    entry=_entry(data['version'],data);ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'])
    query=f'?version={data["version"].pk}'
    workload=client.get('/api/reports/faculty-workload/'+query);rooms=client.get('/api/reports/room-utilization/'+query);section=client.get('/api/reports/section-timetable/'+query);allocation=client.get('/api/reports/course-allocation/'+query)
    assert workload.status_code==rooms.status_code==section.status_code==allocation.status_code==200
    assert workload.data[0]['total_scheduled_periods']==1 and rooms.data[0]['occupied_slots']==1 and section.data[0]['course_code']=='CS101' and allocation.data[0]['scheduled_periods']==1

def test_report_exports_return_csv_and_xlsx(client,data):
    entry=_entry(data['version'],data);ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty']);params={'version':str(data['version'].pk)}
    csv_response=client.get('/api/reports/section-timetable/',{**params,'export':'csv'});xlsx_response=client.get('/api/reports/section-timetable/',{**params,'export':'xlsx'})
    assert csv_response.status_code==xlsx_response.status_code==200 and csv_response['Content-Type'].startswith('text/csv') and xlsx_response['Content-Type'].startswith('application/vnd.openxmlformats') and b'CS101' in csv_response.content and len(xlsx_response.content)>100

def test_faculty_report_isolation_and_analytics(client,data):
    entry=_entry(data['version'],data);ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty']);other=User.objects.create_user('report-other@test.local','Pass12345!',role=Role.FACULTY);other_faculty=Faculty.objects.create(user=other,employee_code='F002',initials='OF',department=data['department'])
    client.force_authenticate(data['faculty'].user);published_only=client.get(f'/api/reports/faculty-timetable/?version={data["version"].pk}&faculty={data["faculty"].pk}');assert published_only.status_code==200 and published_only.data==[];assert client.get(f'/api/reports/faculty-timetable/?version={data["version"].pk}&faculty={other_faculty.pk}').status_code==200 and client.get(f'/api/reports/faculty-workload/?version={data["version"].pk}&faculty={other_faculty.pk}').status_code==403
    assert client.get('/api/reports/analytics/').status_code==403

def test_report_conflicts_reuses_validator_and_version_activity_is_readable(client,data):
    entry=_entry(data['version'],data);ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty']);other=_entry(data['version'],data,course_offering=data['offering'],start_slot=data['slots'][1]);ScheduleEntryFaculty.objects.create(schedule_entry=other,faculty=data['faculty'])
    assert client.get(f'/api/reports/conflicts/?version={data["version"].pk}').status_code==200 and client.get('/api/reports/version-activity/').status_code==200

def test_report_filter_health_and_openapi_contract(client,data):
    entry=_entry(data['version'],data);ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=data['faculty'])
    response=client.get(f'/api/reports/section-timetable/?version={data["version"].pk}&section={data["section"].pk}')
    assert response.status_code==200 and len(response.data)==1 and response.data[0]['section_id']==str(data['section'].pk)
    assert client.get('/api/health/').status_code==200 and client.get('/api/health/').data=={'status':'ok'}
    schema=client.get('/api/schema/'); assert schema.status_code==200
    paths=schema.data['paths']; assert '/api/reports/section-timetable/' in paths and '/api/reports/analytics/' in paths and '/api/health/' in paths
