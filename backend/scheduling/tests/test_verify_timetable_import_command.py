from io import StringIO

import pytest
from django.core.management import call_command

from faculty.models import CourseOfferingFaculty
from scheduling.models import ScheduleEntry, ScheduleEntryFaculty
from scheduling.management.commands.verify_timetable_import import verify
from scheduling.test_scheduling import data


@pytest.mark.django_db
def test_verify_timetable_import_scopes_counts_to_selected_version(data):
    CourseOfferingFaculty.objects.create(course_offering=data['offering'], faculty=data['faculty'])
    entry = ScheduleEntry.objects.create(
        version=data['version'], section=data['section'], course_offering=data['offering'],
        weekday=0, start_slot=data['slots'][0], room=data['room'],
    )
    ScheduleEntryFaculty.objects.create(schedule_entry=entry, faculty=data['faculty'])
    other = type(data['version']).objects.create(
        timetable=data['timetable'], version_no=2, created_by=data['admin'])
    ScheduleEntry.objects.create(
        version=other, section=data['section'], course_offering=data['offering'],
        weekday=1, start_slot=data['slots'][0], room=data['room'],
    )

    report = verify(data['version'], expected_rows=1)

    assert report['database_entries'] == 1
    assert report['sections'] == report['faculty'] == report['rooms'] == 1
    assert report['course_offerings'] == 1
    assert report['passed'] is True


@pytest.mark.django_db
def test_verify_timetable_import_command_reports_failures(data):
    ScheduleEntry.objects.create(
        version=data['version'], section=data['section'], course_offering=data['offering'],
        weekday=0, start_slot=data['slots'][0], room=data['room'],
    )
    output = StringIO()

    call_command('verify_timetable_import', version_id=str(data['version'].pk),
                 expected_rows=2, stdout=output)

    rendered = output.getvalue()
    assert 'Database Entries: 1' in rendered
    assert 'Missing Relationships: 1' in rendered
    assert 'Final Result: FAIL' in rendered
