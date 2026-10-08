from django.db import migrations, models


FIRST_YEAR_ONLY_ROOM_CODES = ('LGF001', 'LGF002', 'LGF007', 'LGF008', 'LGF011', 'LGF012', '110')


def restrict_first_year_rooms(apps, schema_editor):
    Room = apps.get_model('rooms', 'Room')
    Room.objects.filter(code__in=FIRST_YEAR_ONLY_ROOM_CODES).update(allowed_year=1)


class Migration(migrations.Migration):
    dependencies = [('rooms', '0002_room_has_projector')]
    operations = [
        migrations.AddField(
            model_name='room',
            name='allowed_year',
            field=models.PositiveSmallIntegerField(blank=True, help_text='Null means available to all academic years.', null=True),
        ),
        migrations.RunPython(restrict_first_year_rooms, migrations.RunPython.noop),
    ]
