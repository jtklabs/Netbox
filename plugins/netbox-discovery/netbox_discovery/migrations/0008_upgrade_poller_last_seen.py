from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0007_upgradejob')]

    operations = [migrations.AddField(
        model_name='discoverypoller',
        name='upgrade_last_seen_at',
        field=models.DateTimeField(
            blank=True, null=True, editable=False,
            help_text='Last upgrade-worker check-in, heartbeat or accepted progress report; excludes SNMP-only activity',
        ),
    )]
