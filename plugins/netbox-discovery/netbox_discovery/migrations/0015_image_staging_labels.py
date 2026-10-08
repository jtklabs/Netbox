from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0014_saved_job_profiles')]

    operations = [
        migrations.AlterModelOptions(
            name='prestagepolicy',
            options={
                'ordering': ('device_type__manufacturer__name', 'device_type__model'),
                'verbose_name': 'image staging policy',
                'verbose_name_plural': 'automatic image staging',
            },
        ),
    ]
