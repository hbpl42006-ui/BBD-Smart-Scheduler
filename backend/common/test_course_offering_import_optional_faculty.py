from datetime import date

import pytest

from academics.models import AcademicSession, Course, CourseOffering, Section, Semester
from common.course_offering_import import commit, parse
from faculty.models import CourseOfferingFaculty, Faculty
from institutions.models import Department, Institution, Program
from rooms.models import Room


@pytest.fixture
def offering_import_fixture(db):
    institution = Institution.objects.create(name='Import Institute', code='IMP')
    department = Department.objects.create(institution=institution, name='Computer Science', code='IMP-CSE')
    program = Program.objects.create(department=department, name='B.Tech CSE', code='IMP-BTECH', duration_years=4)
    session = AcademicSession.objects.create(
        institution=institution, name='2026-27', start_date=date(2026, 7, 1), end_date=date(2027, 6, 30)
    )
    semester = Semester.objects.create(
        session=session, name='Odd Semester', number=1, type=Semester.SemesterType.ODD,
        start_date=date(2026, 7, 1), end_date=date(2026, 12, 31)
    )
    section = Section.objects.create(program=program, semester=semester, year=1, name='CS 1A')
    course = Course.objects.create(code='IMP101', name='Import Course', credit=3, short_code='IC')
    room = Room.objects.create(code='IMP-R1', building='Main', floor='1', capacity=60, room_type='CLASSROOM')
    faculty = Faculty.objects.create(employee_code='IMP-F001', initials='IF', name='Import Faculty', department=department)
    return {
        'session': session.name, 'semester': semester.name, 'section': section.name,
        'course': course.code, 'room': room.code, 'faculty': faculty,
        'semester_obj': semester, 'section_obj': section, 'course_obj': course,
    }


def row(data, employee_code='', faculty_name=''):
    return {
        'Session': data['session'], 'Semester': data['semester'], 'Course Code': data['course'],
        'Section': data['section'], 'Weekly Periods': 3, 'Employee Code': employee_code, 'Faculty Name': faculty_name,
        'Room No.': data['room'],
    }


def test_blank_employee_code_is_valid_warning_and_creates_no_mapping(offering_import_fixture):
    result, prepared = parse([row(offering_import_fixture)])

    assert result['valid'] == 1
    assert result['invalid'] == 0
    assert result['warnings'] == 1
    assert prepared[0][5] is None
    counts = commit(prepared)
    assert counts['offerings_created'] == 1
    assert CourseOfferingFaculty.objects.count() == 0


def test_nf_faculty_name_is_valid_warning_and_creates_no_mapping(offering_import_fixture):
    result, prepared = parse([row(offering_import_fixture, faculty_name=' nf ')])

    assert result['valid'] == 1
    assert result['invalid'] == 0
    assert result['warnings'] == 1
    assert 'not assigned' in result['warning_details'][0]['message'].lower()
    commit(prepared)
    assert CourseOfferingFaculty.objects.count() == 0


def test_nonblank_invalid_employee_code_remains_invalid(offering_import_fixture):
    result, prepared = parse([row(offering_import_fixture, 'DOES-NOT-EXIST')])

    assert result['valid'] == 0
    assert result['invalid'] == 1
    assert prepared == []


def test_blank_employee_code_preserves_existing_faculty_mapping_on_upsert(offering_import_fixture):
    data = offering_import_fixture
    offering = CourseOffering.objects.create(
        semester=data['semester_obj'], section=data['section_obj'], course=data['course_obj'], weekly_periods=2
    )
    CourseOfferingFaculty.objects.create(course_offering=offering, faculty=data['faculty'])

    result, prepared = parse([row(data)])
    assert result['valid'] == 1
    commit(prepared)

    assert CourseOfferingFaculty.objects.filter(course_offering=offering, faculty=data['faculty']).count() == 1
    assert CourseOffering.objects.get(pk=offering.pk).weekly_periods == 3


def test_valid_employee_code_creates_assignment(offering_import_fixture):
    data = offering_import_fixture
    result, prepared = parse([row(data, data['faculty'].employee_code)])

    assert result['valid'] == 1
    assert result['warnings'] == 0
    commit(prepared)
    assert CourseOfferingFaculty.objects.filter(faculty=data['faculty']).count() == 1


@pytest.mark.parametrize(('source', 'canonical'), [('LGF-001', 'LGF001'), ('LGF1', 'LGF001'), ('UGF-12', 'UGF012'), ('UGF13', 'UGF013'), ('UGF-013', 'UGF013')])
def test_room_punctuation_alias_resolves_only_canonical_existing_room(offering_import_fixture, source, canonical):
    data = offering_import_fixture
    room = Room.objects.create(code=canonical, building='Main', floor='0', capacity=60, room_type='CLASSROOM')
    source_row = row(data, data['faculty'].employee_code)
    source_row['Room No.'] = source
    result, prepared = parse([source_row])
    assert result['valid'] == 1
    assert prepared[0][6] == room


def test_ambiguous_room_alias_remains_invalid(offering_import_fixture):
    data = offering_import_fixture
    Room.objects.create(code='Lab1', building='Main', floor='1', capacity=60, room_type='LAB')
    Room.objects.create(code='LAB-1', building='Main', floor='1', capacity=60, room_type='LAB')
    source_row = row(data, data['faculty'].employee_code)
    source_row['Room No.'] = 'LAB1'
    result, prepared = parse([source_row])
    assert result['valid'] == 0
    assert 'ambiguous' in result['errors'][0]['message'].lower()
    assert 'Lab1' in result['errors'][0]['message']
    assert 'LAB-1' in result['errors'][0]['message']


def test_preview_exposes_activity_type_and_blank_room_is_allowed(offering_import_fixture):
    source_row = row(offering_import_fixture, offering_import_fixture['faculty'].employee_code)
    source_row['Room No.'] = ''
    source_row['Activity Type'] = 'LECTURE'
    result, _prepared = parse([source_row])
    assert result['valid'] == 1
    assert result['rows'][0]['activity_type'] == 'LECTURE'
    assert result['rows'][0]['preferred_room'] is None
