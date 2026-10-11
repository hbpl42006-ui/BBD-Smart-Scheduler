import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from academics.models import Section
from faculty.models import CourseOfferingFaculty
from rooms.models import Room
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty
from scheduling.test_scheduling import client, data


def upload(room, *, section='A', day='Tuesday', slot='9:00-10:00'):
    value = '' if room is None else room
    body = ('Section,Course Code,Employee Code,Day,Time Slot,Room No.\n'
            f'{section},CS101,F001,{day},{slot},{value}\n').encode()
    return SimpleUploadedFile('hybrid.csv', body, content_type='text/csv')


@pytest.fixture
def year4(data):
    section = data['section']
    section.year = 4
    section.delivery_policy = Section.DeliveryPolicy.HYBRID
    section.offline_weekday = 0
    section.save(update_fields=['year', 'delivery_policy', 'offline_weekday'])
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'])
    return section


def preview(client, data, room, **kwargs):
    return client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/preview/',
        {'file': upload(room, **kwargs)}, format='multipart',
    )


@pytest.mark.parametrize('room', ['', '   ', None])
def test_year4_blank_room_is_online_even_on_offline_weekday(client, data, year4, room):
    response = preview(client, data, room, day='Monday')
    assert response.status_code == 200
    assert response.data['valid'] == 1
    assert response.data['rows'][0]['delivery_mode'] == 'ONLINE'
    assert response.data['rows'][0]['room'] is None


def test_year4_online_commit_has_no_physical_room(client, data, year4):
    response = client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/commit/',
        {'file': upload('', day='Monday')}, format='multipart',
    )
    assert response.status_code == 201
    entry = ScheduleEntry.objects.get(version=data['version'])
    assert entry.delivery_mode == 'ONLINE' and entry.room_id is None
    assert Room.objects.filter(code='ONLINE').count() == 0
    from scheduling.services.validation import validate_version
    conflicts = validate_version(data['version'])['conflicts']
    assert not any(item['type'] in {'DELIVERY_MODE_MISMATCH', 'ROOM_REQUIRED'} for item in conflicts)


def test_year4_physical_room_is_offline_on_any_weekday(client, data, year4):
    response = preview(client, data, 'R101')
    assert response.status_code == 200
    assert response.data['valid'] == 1
    assert response.data['rows'][0]['delivery_mode'] == 'OFFLINE'
    assert response.data['rows'][0]['room'] == 'R101'


def test_year4_unknown_physical_room_still_fails(client, data, year4):
    response = preview(client, data, 'NOT-A-ROOM')
    assert response.data['invalid'] == 1
    assert 'not found' in response.data['errors'][0]['message']


def test_year4_physical_room_capacity_still_fails(client, data, year4):
    data['room'].capacity = 1
    data['room'].save(update_fields=['capacity'])
    response = preview(client, data, 'R101')
    assert response.data['invalid'] == 1
    assert response.data['errors'][0]['code'] == 'ROOM_CAPACITY_EXCEEDED'


@pytest.mark.parametrize('clash', ['section', 'faculty'])
def test_year4_online_still_detects_time_clashes(client, data, year4, clash):
    other_section = Section.objects.create(
        program=data['program'], semester=data['semester'], year=4,
        name='B', student_strength=30,
    )
    entry = ScheduleEntry.objects.create(
        version=data['version'], section=year4 if clash == 'section' else other_section,
        course_offering=data['offering'], weekday=1,
        start_slot=data['slots'][0], room=None, delivery_mode='ONLINE',
    )
    if clash == 'faculty':
        ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=data['faculty'])
    response = preview(client, data, '')
    assert response.data['invalid'] == 1
    assert f'{clash.capitalize()} is already occupied' in response.data['errors'][0]['message']
    assert response.data['errors'][0]['code'] == f'{clash.upper()}_CLASH'


def test_earlier_year_blank_room_remains_invalid(client, data):
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'])
    response = preview(client, data, '')
    assert response.data['invalid'] == 1
    assert 'Room "" not found' in response.data['errors'][0]['message']
