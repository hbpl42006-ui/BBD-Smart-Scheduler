import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from academics.models import Course, CourseOffering
from faculty.models import CourseOfferingFaculty
from rooms.models import Room
from rooms.services.eligibility import room_eligibility_error, room_satisfies_requirement
from scheduling.test_scheduling import client, data


def preview(client, data, course_code, room_code):
    body = ('Section,Course Code,Employee Code,Day,Time Slot,Room No.\n'
            f'A,{course_code},F001,Monday,9:00-10:00,{room_code}\n').encode()
    return client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/preview/',
        {'file': SimpleUploadedFile('timetable.csv', body, content_type='text/csv')},
        format='multipart',
    )


@pytest.fixture
def room105(data):
    data['program'].code = 'BTECH-CSE'
    data['program'].save(update_fields=['code'])
    return Room.objects.create(
        code='105', building='Engineering', floor='1', capacity=60,
        room_type='QUANTUM_PHYSICS_ADVANCED_FUNCTIONAL_MATERIALS_LAB',
    )


def test_import_allows_rbs5151_in_room105_even_if_row_defaults_to_lecture(client, data, room105):
    course = Course.objects.create(code='RBS5151', name='Quantum Physics and Advanced Functional Materials Lab', credit=1)
    data['offering'].course = course
    data['offering'].default_class_type = 'PRACTICAL'
    data['offering'].room_type_requirement = 'CLASSROOM'
    data['offering'].save(update_fields=['course', 'default_class_type', 'room_type_requirement'])
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'])
    response = preview(client, data, 'RBS5151', '105')
    assert response.status_code == 200 and response.data['valid'] == 1
    assert response.data['rows'][0]['room'] == '105'
    body = b'Section,Course Code,Employee Code,Day,Time Slot,Room No.\nA,RBS5151,F001,Monday,9:00-10:00,105\n'
    committed = client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/commit/',
        {'file': SimpleUploadedFile('timetable.csv', body, content_type='text/csv')},
        format='multipart',
    )
    assert committed.status_code == 201 and committed.data['entries_created'] == 1
    assert data['version'].entries.get().room_id == room105.pk


def test_import_rejects_unrelated_course_in_room105(client, data, room105):
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'])
    response = preview(client, data, 'CS101', '105')
    assert response.status_code == 200 and response.data['invalid'] == 1
    assert response.data['errors'][0]['code'] == 'FIXED_ROOM_RESERVED'


def test_room_type_requirement_stays_enforced_and_can_be_corrected(data):
    room = data['room']
    offering = data['offering']
    offering.room_type_requirement = 'CLASSROOM'
    offering.save(update_fields=['room_type_requirement'])
    assert room_eligibility_error(room, offering, data['section']) is None
    offering.room_type_requirement = 'COMPUTER_LAB'
    offering.save(update_fields=['room_type_requirement'])
    assert room_eligibility_error(room, offering, data['section'])['code'] == 'ROOM_TYPE_MISMATCH'
    offering.room_type_requirement = 'CLASSROOM'
    offering.save(update_fields=['room_type_requirement'])
    assert room_eligibility_error(room, offering, data['section']) is None


def test_practical_is_activity_marker_not_a_strict_lab_requirement(data):
    lab = Room.objects.create(code='LAB601', building='Main', floor='6', capacity=60, room_type='COMPUTER_LAB')
    assert room_satisfies_requirement(lab, 'PRACTICAL')
    assert room_satisfies_requirement(data['room'], 'PRACTICAL')
    assert room_satisfies_requirement(lab, 'COMPUTER_LAB')
    assert not room_satisfies_requirement(data['room'], 'COMPUTER_LAB')


def test_import_room_type_requires_evidence_based_offering_correction(client, data):
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'])
    data['offering'].room_type_requirement = 'COMPUTER_LAB'
    data['offering'].save(update_fields=['room_type_requirement'])
    rejected = preview(client, data, 'CS101', 'R101')
    assert rejected.status_code == 200 and rejected.data['invalid'] == 1
    assert rejected.data['errors'][0]['code'] == 'ROOM_TYPE_MISMATCH'
    data['offering'].room_type_requirement = 'CLASSROOM'
    data['offering'].save(update_fields=['room_type_requirement'])
    accepted = preview(client, data, 'CS101', 'R101')
    assert accepted.status_code == 200 and accepted.data['valid'] == 1


def test_import_resolves_theory_and_practical_offerings_by_activity_type(client, data):
    lecture = data['offering']
    lecture.default_class_type = 'LECTURE'
    lecture.room_type_requirement = 'CLASSROOM'
    lecture.preferred_room = data['room']
    lecture.save(update_fields=['default_class_type', 'room_type_requirement', 'preferred_room'])
    mechanics_lab = Room.objects.create(
        code='MECH-LAB', building='Engineering', floor='0', capacity=60,
        room_type='MECHANICS_LAB',
    )
    practical = CourseOffering.objects.create(
        semester=data['semester'], section=data['section'], course=data['course'],
        weekly_periods=2, default_class_type='PRACTICAL', required_block_size=2,
        room_type_requirement='MECHANICS_LAB', preferred_room=mechanics_lab,
    )
    CourseOfferingFaculty.objects.create(course_offering=lecture, faculty=data['faculty'])
    CourseOfferingFaculty.objects.create(course_offering=practical, faculty=data['faculty'])
    url = f'/api/versions/{data["version"].pk}/imports/timetable/'

    theory_body = b'Section,Course Code,Employee Code,Day,Time Slot,Room No.\nA,CS101,F001,Monday,9:00-10:00,R101\n'
    preview_response = client.post(url + 'preview/', {
        'file': SimpleUploadedFile('theory.csv', theory_body, content_type='text/csv')}, format='multipart')
    assert preview_response.status_code == 200 and preview_response.data['valid'] == 1
    commit_response = client.post(url + 'commit/', {
        'file': SimpleUploadedFile('theory.csv', theory_body, content_type='text/csv')}, format='multipart')
    assert commit_response.status_code == 201
    entry = data['version'].entries.get()
    assert entry.course_offering_id == lecture.pk and entry.entry_type == 'LECTURE' and entry.room_id == data['room'].pk

    data['version'].entries.all().delete()
    practical_body = b'Section,Course Code,Employee Code,Day,Time Slot,Room No.,Entry Type,Block Length\nA,CS101,F001,Monday,9:00-10:00,MECH-LAB,PRACTICAL,2\n'
    practical_response = client.post(url + 'preview/', {
        'file': SimpleUploadedFile('practical.csv', practical_body, content_type='text/csv')}, format='multipart')
    assert practical_response.status_code == 200 and practical_response.data['valid'] == 1


def test_import_rejects_ambiguous_activity_instead_of_selecting_first(client, data):
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'])
    CourseOffering.objects.create(
        semester=data['semester'], section=data['section'], course=data['course'],
        weekly_periods=1, default_class_type='PRACTICAL', room_type_requirement='LAB',
    )
    body = b'Section,Course Code,Employee Code,Day,Time Slot,Room No.,Entry Type\nA,CS101,F001,Monday,9:00-10:00,R101,OTHER\n'
    response = client.post(
        f'/api/versions/{data["version"].pk}/imports/timetable/preview/',
        {'file': SimpleUploadedFile('ambiguous.csv', body, content_type='text/csv')}, format='multipart')
    assert response.status_code == 200 and response.data['invalid'] == 1
    assert response.data['errors'][0]['code'] == 'COURSE_OFFERING_ACTIVITY_MISMATCH'
