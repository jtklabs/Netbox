from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0022_preserve_runs_after_schedule_deletion')]

    operations = [
        migrations.AddField(
            model_name='discoverypoller', name='worker_version',
            field=models.CharField(max_length=50, blank=True, editable=False,
                                   help_text='Upgrade / standards worker version reported at check-in'),
        ),
        migrations.AddField(
            model_name='upgradejob', name='worker_version',
            field=models.CharField(max_length=50, blank=True, editable=False,
                                   help_text='Version of the worker that claimed this job'),
        ),
    ]
