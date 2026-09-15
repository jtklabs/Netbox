import django.db.models.deletion
import netbox.models.deletion
import taggit.managers
import utilities.json
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_discovery', '0010_commandoutput'),
    ]

    operations = [
        migrations.CreateModel(
            name='DiscoveryRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('custom_field_data', models.JSONField(blank=True, default=dict, encoder=utilities.json.CustomFieldJSONEncoder)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('comments', models.TextField(blank=True)),
                ('name', models.CharField(max_length=100, unique=True)),
                ('enabled', models.BooleanField(default=True)),
                ('weight', models.PositiveSmallIntegerField(default=100)),
                ('match_field', models.CharField(default='name', max_length=30)),
                ('match_operator', models.CharField(default='contains', max_length=20)),
                ('match_value', models.CharField(max_length=200)),
                ('set_field', models.CharField(default='model', max_length=30)),
                ('set_value', models.CharField(max_length=200)),
                ('only_if_blank', models.BooleanField(default=True)),
                ('owner', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='users.owner')),
                ('tags', taggit.managers.TaggableManager(through='extras.TaggedItem', to='extras.Tag')),
            ],
            options={
                'verbose_name': 'discovery rule',
                'verbose_name_plural': 'discovery rules',
                'ordering': ('weight', 'name'),
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
    ]
