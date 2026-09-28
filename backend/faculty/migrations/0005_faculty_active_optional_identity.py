from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('faculty', '0004_facultyarrangement_arrangementattendanceevidence_and_more')]
    operations = [
        migrations.AlterField(model_name='faculty', name='employee_code', field=models.CharField(blank=True, max_length=50, null=True, unique=True)),
        migrations.AlterField(model_name='faculty', name='initials', field=models.CharField(blank=True, default='', max_length=10)),
        migrations.AddField(model_name='faculty', name='active', field=models.BooleanField(default=True)),
    ]
