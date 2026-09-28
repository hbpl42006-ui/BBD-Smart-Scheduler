from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('scheduling', '0003_timetableversion_approved_at_and_more')]

    operations = [
        migrations.AddField(
            model_name='scheduleentry',
            name='delivery_mode',
            field=models.CharField(choices=[('ONLINE', 'Online'), ('OFFLINE', 'Offline')], default='OFFLINE', max_length=10),
        ),
    ]
