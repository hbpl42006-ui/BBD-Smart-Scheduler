import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from faculty.models import CourseOfferingFaculty, Faculty
from scheduling.models import ScheduleEntryFaculty
from scheduling.services.faculty_names import faculty_name_match_kind
from scheduling.test_scheduling import client, data


def upload(name, employee_code='8299668742'):
    body = ('Section,Course Code,Credit,Employee Code,Faculty Name,Day,Time Slot,Room No.\n'
            f'A,CS101,3,{employee_code},{name},Monday,9:00-10:00,R101\n').encode()
    return SimpleUploadedFile('timetable.csv', body, content_type='text/csv')


@pytest.fixture
def awantika(data):
    faculty = Faculty.objects.create(
        employee_code='8299668742', name='Ms. Awantika Awasthi',
        initials='AA', department=data['department'],
    )
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=faculty)
    return faculty


@pytest.mark.parametrize(('name', 'warning'), [
    ('Ms. Awantika', True),
    ('MS AWANTIKA', True),
    ('Awantika', True),
    ('Ms. Awantika Awasthi', False),
    ('AWANTIKA AWASTHI', False),
    ('', False),
])
def test_faculty_name_preview_accepts_canonical_short_or_blank(client, data, awantika, name, warning):
    response = client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/preview/',
        {'file': upload(name)}, format='multipart',
    )
    assert response.status_code == 200
    assert response.data['valid'] == 1 and response.data['invalid'] == 0
    assert response.data['rows'][0]['faculty_name'] == 'Ms. Awantika Awasthi'
    assert bool(response.data['rows'][0].get('message')) is warning
    assert response.data['warnings'] == int(warning)
    assert not data['version'].entries.exists()


@pytest.mark.parametrize(('name', 'code'), [
    ('Dr. Pooja Verma', 'FACULTY_NAME_MISMATCH'),
    ('Ms. Awantika Wrong', 'FACULTY_NAME_MISMATCH'),
    ('A. Awasthi', 'FACULTY_NAME_MISMATCH'),
])
def test_faculty_name_preview_rejects_conflicts(client, data, awantika, name, code):
    response = client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/preview/',
        {'file': upload(name)}, format='multipart',
    )
    assert response.status_code == 200
    assert response.data['invalid'] == 1
    assert response.data['errors'][0]['code'] == code
    assert not data['version'].entries.exists()


def test_faculty_name_does_not_resolve_unknown_employee_code(client, data, awantika):
    response = client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/preview/',
        {'file': upload('Ms. Awantika Awasthi', 'UNKNOWN')}, format='multipart',
    )
    assert response.status_code == 200
    assert response.data['invalid'] == 1
    assert response.data['errors'][0]['code'] == 'FACULTY_NOT_FOUND'


def test_faculty_name_commit_uses_resolved_employee_code(client, data, awantika):
    response = client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/commit/',
        {'file': upload('Ms. Awantika')}, format='multipart',
    )
    assert response.status_code == 201
    assert response.data['entries_created'] == 1
    assert ScheduleEntryFaculty.objects.filter(
        schedule_entry__version=data['version'], faculty=awantika,
    ).exists()
    assert Faculty.objects.filter(employee_code='8299668742').count() == 1


@pytest.mark.parametrize(('uploaded', 'canonical', 'expected'), [
    ('Dr. Priya', 'Dr. Priya Kumari', 'ABBREVIATED'),
    ('Mr Abhishek', 'Mr. Abhishek Sharma', 'ABBREVIATED'),
    ('Ms. Shubhranjali', 'Shubhranjali Singh', 'ABBREVIATED'),
    ('Prof. Pooja-Verma', 'Pooja Verma', 'EXACT'),
    ('Ms. Awantika', 'Ms. Awantika Awasthi', 'ABBREVIATED'),
    ('Mr. Vivek Singh', 'Mr. Vivek Kumar Singh', 'ABBREVIATED'),
    ('Pooja Verma', 'Mr. Vivek Kumar Singh', 'MISMATCH'),
    ('Dr. Pooja', 'Ms. Awantika Awasthi', 'MISMATCH'),
])
def test_faculty_name_match_kind(uploaded, canonical, expected):
    assert faculty_name_match_kind(uploaded, canonical) == expected


def test_vivek_middle_name_omission_preview_and_commit(client, data, awantika):
    awantika.employee_code = '9643141501'
    awantika.name = 'Mr. Vivek Kumar Singh'
    awantika.save(update_fields=['employee_code', 'name'])
    url = f'/api/versions/{data["version"].pk}/imports/timetable/'
    preview = client.post(url + 'preview/', {'file': upload('Mr. Vivek Singh', '9643141501')}, format='multipart')
    assert preview.status_code == 200 and preview.data['valid'] == 1
    assert preview.data['rows'][0]['faculty_name'] == 'Mr. Vivek Kumar Singh'
    committed = client.post(url + 'commit/', {'file': upload('Mr. Vivek Singh', '9643141501')}, format='multipart')
    assert committed.status_code == 201 and committed.data['entries_created'] == 1
    assert ScheduleEntryFaculty.objects.filter(schedule_entry__version=data['version'], faculty=awantika).exists()
