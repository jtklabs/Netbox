from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0025_f5_job_rollback')]

    operations = [
        migrations.AddField(
            model_name='upgradejob', name='validation_overrides',
            field=models.JSONField(default=dict, blank=True, editable=False),
        ),
    ]
