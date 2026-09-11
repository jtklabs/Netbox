import uuid
import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_discovery', '0006_override_model'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [migrations.CreateModel(
        name='UpgradeJob',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
            ('created', models.DateTimeField(auto_now_add=True, null=True)),
            ('last_updated', models.DateTimeField(auto_now=True, null=True)),
            ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder)),
            ('description', models.CharField(blank=True, max_length=200)),
            ('comments', models.TextField(blank=True)),
            ('batch_id', models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)),
            ('address', models.GenericIPAddressField(editable=False)),
            ('device_name', models.CharField(max_length=64, editable=False)),
            ('profile', models.JSONField(help_text='Snapshot of the validated IOS XE upgrade profile')),
            ('operation', models.CharField(max_length=16, default='audit')),
            ('scheduled_at', models.DateTimeField()),
            ('start_before', models.DateTimeField(help_text='Latest time device changes may begin; ongoing recovery continues')),
            ('status', models.CharField(max_length=32, default='pending')),
            ('claimed_at', models.DateTimeField(null=True, blank=True)),
            ('started_at', models.DateTimeField(null=True, blank=True)),
            ('completed_at', models.DateTimeField(null=True, blank=True)),
            ('last_seen_at', models.DateTimeField(null=True, blank=True)),
            ('claim_token', models.UUIDField(null=True, blank=True, editable=False)),
            ('sequence', models.PositiveIntegerField(default=0, editable=False)),
            ('stage', models.CharField(max_length=50, blank=True)),
            ('message', models.CharField(max_length=1000, blank=True)),
            ('summary', models.JSONField(default=dict, blank=True)),
            ('events', models.JSONField(default=list, blank=True)),
            ('run_id', models.CharField(max_length=100, blank=True)),
            ('device', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='upgrade_jobs', to='dcim.device')),
            ('poller', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='upgrade_jobs', to='netbox_discovery.discoverypoller')),
            ('requested_by', models.ForeignKey(on_delete=django.db.models.deletion.SET_NULL, null=True, related_name='+', to=settings.AUTH_USER_MODEL)),
            ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='users.owner')),
            ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
        ],
        options={
            'ordering': ('-scheduled_at', '-pk'),
            'permissions': [('run_upgradejob', 'Execute scheduled upgrade jobs'), ('apply_upgradejob', 'Schedule image staging and upgrades')],
            'indexes': [models.Index(fields=('poller', 'status', 'scheduled_at'), name='upgrade_due_idx')],
            'constraints': [models.UniqueConstraint(fields=('device',), condition=models.Q(status__in=('claimed', 'running', 'recovery_required')), name='one_active_upgrade_per_device')],
        },
        bases=(netbox.models.deletion.DeleteMixin, models.Model),
    )]
