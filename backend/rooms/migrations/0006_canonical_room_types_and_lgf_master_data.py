from django.db import migrations, models


ROOM_TYPES = {
    'ELECTRICAL LAB': 'ELECTRICAL_LAB',
    'MECHANICS LAB': 'MECHANICS_LAB',
    'ENGINEERING GRAPHICS LAB': 'ENGINEERING_GRAPHICS_LAB',
    'PHYSICS LAB': 'PHYSICS_LAB',
    'SUSTAINABLE CHEMICAL SCIENCES LAB': 'SUSTAINABLE_CHEMICAL_SCIENCES_LAB',
}

LGF_TYPES = {
    'LGF001': 'ELECTRICAL_LAB',
    'LGF002': 'ELECTRICAL_LAB',
    'LGF007': 'WORKSHOP',
    'LGF008': 'MECHANICS_LAB',
    'LGF011': 'ENGINEERING_GRAPHICS_LAB',
    'LGF012': 'PHYSICS_LAB',
}


def canonicalize_room_types(apps, schema_editor):
    Room = apps.get_model('rooms', 'Room')
    for old_value, new_value in ROOM_TYPES.items():
        Room.objects.filter(room_type=old_value).update(room_type=new_value)
    for code, room_type in LGF_TYPES.items():
        Room.objects.filter(code=code).update(room_type=room_type)


class Migration(migrations.Migration):
    dependencies = [('rooms', '0005_room_exclusive_program_year_reservation')]

    operations = [
        migrations.RunPython(canonicalize_room_types, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='room',
            name='room_type',
            field=models.CharField(
                choices=[
                    ('CLASSROOM', 'Classroom'),
                    ('COMPUTER_LAB', 'Computer Lab'),
                    ('LAB', 'Lab'),
                    ('SEMINAR_HALL', 'Seminar Hall'),
                    ('AUDITORIUM', 'Auditorium'),
                    ('ELECTRICAL_LAB', 'Electrical Lab'),
                    ('WORKSHOP', 'Workshop'),
                    ('MECHANICS_LAB', 'Mechanics Lab'),
                    ('ENGINEERING_GRAPHICS_LAB', 'Engineering Graphics Lab'),
                    ('PHYSICS_LAB', 'Physics Lab'),
                    ('SUSTAINABLE_CHEMICAL_SCIENCES_LAB', 'Sustainable Chemical Sciences Lab'),
                    ('OTHER', 'Other'),
                ],
                default='CLASSROOM',
                max_length=50,
            ),
        ),
    ]
