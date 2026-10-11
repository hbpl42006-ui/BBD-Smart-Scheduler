import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

from academics.models import CourseOffering, Section
from rooms.models import Room
from scheduling.management.commands import repair_course_offering_room_requirements_for_v6 as command
from scheduling.models import ScheduleEntry, TimetableVersion
from scheduling.services.room_requirement_repair import plan_room_requirement_repair
from scheduling.test_scheduling import data


@pytest.mark.django_db
def test_dry_run_apply_idempotence_and_published_entries_untouched(data, tmp_path):
    data['course'].name = 'Computer Networks Lab'
    data['course'].save(update_fields=['name'])
    sections = [data['section']]
    for name in ('B', 'C'):
        sections.append(Section.objects.create(
            program=data['program'], semester=data['semester'], year=1,
            name=name, student_strength=30,
        ))
    offerings = [data['offering']]
    for section in sections[1:]:
        offerings.append(CourseOffering.objects.create(
            semester=data['semester'], section=section, course=data['course'],
            weekly_periods=2, default_class_type='PRACTICAL',
            room_type_requirement='CLASSROOM',
        ))
    offerings[0].default_class_type = 'PRACTICAL'
    offerings[0].room_type_requirement = 'CLASSROOM'
    offerings[0].save(update_fields=['default_class_type', 'room_type_requirement'])
    published = TimetableVersion.objects.create(
        timetable=data['timetable'], version_no=2,
        status=TimetableVersion.Status.PUBLISHED, created_by=data['admin'],
    )
    published_entry = ScheduleEntry.objects.create(
        version=published, section=data['section'], course_offering=offerings[0],
        weekday=0, start_slot=data['slots'][0], room=data['room'],
    )
    report = {
        'summary': {'total': 6, 'valid': 0, 'invalid': 6},
        'room_type_mismatches': [
            {
                'offering_id': str(offering.pk), 'section': offering.section.name,
                'course_code': data['course'].code, 'room_code': 'LAB601',
                'room_type': 'COMPUTER_LAB', 'offering_class_type': 'PRACTICAL',
                'requirement': 'CLASSROOM', 'rows': [index * 2 + 2, index * 2 + 3],
                'equivalent_offering_requirements': ['CLASSROOM'],
            }
            for index, offering in enumerate(offerings)
        ],
    }
    Room.objects.create(code='LAB601', building='Main', floor='6', capacity=60, room_type='COMPUTER_LAB')
    workbook = tmp_path / 'source.csv'
    workbook.write_text('Section,Course Code,Room No.\nA,CS101,LAB601\nB,CS101,LAB601\nC,CS101,LAB601\n', encoding='utf-8')
    args = ['--workbook', str(workbook)]
    with (patch.object(command, 'VERSION_ID', str(data['version'].pk)),
          patch.object(command, 'audit', return_value=report),
          patch.object(command, 'backup_sqlite', return_value=tmp_path / 'backup.sqlite3') as backup):
        output = StringIO()
        call_command('repair_course_offering_room_requirements_for_v6', *args, stdout=output)
        dry_run = json.loads(output.getvalue())
        assert dry_run['classifications']['CLEAR_REQUIREMENT_ERROR'] == 3
        assert dry_run['offerings_changed'] == 0
        assert all(CourseOffering.objects.get(pk=offering.pk).room_type_requirement == 'CLASSROOM' for offering in offerings)
        backup.assert_not_called()

        output = StringIO()
        call_command('repair_course_offering_room_requirements_for_v6', *args, '--apply', stdout=output)
        applied = json.loads(output.getvalue())
        assert applied['offerings_changed'] == 3
        backup.assert_called_once()
        assert all(CourseOffering.objects.get(pk=offering.pk).room_type_requirement == 'COMPUTER_LAB' for offering in offerings)

        output = StringIO()
        call_command('repair_course_offering_room_requirements_for_v6', *args, '--apply', stdout=output)
        repeated = json.loads(output.getvalue())
        assert repeated['offerings_changed'] == 0
        assert repeated['classifications'].get('CLEAR_REQUIREMENT_ERROR', 0) == 0
        backup.assert_called_once()
    assert ScheduleEntry.objects.filter(pk=published_entry.pk, version=published, room=data['room']).exists()


@pytest.mark.django_db
def test_mixed_classroom_and_lab_source_is_not_auto_repaired(data):
    data['course'].name = 'Computer Networks Lab'
    data['course'].save(update_fields=['name'])
    data['offering'].default_class_type = 'PRACTICAL'
    data['offering'].room_type_requirement = 'CLASSROOM'
    data['offering'].save(update_fields=['default_class_type', 'room_type_requirement'])
    Room.objects.create(code='LAB601', building='Main', floor='6', capacity=60, room_type='COMPUTER_LAB')
    items = []
    rows = []
    for index, name in enumerate(('A', 'B', 'C')):
        section = data['section'] if index == 0 else Section.objects.create(
            program=data['program'], semester=data['semester'], year=1,
            name=name, student_strength=30,
        )
        offering = data['offering'] if index == 0 else CourseOffering.objects.create(
            semester=data['semester'], section=section, course=data['course'],
            weekly_periods=2, default_class_type='PRACTICAL', room_type_requirement='CLASSROOM',
        )
        items.append({
            'offering_id': str(offering.pk), 'section': name,
            'course_code': data['course'].code, 'room_code': 'LAB601',
            'room_type': 'COMPUTER_LAB', 'offering_class_type': 'PRACTICAL',
            'requirement': 'CLASSROOM', 'rows': [index + 2],
            'equivalent_offering_requirements': ['CLASSROOM'],
        })
        rows.append({'Section': name, 'Course Code': data['course'].code, 'Room No.': 'LAB601'})
    rows.append({'Section': 'A', 'Course Code': data['course'].code, 'Room No.': 'R101'})
    decisions = plan_room_requirement_repair({'room_type_mismatches': items}, rows)
    assert {item['section'] for item in decisions if item['classification'] == 'CLEAR_REQUIREMENT_ERROR'} == {'B', 'C'}
    mixed = next(item for item in decisions if item['section'] == 'A')
    assert mixed['classification'] == 'NEEDS_REVIEW'
    assert mixed['all_source_room_types'] == ['CLASSROOM', 'COMPUTER_LAB']
