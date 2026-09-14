import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


def primary_fields():
    return [
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
        ('created', models.DateTimeField(auto_now_add=True, null=True)),
        ('last_updated', models.DateTimeField(auto_now=True, null=True)),
        ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder)),
        ('description', models.CharField(blank=True, max_length=200)),
        ('comments', models.TextField(blank=True)),
    ]


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_discovery', '0008_upgrade_poller_last_seen'),
        ('dcim', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='UpgradeGroup',
            fields=primary_fields() + [
                ('name', models.CharField(max_length=100, unique=True)),
                ('max_concurrent', models.PositiveSmallIntegerField(default=1)),
                ('source', models.CharField(default='manual', max_length=20)),
                ('key', models.CharField(blank=True, db_index=True, max_length=200)),
                ('stale', models.BooleanField(default=False)),
                ('members', models.ManyToManyField(blank=True, related_name='upgrade_groups', to='dcim.device')),
                ('depends_on', models.ManyToManyField(blank=True, related_name='dependents', to='netbox_discovery.upgradegroup')),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='users.owner')),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
            ],
            options={'ordering': ('name',)},
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.CreateModel(
            name='UpgradeDependency',
            fields=primary_fields() + [
                ('source', models.CharField(default='manual', max_length=20)),
                ('key', models.CharField(blank=True, db_index=True, max_length=200)),
                ('stale', models.BooleanField(default=False)),
                ('upstream', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='upgrade_downstream', to='dcim.device')),
                ('downstream', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='upgrade_upstream', to='dcim.device')),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='users.owner')),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
            ],
            options={'ordering': ('upstream__name', 'downstream__name'),
                     'constraints': [models.UniqueConstraint(fields=('upstream', 'downstream'), name='upgrade_dependency_unique')]},
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.AddField(model_name='upgradejob', name='groups', field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='upgradejob', name='waits_for', field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='upgradejob', name='held_reason', field=models.CharField(blank=True, max_length=1000)),
        migrations.AddField(model_name='upgradejob', name='planned_wave', field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(model_name='upgradejob', name='acknowledged', field=models.JSONField(blank=True, default=list)),
    ]
