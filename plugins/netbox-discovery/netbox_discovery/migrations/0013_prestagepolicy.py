import django.core.validators
import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_discovery', '0012_strippeddomain'),
    ]

    operations = [
        migrations.CreateModel(
            name='PrestagePolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('comments', models.TextField(blank=True)),
                ('enabled', models.BooleanField(default=True)),
                ('interval_hours', models.PositiveSmallIntegerField(default=24, validators=[django.core.validators.MinValueValidator(1)])),
                ('window_hours', models.PositiveSmallIntegerField(default=4, validators=[django.core.validators.MinValueValidator(1)])),
                ('minimum_free_bytes', models.BigIntegerField(blank=True, null=True, validators=[django.core.validators.MinValueValidator(1)])),
                ('last_run_at', models.DateTimeField(blank=True, editable=False, null=True)),
                ('last_summary', models.JSONField(blank=True, default=dict, editable=False)),
                ('device_type', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='prestage_policy', to='dcim.devicetype')),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='users.owner')),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
            ],
            options={
                'verbose_name': 'prestage policy',
                'verbose_name_plural': 'prestage policies',
                'ordering': ('device_type__manufacturer__name', 'device_type__model'),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
    ]
