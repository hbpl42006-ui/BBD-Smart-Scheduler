from io import StringIO

import pytest
from django.core.management import call_command

from academics.models import Course, CourseOffering, Section, Semester
from faculty.models import CourseOfferingFaculty, Faculty
from rooms.models import Room
from scheduling.models import ScheduleEntry, TimetableVersion
from scheduling.services.course_offering_repair import apply_repair, plan_repair, preview_counts
from scheduling.test_scheduling import data


def source_rows(section='B', employee='F001', role=None):
    rows = []
    for hour in (9, 10, 11):
        row = {
            'Section': section, 'Course Code': 'CS101', 'Credit': '3.0',
            'Employee Code': employee, 'Faculty Name': 'Test Faculty' if employee == 'F001' else 'Second Faculty',
            'Day': 'Monday', 'Time Slot': f'{hour}:00-{hour + 1}:00', 'Room No.': 'R101',
        }
        if role:
            row['Faculty Role'] = role
        rows.append(row)
    return rows


def target_section(data):
    return Section.objects.create(
        program=data['program'], semester=data['semester'], year=1,
        name='B', student_strength=40,
    )


def test_repair_creates_one_offering_and_mapping_from_three_rows_and_is_idempotent(data):
    target = target_section(data)
    source = source_rows()
    original_credit = Course.objects.get(pk=data['course'].pk).credit
    original_offering = CourseOffering.objects.get(pk=data['offering'].pk)
    original_periods = original_offering.weekly_periods
    before = preview_counts(data['version'], source)
    assert before['missing_offering_rows'] == 3
    plan = plan_repair(data['version'], source)
    assert plan['missing_found'] == 1
    assert plan['would_create'] == 1
    assert plan['faculty_assignments_to_create'] == 1
    assert not plan['problems']
    assert not CourseOffering.objects.filter(section=target, course=data['course']).exists()

    created = apply_repair(data['version'], plan)
    assert created['offerings_created'] == 1
    assert created['faculty_mappings_created'] == 1
    offering = CourseOffering.objects.get(section=target, course=data['course'])
    assert offering.semester_id == data['version'].timetable.semester_id
    assert offering.weekly_periods == original_periods
    assert CourseOfferingFaculty.objects.get(course_offering=offering).faculty_id == data['faculty'].pk
    assert preview_counts(data['version'], source) == {'total': 3, 'valid': 3, 'invalid': 0, 'missing_offering_rows': 0}

    again = apply_repair(data['version'], plan_repair(data['version'], source))
    assert again['offerings_created'] == again['faculty_mappings_created'] == 0
    assert CourseOffering.objects.filter(section=target, course=data['course']).count() == 1
    assert CourseOfferingFaculty.objects.filter(course_offering=offering).count() == 1
    original_offering.refresh_from_db()
    assert original_offering.weekly_periods == original_periods
    assert Course.objects.get(pk=data['course'].pk).credit == original_credit


def test_repair_dry_run_does_not_change_master_or_published_data(data, tmp_path):
    target = target_section(data)
    published = TimetableVersion.objects.create(
        timetable=data['timetable'], version_no=4, status='PUBLISHED', created_by=data['admin'],
    )
    entry = ScheduleEntry.objects.create(
        version=published, section=data['section'], course_offering=data['offering'],
        weekday=0, start_slot=data['slots'][0], room=data['room'],
    )
    source = source_rows()
    path = tmp_path / 'timetable.csv'
    import csv
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=source[0])
        writer.writeheader()
        writer.writerows(source)
    output = StringIO()
    call_command(
        'repair_missing_course_offerings_for_timetable_import', str(path),
        version_id=str(data['version'].pk), dry_run=True, stdout=output,
    )
    assert 'Missing CourseOfferings found: 1' in output.getvalue()
    assert 'Would create: 1' in output.getvalue()
    assert not CourseOffering.objects.filter(section=target, course=data['course']).exists()
    assert ScheduleEntry.objects.get(pk=entry.pk).version_id == published.pk
    published.refresh_from_db()
    assert published.status == 'PUBLISHED'
    applied_output = StringIO()
    call_command(
        'repair_missing_course_offerings_for_timetable_import', str(path),
        version_id=str(data['version'].pk), apply=True, stdout=applied_output,
    )
    assert "'offerings_created': 1" in applied_output.getvalue()
    assert "'missing_offering_rows': 0" in applied_output.getvalue()
    assert CourseOffering.objects.filter(section=target, course=data['course']).count() == 1
    assert ScheduleEntry.objects.get(pk=entry.pk).version_id == published.pk
    assert data['version'].entries.count() == 0
    published.refresh_from_db()
    assert published.status == 'PUBLISHED'


def test_repair_keeps_existing_offering_and_adds_only_missing_faculty_mapping(data):
    source = source_rows(section='A')
    plan = plan_repair(data['version'], source)
    assert plan['missing_found'] == 0
    assert plan['already_existing'] == 1
    assert plan['would_create'] == 0
    assert plan['existing_offering_mappings_to_create'] == 1
    result = apply_repair(data['version'], plan)
    assert result['offerings_created'] == 0
    assert result['existing_offering_mappings_created'] == 1
    assert CourseOfferingFaculty.objects.filter(course_offering=data['offering'], faculty=data['faculty']).count() == 1


def test_repair_requires_roles_for_multiple_faculty_unless_source_specifies_them(data):
    target = target_section(data)
    second = Faculty.objects.create(employee_code='F002', name='Second Faculty', initials='SF', department=data['department'])
    source = source_rows() + source_rows(employee='F002')
    plan = plan_repair(data['version'], source)
    assert plan['missing_found'] == 1 and plan['would_create'] == 0
    assert any('Multiple faculty' in problem for problem in plan['problems'])
    assert not CourseOffering.objects.filter(section=target).exists()
    explicit = source_rows(role='PRIMARY') + source_rows(employee='F002', role='CO_FACULTY')
    plan = plan_repair(data['version'], explicit)
    assert plan['would_create'] == 1 and plan['faculty_assignments_to_create'] == 2
    apply_repair(data['version'], plan)
    offering = CourseOffering.objects.get(section=target, course=data['course'])
    assert set(CourseOfferingFaculty.objects.filter(course_offering=offering).values_list('faculty_id', 'role')) == {
        (data['faculty'].pk, 'PRIMARY'), (second.pk, 'CO_FACULTY'),
    }


def test_repair_uses_version_semester_and_does_not_guess_unmatched_configuration(data):
    target = target_section(data)
    other_semester = Semester.objects.create(
        session=data['session'], name='Even Semester', number=2, type='EVEN',
        start_date=data['semester'].end_date, end_date=data['session'].end_date,
    )
    other_section = Section.objects.create(program=data['program'], semester=other_semester, year=1, name='B', student_strength=40)
    CourseOffering.objects.create(semester=other_semester, section=other_section, course=data['course'], weekly_periods=9)
    data['offering'].delete()
    plan = plan_repair(data['version'], source_rows())
    assert plan['missing_found'] == 1 and plan['would_create'] == 0
    assert any('No equivalent offering' in problem for problem in plan['problems'])
    assert not CourseOffering.objects.filter(section=target).exists()


def test_repair_uses_matching_source_room_to_resolve_conflicting_siblings(data):
    target_section(data)
    data['offering'].preferred_room = data['room']
    data['offering'].room_type_requirement = 'CLASSROOM'
    data['offering'].save(update_fields=['preferred_room', 'room_type_requirement'])
    exception_section = Section.objects.create(
        program=data['program'], semester=data['semester'], year=1,
        name='C', student_strength=40,
    )
    exception_room = Room.objects.create(code='R102', building='Main', floor='1', capacity=50, room_type='LAB')
    CourseOffering.objects.create(
        semester=data['semester'], section=exception_section, course=data['course'],
        weekly_periods=3, room_type_requirement='LAB', preferred_room=exception_room,
    )
    plan = plan_repair(data['version'], source_rows())
    assert plan['would_create'] == 1 and not plan['problems']
    assert plan['plans'][0]['configuration']['preferred_room'].pk == data['room'].pk
    assert plan['plans'][0]['configuration']['room_type_requirement'] == 'CLASSROOM'


def test_repair_reports_source_period_shortfall_without_changing_sibling_requirement(data):
    target_section(data)
    plan = plan_repair(data['version'], source_rows()[:2])
    assert plan['would_create'] == 1
    assert plan['plans'][0]['configuration']['weekly_periods'] == 3
    assert len(plan['warnings']) == 1
