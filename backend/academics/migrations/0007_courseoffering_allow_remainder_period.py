from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('academics', '0006_section_hybrid_delivery')]

    operations = [
        migrations.AddField(
            model_name='courseoffering',
            name='allow_remainder_period',
            field=models.BooleanField(default=False),
        ),
    ]
