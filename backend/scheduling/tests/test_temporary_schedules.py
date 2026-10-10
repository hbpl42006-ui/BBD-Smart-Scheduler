from datetime import date, time

import pytest
from rest_framework.test import APIClient

from accounts.models import Role, User
from academics.models import AcademicSession, Course, CourseOffering, Program, Section, Semester
from common.models import TimeSlot, TimeSlotTemplate
from faculty.models import Faculty, FacultyAvailability
from institutions.models import Department, Institution
from rooms.models import Room, RoomAvailability
from scheduling.models import (ScheduleEntry, ScheduleEntryFaculty, TemporaryScheduleBlock,
                               TemporarySchedulePlan, Timetable, TimetableVersion)
from scheduling.services.temporary_schedules import resolve_effective_schedule, validate_temporary_plan, plan_impact


@pytest.fixture
def temporary_data(db):
    institution=Institution.objects.create(name='Temp University',code='TU')
    department=Department.objects.create(institution=institution,name='Computing',code='TC')
    program=Program.objects.create(department=department,name='Computing',code='TCP',duration_years=4)
    session=AcademicSession.objects.create(institution=institution,name='2026-27',start_date=date(2026,7,1),end_date=date(2027,6,30))
    semester=Semester.objects.create(session=session,name='Odd',number=1,type='ODD',start_date=date(2026,7,1),end_date=date(2026,12,31))
    section=Section.objects.create(program=program,semester=semester,year=1,name='A')
    course=Course.objects.create(code='TMP101',name='Base class',short_code='TB',credit=3)
    offering=CourseOffering.objects.create(semester=semester,section=section,course=course,weekly_periods=2,default_class_type='LECTURE',required_block_size=1)
    admin=User.objects.create_user('temp-admin@test.local','pass',role=Role.SUPER_ADMIN)
    faculty=Faculty.objects.create(name='Trainer One',employee_code='T-1',department=department)
    room=Room.objects.create(code='T-101',building='Main',floor='1',capacity=50,room_type='CLASSROOM')
    template=TimeSlotTemplate.objects.create(institution=institution,name='Temp slots')
    hours=[9,10,11,12,13,14,15,16]
    slots=[TimeSlot.objects.create(template=template,label=f'{hour:02}:00-{hour+1:02}:00',start_time=time(hour,0),end_time=time(hour+1,0),order=index+1,is_break=hour==13) for index,hour in enumerate(hours)]
    version=TimetableVersion.objects.create(timetable=Timetable.objects.create(institution=institution,academic_session=session,semester=semester,department=department,title='Temp',created_by=admin),version_no=1,status='PUBLISHED',created_by=admin)
    entry=ScheduleEntry.objects.create(version=version,section=section,course_offering=offering,weekday=0,start_slot=slots[0],room=room)
    ScheduleEntryFaculty.objects.create(schedule_entry=entry,faculty=faculty)
    return locals()


@pytest.mark.django_db
def test_recurring_overlay_override_and_date_fallback(temporary_data):
    d=temporary_data
    plan=TemporarySchedulePlan.objects.create(base_timetable_version=d['version'],title='Workshop',event_type='WORKSHOP',start_date=date(2026,10,5),end_date=date(2026,10,16),created_by=d['admin'],status='PUBLISHED')
    plan.sections.add(d['section'])
    block=TemporaryScheduleBlock.objects.create(plan=plan,section=d['section'],weekday=0,start_slot=d['slots'][0],block_length=1,event_type='WORKSHOP',title='Special workshop',room=d['room'],override_regular_class=True)
    block.faculty.add(d['faculty'])
    during=resolve_effective_schedule(d['version'],date(2026,10,5),section_id=d['section'].pk)
    assert len(during)==1 and during[0]['is_temporary'] and during[0]['title']=='Special workshop'
    outside=resolve_effective_schedule(d['version'],date(2026,10,19),section_id=d['section'].pk)
    assert len(outside)==1 and not outside[0]['is_temporary']
    assert ScheduleEntry.objects.filter(pk=d['entry'].pk).exists()


@pytest.mark.django_db
def test_specific_date_event_takes_precedence_and_published_effective_reports(temporary_data):
    d=temporary_data
    plan=TemporarySchedulePlan.objects.create(base_timetable_version=d['version'],title='Specials',event_type='OTHER',start_date=date(2026,10,5),end_date=date(2026,10,9),created_by=d['admin'],status='PUBLISHED')
    plan.sections.add(d['section'])
    TemporaryScheduleBlock.objects.create(plan=plan,section=d['section'],weekday=0,start_slot=d['slots'][1],title='Recurring')
    TemporaryScheduleBlock.objects.create(plan=plan,section=d['section'],specific_date=date(2026,10,5),start_slot=d['slots'][1],title='One-off')
    rows=resolve_effective_schedule(d['version'],date(2026,10,5),section_id=d['section'].pk)
    assert [row['title'] for row in rows if row['is_temporary']]==['One-off']
    client=APIClient();client.force_authenticate(d['admin'])
    response=client.get('/api/reports/section-timetable/visual/',{'version':str(d['version'].pk),'section':str(d['section'].pk),'schedule_mode':'EFFECTIVE','date':'2026-10-05'})
    assert response.status_code==200
    assert response.data['summary']['schedule_mode']=='EFFECTIVE'


@pytest.mark.django_db
def test_publish_requires_override_and_keeps_base_immutable(temporary_data):
    d=temporary_data
    client=APIClient();client.force_authenticate(d['admin'])
    plan=TemporarySchedulePlan.objects.create(base_timetable_version=d['version'],title='Conflicting',event_type='TRAINING',start_date=date(2026,10,5),end_date=date(2026,10,5),created_by=d['admin'])
    plan.sections.add(d['section'])
    TemporaryScheduleBlock.objects.create(plan=plan,section=d['section'],specific_date=date(2026,10,5),start_slot=d['slots'][0],title='Training')
    response=client.post(f'/api/temporary-schedules/{plan.pk}/publish/',{},format='json')
    assert response.status_code==400 and response.data['conflicts'][0]['code']=='SECTION_CONFLICT'
    assert ScheduleEntry.objects.filter(pk=d['entry'].pk).exists()


def make_plan(d, *, status='DRAFT', priority=0, end=date(2026,10,12)):
    plan=TemporarySchedulePlan.objects.create(base_timetable_version=d['version'],title='Training',event_type='TRAINING',
        start_date=date(2026,10,5),end_date=end,created_by=d['admin'],status=status,priority=priority)
    plan.sections.add(d['section'])
    return plan


def make_block(d, plan, *, section=None, slot=None, room=None, faculty=None, weekday=0, specific_date=None,
               length=1, override=False):
    block=TemporaryScheduleBlock.objects.create(plan=plan,section=section or d['section'],
        start_slot=slot or d['slots'][0],room=room,weekday=None if specific_date else weekday,
        specific_date=specific_date,block_length=length,title='Training',event_type='TRAINING',
        override_regular_class=override)
    if faculty: block.faculty.add(faculty)
    return block


@pytest.mark.django_db
def test_modes_status_and_date_fallback(temporary_data):
    d=temporary_data; plan=make_plan(d);make_block(d,plan,override=True)
    monday=date(2026,10,5)
    assert len(resolve_effective_schedule(d['version'],monday,mode='regular'))==1
    assert not any(r['is_temporary'] for r in resolve_effective_schedule(d['version'],monday))
    plan.status='PUBLISHED';plan.save()
    assert [r['is_temporary'] for r in resolve_effective_schedule(d['version'],monday)]==[True]
    assert [r['is_temporary'] for r in resolve_effective_schedule(d['version'],monday,mode='special')]==[True]
    assert [r['is_temporary'] for r in resolve_effective_schedule(d['version'],date(2026,10,19))]==[False]
    plan.status='CANCELLED';plan.save()
    assert [r['is_temporary'] for r in resolve_effective_schedule(d['version'],monday)]==[False]


@pytest.mark.django_db
def test_room_and_faculty_filters_are_applied_after_override(temporary_data):
    d=temporary_data;new_room=Room.objects.create(code='T-410',building='New',floor='4',capacity=40,room_type='CLASSROOM')
    plan=make_plan(d,status='PUBLISHED');make_block(d,plan,room=new_room,override=True)
    day=date(2026,10,5)
    assert resolve_effective_schedule(d['version'],day,room_id=d['room'].pk)==[]
    assert resolve_effective_schedule(d['version'],day,faculty_id=d['faculty'].pk)==[]
    assert [r['is_temporary'] for r in resolve_effective_schedule(d['version'],day,room_id=new_room.pk)]==[True]
    client=APIClient();client.force_authenticate(d['admin'])
    free=client.get('/api/reports/free-rooms/',{'schedule_mode':'effective','date':'2026-10-05','time_slot':str(d['slots'][0].pk)})
    assert free.status_code==200
    assert d['room'].code in free.data['audit']['free_codes']
    assert new_room.code in free.data['audit']['occupied_codes']
    later=client.get('/api/reports/free-rooms/',{'schedule_mode':'effective','date':'2026-10-19','time_slot':str(d['slots'][0].pk)})
    assert d['room'].code in later.data['audit']['occupied_codes']
    assert new_room.code in later.data['audit']['free_codes']
    room=client.get('/api/reports/room-timetable/visual/',{'schedule_mode':'effective','date':'2026-10-05','room':str(d['room'].pk)})
    assert room.status_code==200 and room.data['entries']==[]


@pytest.mark.django_db
def test_temporary_section_options_are_complete_and_filter_by_canonical_fields(temporary_data):
    d=temporary_data
    other_program=Program.objects.create(department=d['department'],name='Other Computing',code='TOC',duration_years=4)
    sections=[d['section']]
    for index in range(1,31):
        program=d['program'] if index<=15 else other_program
        sections.append(Section.objects.create(program=program,semester=d['semester'],year=(index-1)%4+1,name=f'Section {index}'))
    client=APIClient();client.force_authenticate(d['admin'])
    response=client.get('/api/temporary-schedules/section-options/')
    assert response.status_code==200
    assert len(response.data)==31
    assert len({row['id'] for row in response.data})==31
    assert any(row['name']=='Section 11' for row in response.data)
    assert {row['year'] for row in response.data}=={1,2,3,4}
    assert len(client.get('/api/temporary-schedules/section-options/',{'program':str(d['program'].pk)}).data)==16
    assert {row['year'] for row in client.get('/api/temporary-schedules/section-options/',{'year':3}).data}=={3}
    combined=client.get('/api/temporary-schedules/section-options/',{'program':str(d['program'].pk),'year':3})
    assert len(combined.data)==4 and all(row['program_id']==str(d['program'].pk) and row['year']==3 for row in combined.data)
    scoped=client.get('/api/temporary-schedules/section-options/',{'version':str(d['version'].pk)})
    assert len(scoped.data)==31 and {row['semester_id'] for row in scoped.data}=={str(d['semester'].pk)}


@pytest.mark.django_db
def test_temporary_plan_accepts_many_section_ids(temporary_data):
    d=temporary_data
    sections=[d['section']]
    for index in range(1,30):
        sections.append(Section.objects.create(program=d['program'],semester=d['semester'],year=index%4+1,name=f'Many {index}'))
    client=APIClient();client.force_authenticate(d['admin'])
    response=client.post('/api/temporary-schedules/',{'title':'Many sections','event_type':'TRAINING',
        'start_date':'2026-10-05','end_date':'2026-10-06','base_timetable_version':str(d['version'].pk),
        'sections':[str(section.pk) for section in sections]},format='json')
    assert response.status_code==201
    assert len(response.data['sections'])==30


@pytest.mark.django_db
def test_three_period_window_break_and_availability(temporary_data):
    d=temporary_data;plan=make_plan(d)
    block=make_block(d,plan,length=3,room=d['room'],faculty=d['faculty'],override=True)
    assert not validate_temporary_plan(plan)
    plan.status='PUBLISHED';plan.save()
    row=resolve_effective_schedule(d['version'],date(2026,10,5))[0]
    assert len(row['occupied_slot_ids'])==3
    assert plan_impact(plan)['affected_regular_periods']==2  # two Mondays in the plan range
    plan.status='DRAFT';plan.save()
    FacultyAvailability.objects.create(faculty=d['faculty'],weekday=0,time_slot=d['slots'][2],is_available=False)
    RoomAvailability.objects.create(room=d['room'],weekday=0,time_slot=d['slots'][2],status='MAINTENANCE')
    codes={e['code'] for e in validate_temporary_plan(plan)}
    assert {'FACULTY_UNAVAILABLE','ROOM_UNAVAILABLE'}<=codes
    d['slots'][1].is_break=True;d['slots'][1].save()
    assert 'INVALID_SLOT_WINDOW' in {e['code'] for e in validate_temporary_plan(plan)}


@pytest.mark.django_db
def test_resource_conflicts_and_same_priority_plan(temporary_data):
    d=temporary_data;plan=make_plan(d)
    room2=Room.objects.create(code='T-102',building='Main',floor='1',capacity=30,room_type='CLASSROOM')
    first=make_block(d,plan,room=room2,faculty=d['faculty'],override=True)
    other_section=Section.objects.create(program=d['program'],semester=d['semester'],year=1,name='B')
    plan.sections.add(other_section)
    make_block(d,plan,section=other_section,room=room2,faculty=d['faculty'])
    codes={e['code'] for e in validate_temporary_plan(plan)}
    assert {'ROOM_CONFLICT','FACULTY_CONFLICT'}<=codes
    plan.blocks.exclude(pk=first.pk).delete()
    second=make_plan(d,status='PUBLISHED');make_block(d,second,room=room2,override=True)
    assert 'SAME_PRIORITY_CONFLICT' in {e['code'] for e in validate_temporary_plan(plan)}


@pytest.mark.django_db
def test_effective_report_modes_and_permissions(temporary_data):
    d=temporary_data;plan=make_plan(d,status='PUBLISHED');block=make_block(d,plan,room=d['room'],faculty=d['faculty'],override=True)
    client=APIClient();client.force_authenticate(d['admin'])
    params={'version':str(d['version'].pk),'date':'2026-10-05','schedule_mode':'special'}
    section=client.get('/api/reports/section-timetable/visual/',{**params,'section':str(d['section'].pk)})
    faculty=client.get('/api/reports/faculty-timetable/visual/',{**params,'faculty':str(d['faculty'].pk)})
    room=client.get('/api/reports/room-timetable/visual/',{**params,'room':str(d['room'].pk)})
    assert all(response.status_code==200 for response in (section,faculty,room))
    assert all(len(response.data['entries'])==1 and response.data['entries'][0]['is_temporary'] for response in (section,faculty,room))
    regular=client.get('/api/reports/section-timetable/visual/',{**params,'section':str(d['section'].pk),'schedule_mode':'regular'})
    assert regular.status_code==200 and not regular.data['entries'][0].get('is_temporary')
    viewer=User.objects.create_user('temp-viewer@test.local','pass',role=Role.READ_ONLY_VIEWER)
    client.force_authenticate(viewer)
    assert client.post('/api/temporary-schedules/',{}).status_code==403
    assert client.post(f'/api/temporary-schedules/{plan.pk}/cancel/',{}).status_code==403
    assert client.delete(f'/api/temporary-schedules/{plan.pk}/blocks/{block.pk}/').status_code==403


@pytest.mark.django_db
def test_regular_room_and_faculty_conflicts_from_other_section(temporary_data):
    d=temporary_data
    section_b=Section.objects.create(program=d['program'],semester=d['semester'],year=1,name='B')
    offering_b=CourseOffering.objects.create(semester=d['semester'],section=section_b,course=d['course'],weekly_periods=1,
        default_class_type='LECTURE',required_block_size=1)
    plan=make_plan(d)
    block=make_block(d,plan,section=section_b,room=d['room'],faculty=d['faculty'])
    plan.sections.add(section_b)
    codes={e['code'] for e in validate_temporary_plan(plan)}
    assert {'ROOM_CONFLICT','FACULTY_CONFLICT'}<=codes
    # A regular class in the same section additionally requires explicit override.
    block.section=d['section'];block.save(update_fields=['section'])
    assert 'SECTION_CONFLICT' in {e['code'] for e in validate_temporary_plan(plan)}


@pytest.mark.django_db
def test_availability_is_date_and_weekday_scoped(temporary_data):
    d=temporary_data;plan=make_plan(d)
    block=make_block(d,plan,weekday=0,room=d['room'],override=True)
    RoomAvailability.objects.create(room=d['room'],weekday=1,time_slot=d['slots'][0],status='BLOCKED')
    assert 'ROOM_UNAVAILABLE' not in {e['code'] for e in validate_temporary_plan(plan)}
    RoomAvailability.objects.create(room=d['room'],date=date(2026,10,5),time_slot=d['slots'][0],status='BLOCKED')
    errors=[e for e in validate_temporary_plan(plan) if e['code']=='ROOM_UNAVAILABLE']
    assert len(errors)==1 and errors[0]['date']=='2026-10-05'


@pytest.mark.django_db
def test_specific_date_then_priority_precedence(temporary_data):
    d=temporary_data
    recurring=make_plan(d,status='PUBLISHED',priority=10)
    make_block(d,recurring,slot=d['slots'][1])
    one_off=make_plan(d,status='PUBLISHED',priority=0)
    make_block(d,one_off,slot=d['slots'][1],specific_date=date(2026,10,5))
    rows=resolve_effective_schedule(d['version'],date(2026,10,5),mode='special')
    assert len(rows)==1 and rows[0]['temporary_plan_id']==str(one_off.pk)
    one_off.blocks.all().delete()
    make_block(d,one_off,slot=d['slots'][1])
    rows=resolve_effective_schedule(d['version'],date(2026,10,5),mode='special')
    assert len(rows)==1 and rows[0]['temporary_plan_id']==str(recurring.pk)
    impact=plan_impact(one_off)
    assert impact['summary']['warnings']==2
    assert all(item['code']=='LOWER_PRIORITY_SUPPRESSED' for item in impact['warnings'])


@pytest.mark.django_db
def test_online_roomless_activity_and_publish_snapshot(temporary_data):
    d=temporary_data;plan=make_plan(d)
    block=make_block(d,plan,slot=d['slots'][1],room=None)
    block.delivery_mode='ONLINE';block.save(update_fields=['delivery_mode'])
    client=APIClient();client.force_authenticate(d['admin'])
    response=client.post(f'/api/temporary-schedules/{plan.pk}/publish/',{},format='json')
    assert response.status_code==200
    plan.refresh_from_db()
    assert plan.published_impact['summary']['temporary_event_instances']==2
    rows=resolve_effective_schedule(d['version'],date(2026,10,5),mode='special')
    assert rows[0]['room_id'] is None and rows[0]['delivery_mode']=='ONLINE'
    assert client.patch(f'/api/temporary-schedules/{plan.pk}/',{'title':'Changed'},format='json').status_code==409
    assert client.post(f'/api/temporary-schedules/{plan.pk}/cancel/',{},format='json').status_code==200
    assert all(not row['is_temporary'] for row in resolve_effective_schedule(d['version'],date(2026,10,5)))


@pytest.mark.django_db
def test_dated_regular_mode_and_invalid_scope(temporary_data):
    d=temporary_data;client=APIClient();client.force_authenticate(d['admin'])
    monday=client.get('/api/reports/section-timetable/visual/',{'version':str(d['version'].pk),
        'section':str(d['section'].pk),'schedule_mode':'regular','date':'2026-10-05'})
    tuesday=client.get('/api/reports/section-timetable/visual/',{'version':str(d['version'].pk),
        'section':str(d['section'].pk),'schedule_mode':'regular','date':'2026-10-06'})
    assert monday.status_code==200 and len(monday.data['entries'])==1
    assert tuesday.status_code==200 and tuesday.data['entries']==[]
    assert client.get('/api/reports/free-rooms/',{'schedule_mode':'bogus'}).status_code==400
    assert client.get('/api/reports/room-timetable/visual/',{'date':'2026-40-99'}).status_code==400
    draft=TimetableVersion.objects.create(timetable=d['version'].timetable,version_no=2,status='DRAFT',created_by=d['admin'])
    assert client.get('/api/reports/section-timetable/visual/',{'version':str(draft.pk),'schedule_mode':'effective','date':'2026-10-05'}).status_code==400


@pytest.mark.django_db
def test_unaffected_base_class_remains_and_multi_period_overlap_detected(temporary_data):
    d=temporary_data
    unaffected=ScheduleEntry.objects.create(version=d['version'],section=d['section'],course_offering=d['offering'],
        weekday=0,start_slot=d['slots'][2],room=d['room'])
    plan=make_plan(d,status='PUBLISHED')
    make_block(d,plan,slot=d['slots'][0],room=None,override=True)
    rows=resolve_effective_schedule(d['version'],date(2026,10,5))
    assert {row['entry_id'] for row in rows if not row['is_temporary']}=={str(unaffected.pk)}
    plan.status='DRAFT';plan.save()
    plan.blocks.all().delete()
    make_block(d,plan,slot=d['slots'][1],length=2,room=d['room'])
    assert 'ROOM_CONFLICT' in {x['code'] for x in validate_temporary_plan(plan)}


@pytest.mark.django_db
def test_global_room_block_and_effective_faculty_report(temporary_data):
    d=temporary_data;plan=make_plan(d)
    block=make_block(d,plan,slot=d['slots'][1],room=d['room'],faculty=d['faculty'])
    RoomAvailability.objects.create(room=d['room'],weekday=None,date=None,time_slot=d['slots'][1],status='BLOCKED')
    assert 'ROOM_UNAVAILABLE' in {x['code'] for x in validate_temporary_plan(plan)}
    RoomAvailability.objects.all().delete()
    block.start_slot=d['slots'][0];block.override_regular_class=True;block.save(update_fields=['start_slot','override_regular_class'])
    assert not validate_temporary_plan(plan)
    plan.status='PUBLISHED';plan.save()
    client=APIClient();client.force_authenticate(d['admin'])
    result=client.get('/api/reports/faculty-timetable/visual/',{'version':str(d['version'].pk),'faculty':str(d['faculty'].pk),
        'schedule_mode':'effective','date':'2026-10-05'})
    assert result.status_code==200
    assert len(result.data['entries'])==1 and result.data['entries'][0]['is_temporary']


@pytest.mark.django_db
def test_management_lifecycle_rejects_viewer_and_published_edits(temporary_data):
    d=temporary_data;plan=make_plan(d)
    block=make_block(d,plan,slot=d['slots'][1])
    viewer=User.objects.create_user('temp-viewer2@test.local','pass',role=Role.READ_ONLY_VIEWER)
    client=APIClient();client.force_authenticate(viewer)
    assert client.patch(f'/api/temporary-schedules/{plan.pk}/',{'title':'No'},format='json').status_code==403
    assert client.post(f'/api/temporary-schedules/{plan.pk}/publish/',{},format='json').status_code==403
    coordinator=User.objects.create_user('temp-coordinator@test.local','pass',role=Role.TIMETABLE_COORDINATOR)
    client.force_authenticate(coordinator)
    assert client.patch(f'/api/temporary-schedules/{plan.pk}/',{'notes':'Prepared'},format='json').status_code==200
    assert client.post(f'/api/temporary-schedules/{plan.pk}/publish/',{},format='json').status_code==403
    client.force_authenticate(d['admin'])
    assert client.patch(f'/api/temporary-schedules/{plan.pk}/',{'notes':'Edited'},format='json').status_code==200
    assert client.post(f'/api/temporary-schedules/{plan.pk}/publish/',{},format='json').status_code==200
    client.force_authenticate(coordinator)
    assert client.post(f'/api/temporary-schedules/{plan.pk}/cancel/',{},format='json').status_code==403
    client.force_authenticate(d['admin'])
    assert client.delete(f'/api/temporary-schedules/{plan.pk}/blocks/{block.pk}/').status_code==404
    assert client.delete(f'/api/temporary-schedules/{plan.pk}/').status_code==409


@pytest.mark.django_db
@pytest.mark.parametrize(('day','weekday'),[(date(2026,10,10),5),(date(2026,10,11),6)])
def test_specific_weekend_event_is_visible_in_dated_visual_reports(temporary_data,day,weekday):
    d=temporary_data
    plan=TemporarySchedulePlan.objects.create(base_timetable_version=d['version'],title='Weekend Workshop',
        start_date=day,end_date=day,created_by=d['admin'],status='PUBLISHED')
    plan.sections.add(d['section'])
    make_block(d,plan,specific_date=day,room=d['room'])
    client=APIClient();client.force_authenticate(d['admin'])
    params={'version':str(d['version'].pk),'schedule_mode':'effective','date':day.isoformat()}
    section=client.get('/api/reports/section-timetable/visual/',{**params,'section':str(d['section'].pk)})
    room=client.get('/api/reports/room-timetable/visual/',{**params,'room':str(d['room'].pk)})
    assert section.status_code==200 and room.status_code==200
    assert section.data['entries'][0]['weekday']==weekday
    assert room.data['entries'][0]['weekday']==weekday
    assert section.data['entries'][0]['day']==('Saturday' if weekday==5 else 'Sunday')


@pytest.mark.django_db
@pytest.mark.parametrize(('status','temporary'),[('DRAFT',False),('PUBLISHED',True),('CANCELLED',False)])
def test_plan_status_controls_overlay(temporary_data,status,temporary):
    d=temporary_data;plan=make_plan(d,status=status)
    make_block(d,plan,override=True)
    result=resolve_effective_schedule(d['version'],date(2026,10,5))
    assert len(result)==1 and result[0]['is_temporary'] is temporary


@pytest.mark.django_db
@pytest.mark.parametrize(('mode','source'),[('regular','REGULAR'),('effective','TEMPORARY'),('special','TEMPORARY')])
def test_each_schedule_mode_has_expected_source(temporary_data,mode,source):
    d=temporary_data;plan=make_plan(d,status='PUBLISHED')
    make_block(d,plan,override=True)
    result=resolve_effective_schedule(d['version'],date(2026,10,5),mode=mode)
    assert len(result)==1 and result[0]['source']==source


@pytest.mark.django_db
@pytest.mark.parametrize('status',['BLOCKED','MAINTENANCE'])
def test_room_restriction_status_blocks_publication(temporary_data,status):
    d=temporary_data;plan=make_plan(d)
    make_block(d,plan,slot=d['slots'][1],room=d['room'])
    RoomAvailability.objects.create(room=d['room'],weekday=0,time_slot=d['slots'][1],status=status)
    assert 'ROOM_UNAVAILABLE' in {item['code'] for item in validate_temporary_plan(plan)}


@pytest.mark.django_db
@pytest.mark.parametrize(('blocked_weekday','blocked'),[(0,True),(1,False),(None,True)])
def test_weekday_room_restriction_scope(temporary_data,blocked_weekday,blocked):
    d=temporary_data;plan=make_plan(d)
    make_block(d,plan,slot=d['slots'][1],room=d['room'])
    RoomAvailability.objects.create(room=d['room'],weekday=blocked_weekday,time_slot=d['slots'][1],status='BLOCKED')
    assert ('ROOM_UNAVAILABLE' in {item['code'] for item in validate_temporary_plan(plan)}) is blocked


@pytest.mark.django_db
@pytest.mark.parametrize('length',[1,2,3])
def test_temporary_block_occupies_exact_period_count(temporary_data,length):
    d=temporary_data;plan=make_plan(d,status='PUBLISHED')
    make_block(d,plan,length=length,override=True)
    result=resolve_effective_schedule(d['version'],date(2026,10,5))
    assert len(result)==1 and len(result[0]['occupied_slot_ids'])==length


def post_plan_with_patterns(d,patterns,sections=None):
    client=APIClient();client.force_authenticate(d['admin'])
    return client.post('/api/temporary-schedules/',{'title':'Training soft skill','event_type':'TRAINING',
        'start_date':'2026-10-12','end_date':'2026-10-24','base_timetable_version':str(d['version'].pk),
        'sections':[str(x.pk) for x in (sections or [d['section']])],'patterns':patterns},format='json')


def slot_id_at(d,hour):
    return str(next(slot.pk for slot in d['slots'] if slot.start_time.hour==hour))


@pytest.mark.django_db
@pytest.mark.parametrize(('start_hour','end_hour','expected_length'),[(10,12,3),(14,16,3)])
def test_pattern_clock_ranges_map_to_canonical_slots(temporary_data,start_hour,end_hour,expected_length):
    d=temporary_data
    response=post_plan_with_patterns(d,[{'mode':'weekday','weekdays':[0],'start_slot':slot_id_at(d,start_hour),
        'end_slot':slot_id_at(d,end_hour),'override_regular_class':True}])
    assert response.status_code==201
    block=TemporarySchedulePlan.objects.get(pk=response.data['id']).blocks.get()
    assert str(block.start_slot_id)==slot_id_at(d,start_hour)
    assert block.block_length==expected_length


@pytest.mark.django_db
def test_pattern_applies_monday_to_friday_to_each_selected_section(temporary_data):
    d=temporary_data
    sections=[d['section']]+[Section.objects.create(program=d['program'],semester=d['semester'],year=2,name=f'Batch {i}') for i in range(2)]
    response=post_plan_with_patterns(d,[{'mode':'weekday','weekdays':[0,1,2,3,4],
        'start_slot':slot_id_at(d,10),'end_slot':slot_id_at(d,12)}],sections)
    assert response.status_code==201
    plan=TemporarySchedulePlan.objects.get(pk=response.data['id'])
    assert plan.sections.count()==3 and plan.blocks.count()==15
    assert {block.weekday for block in plan.blocks.all()}=={0,1,2,3,4}


@pytest.mark.django_db
def test_multiple_patterns_and_specific_date_are_saved(temporary_data):
    d=temporary_data
    patterns=[{'mode':'weekday','weekdays':[0,2,4],'start_slot':slot_id_at(d,10),'end_slot':slot_id_at(d,12)},
        {'mode':'weekday','weekdays':[1,3],'start_slot':slot_id_at(d,14),'end_slot':slot_id_at(d,16)},
        {'mode':'date','specific_date':'2026-10-17','start_slot':slot_id_at(d,10),'end_slot':slot_id_at(d,11)}]
    response=post_plan_with_patterns(d,patterns)
    assert response.status_code==201
    blocks=TemporarySchedulePlan.objects.get(pk=response.data['id']).blocks.all()
    assert blocks.count()==6
    assert set(blocks.filter(specific_date__isnull=True).values_list('weekday',flat=True))=={0,1,2,3,4}
    assert blocks.get(specific_date='2026-10-17').weekday is None


@pytest.mark.django_db
@pytest.mark.parametrize(('pattern','expected_field'),[
    ({'mode':'weekday','weekdays':[],'start_slot':'','end_slot':''},'patterns'),
    ({'mode':'weekday','weekdays':[0],'start_slot':'','end_slot':''},'patterns'),
])
def test_invalid_empty_or_reverse_patterns_return_structured_400(temporary_data,pattern,expected_field):
    d=temporary_data
    pattern.update(start_slot=slot_id_at(d,11),end_slot=slot_id_at(d,10))
    response=post_plan_with_patterns(d,[pattern])
    assert response.status_code==400 and expected_field in response.data
    assert not TemporarySchedulePlan.objects.filter(title='Training soft skill').exists()


@pytest.mark.django_db
def test_pattern_crossing_break_is_rejected_and_creation_rolls_back(temporary_data):
    d=temporary_data
    valid={'mode':'weekday','weekdays':[0],'start_slot':slot_id_at(d,10),'end_slot':slot_id_at(d,10)}
    crossing={'mode':'weekday','weekdays':[1],'start_slot':slot_id_at(d,12),'end_slot':slot_id_at(d,14)}
    response=post_plan_with_patterns(d,[valid,crossing])
    assert response.status_code==400 and 'break' in str(response.data).lower()
    assert not TemporarySchedulePlan.objects.filter(title='Training soft skill').exists()


@pytest.mark.django_db
def test_override_pattern_preview_reports_displaced_regular_periods(temporary_data):
    d=temporary_data
    response=post_plan_with_patterns(d,[{'mode':'weekday','weekdays':[0],
        'start_slot':slot_id_at(d,9),'end_slot':slot_id_at(d,9),'override_regular_class':True}])
    assert response.status_code==201
    plan=TemporarySchedulePlan.objects.get(pk=response.data['id'])
    impact=plan_impact(plan)
    assert impact['valid'] is True
    assert impact['summary']['overridden_regular_periods']==2
    assert impact['summary']['temporary_section_period_instances']==2


@pytest.mark.django_db
def test_initial_temporary_page_api_endpoints_are_healthy(temporary_data):
    client=APIClient();client.force_authenticate(temporary_data['admin'])
    for path in ('/api/temporary-schedules/','/api/temporary-schedules/options/',
        '/api/temporary-schedules/section-options/','/api/temporary-schedules/slot-options/',
        '/api/temporary-schedules/room-options/','/api/temporary-schedules/faculty-options/',
        '/api/programs/','/api/time-slots/','/api/rooms/','/api/faculty/'):
        response=client.get(path,{'version':str(temporary_data['version'].pk)},HTTP_HOST='localhost')
        assert response.status_code==200,path


@pytest.mark.django_db
def test_many_section_pattern_creation_is_complete_and_atomic(temporary_data):
    d=temporary_data
    sections=[d['section']]+[Section.objects.create(program=d['program'],semester=d['semester'],year=2,name=f'Batch {i}') for i in range(29)]
    pattern={'mode':'weekday','weekdays':[0,1,2,3,4],'start_slot':slot_id_at(d,10),'end_slot':slot_id_at(d,12)}
    response=post_plan_with_patterns(d,[pattern],sections)
    assert response.status_code==201 and len(response.data['sections'])==30
    plan=TemporarySchedulePlan.objects.get(pk=response.data['id'])
    assert plan.blocks.count()==150


@pytest.mark.django_db
def test_temporary_room_options_are_unpaginated_active_and_lightweight(temporary_data):
    d=temporary_data
    active=[d['room']]
    for index in range(24):
        active.append(Room.objects.create(code=f'OPTION-{index:02}',building='Annex',floor=str(index%4),
            capacity=40+index,room_type='CLASSROOM',has_projector=index%2==0))
    Room.objects.create(code='INACTIVE-ROOM',building='Archive',floor='1',capacity=20,active=False)
    client=APIClient();client.force_authenticate(d['admin'])
    options=client.get('/api/temporary-schedules/room-options/')
    assert options.status_code==200 and len(options.data)==25
    assert len({row['id'] for row in options.data})==25
    assert any(row['code']=='OPTION-10' for row in options.data)
    assert not any(row['code']=='INACTIVE-ROOM' for row in options.data)
    assert {'id','code','building','floor','capacity','room_type','room_type_label','has_projector'}<=set(options.data[0])
    crud=client.get('/api/rooms/')
    assert crud.status_code==200 and crud.data['count']==26 and len(crud.data['results'])==10


@pytest.mark.django_db
def test_room_id_is_used_in_pattern_payload_and_multi_section_shared_room_rejected(temporary_data):
    d=temporary_data
    pattern={'mode':'weekday','weekdays':[0],'start_slot':slot_id_at(d,10),'end_slot':slot_id_at(d,10),
        'room_id':str(d['room'].pk)}
    created=post_plan_with_patterns(d,[pattern])
    assert created.status_code==201
    assert TemporarySchedulePlan.objects.get(pk=created.data['id']).blocks.get().room_id==d['room'].pk
    second=Section.objects.create(program=d['program'],semester=d['semester'],year=2,name='Simultaneous')
    rejected=post_plan_with_patterns(d,[pattern],[d['section'],second])
    assert rejected.status_code==400 and 'room' in rejected.data
    assert not TemporarySchedulePlan.objects.filter(title='Training soft skill').exclude(pk=created.data['id']).exists()


@pytest.mark.django_db
def test_temporary_faculty_options_include_records_beyond_page_one(temporary_data):
    d=temporary_data
    for index in range(12):
        Faculty.objects.create(name=f'Options Faculty {index:02}',employee_code=f'OPT-{index:02}',department=d['department'])
    client=APIClient();client.force_authenticate(d['admin'])
    options=client.get('/api/temporary-schedules/faculty-options/')
    assert options.status_code==200 and len(options.data)==13
    assert len({row['id'] for row in options.data})==13
    assert any(row['name']=='Options Faculty 10' for row in options.data)
    assert {'id','name','employee_code','initials','department_name'}<=set(options.data[0])
    crud=client.get('/api/faculty/')
    assert crud.status_code==200 and crud.data['count']==13 and len(crud.data['results'])==10
