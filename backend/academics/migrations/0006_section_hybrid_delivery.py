from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('academics', '0005_alter_courseoffering_course')]

    operations = [
        migrations.AddField(
            model_name='section',
            name='delivery_policy',
            field=models.CharField(choices=[('STANDARD', 'Standard (physical room required)'), ('HYBRID', 'Hybrid (one offline weekday)')], default='STANDARD', max_length=12),
        ),
        migrations.AddField(
            model_name='section',
            name='offline_weekday',
            field=models.PositiveSmallIntegerField(blank=True, choices=[(0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'), (4, 'Friday'), (5, 'Saturday')], null=True),
        ),
    ]
