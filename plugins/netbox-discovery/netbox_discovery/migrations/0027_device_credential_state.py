import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0026_f5_validation_overrides')]
    operations = [migrations.CreateModel(
        name='DeviceCredentialState',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
            ('transport', models.CharField(max_length=5, choices=[('ssh', 'SSH'), ('https', 'HTTPS')])),
            ('assigned_profile', models.CharField(max_length=32, blank=True)),
            ('allow_fallback', models.BooleanField(default=False)),
            ('selected_profile', models.CharField(max_length=32, blank=True)),
            ('verified_at', models.DateTimeField(null=True, blank=True)),
            ('status', models.CharField(max_length=20, default='unknown')),
            ('attempted', models.JSONField(default=list)),
            ('lease_token', models.UUIDField(null=True)),
            ('lease_until', models.DateTimeField(null=True)),
            ('candidates', models.JSONField(default=list)),
            ('cooldown_until', models.DateTimeField(null=True)),
            ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                        related_name='credential_states', to='dcim.device')),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('device', 'transport'), name='unique_device_credential_transport')]},
    ), migrations.AddField(model_name='discoverypoller', name='credential_health',
                          field=models.JSONField(default=dict, blank=True, editable=False))]
