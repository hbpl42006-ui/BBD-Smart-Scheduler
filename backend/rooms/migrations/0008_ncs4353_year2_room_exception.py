from django.db import migrations


REASON = 'Historical/source timetable explicitly schedules NCS4353 Year-2 practicals in LGF001.'


def add_ncs4353_year2_exception(apps, schema_editor):
    Room = apps.get_model('rooms', 'Room')
    Program = apps.get_model('institutions', 'Program')
    Course = apps.get_model('academics', 'Course')
    Exception = apps.get_model('rooms', 'RoomEligibilityException')

    room = Room.objects.filter(code='LGF001').first()
    program = Program.objects.filter(code='BTECH-CSE').first()
    course = Course.objects.filter(code='NCS4353').first()
    if not (room and program and course):
        return

    existing = Exception.objects.filter(room=room, program=program, year=2, course=course).first()
    if existing:
        if existing.allow and existing.active and existing.reason == REASON:
            return
        raise RuntimeError('The scoped LGF001 / BTECH-CSE / Year 2 / NCS4353 eligibility exception already exists with different settings.')

    Exception.objects.create(
        room=room,
        program=program,
        year=2,
        course=course,
        allow=True,
        active=True,
        reason=REASON,
    )


class Migration(migrations.Migration):
    dependencies = [
        ('academics', '0007_courseoffering_allow_remainder_period'),
        ('institutions', '0001_initial'),
        ('rooms', '0007_room_eligibility_exception'),
    ]

    operations = [
        migrations.RunPython(add_ncs4353_year2_exception, migrations.RunPython.noop),
    ]
