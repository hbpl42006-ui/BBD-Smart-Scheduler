from django.db import migrations, models
import django.db.models.deletion


def configure_reserved_rooms(apps, schema_editor):
    Room = apps.get_model('rooms', 'Room')
    Program = apps.get_model('institutions', 'Program')
    programs = list(Program.objects.all())
    mtech_programs = [
        program for program in programs
        if program.code.casefold().startswith('mtech')
        or program.name.casefold().startswith('m.tech')
        or 'master of technology' in program.name.casefold()
    ]
    program_id = mtech_programs[0].pk if len(mtech_programs) == 1 else None
    for year, code in ((1, '516'), (2, '604')):
        Room.objects.filter(code=code).update(
            exclusive_reservation=True,
            reserved_year=year,
            reserved_program_id=program_id,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('institutions', '0001_initial'),
        ('rooms', '0004_room_descriptive_lab_types'),
    ]

    operations = [
        migrations.AddField(
            model_name='room', name='reserved_program',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reserved_rooms', to='institutions.program'),
        ),
        migrations.AddField(
            model_name='room', name='reserved_year',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='room', name='exclusive_reservation',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(configure_reserved_rooms, migrations.RunPython.noop),
    ]
