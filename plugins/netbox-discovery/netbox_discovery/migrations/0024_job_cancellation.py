from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_discovery', '0023_worker_version'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name='upgradejob', name='supports_cancellation',
                            field=models.BooleanField(default=False, editable=False)),
        migrations.AddField(model_name='upgradejob', name='cancel_requested_at',
                            field=models.DateTimeField(null=True, blank=True, editable=False)),
        migrations.AddField(model_name='upgradejob', name='cancel_requested_by',
                            field=models.ForeignKey(to=settings.AUTH_USER_MODEL, on_delete=django.db.models.deletion.SET_NULL,
                                                    null=True, blank=True, editable=False, related_name='+')),
        migrations.AddField(model_name='upgradejob', name='cancel_reason',
                            field=models.CharField(max_length=1000, blank=True, editable=False)),
        migrations.AddField(model_name='upgradejob', name='cancel_acknowledged_at',
                            field=models.DateTimeField(null=True, blank=True, editable=False)),
    ]
