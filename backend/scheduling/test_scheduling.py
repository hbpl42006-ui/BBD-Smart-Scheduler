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
from scheduling.solver.engine import Requirement,FacultyAssignment,resolve_slots,build_requirements,build_candidates
from scheduling.solver.objective import weights,internal_gap_count

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
