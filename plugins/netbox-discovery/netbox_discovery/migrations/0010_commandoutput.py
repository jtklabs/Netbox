import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import netbox_discovery.command_models


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_discovery', '0009_upgrade_groups'),
        ('dcim', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CommandOutput',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('platform', models.CharField(blank=True, max_length=50)),
                ('command', models.CharField(max_length=200)),
                ('filename', models.CharField(max_length=200)),
                ('file', models.FileField(upload_to=netbox_discovery.command_models.command_output_path)),
                ('size', models.PositiveIntegerField(default=0)),
                ('sha256', models.CharField(blank=True, max_length=64)),
                ('ok', models.BooleanField(default=True)),
                ('error', models.TextField(blank=True)),
                ('collected_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated', models.DateTimeField(auto_now=True)),
                ('device', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='command_outputs', to='dcim.device')),
                ('poller', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='netbox_discovery.discoverypoller')),
            ],
            options={
                'verbose_name': 'command output',
                'ordering': ('device', 'command'),
                'constraints': [models.UniqueConstraint(fields=('device', 'command'), name='one_output_per_device_command')],
            },
        ),
    ]
