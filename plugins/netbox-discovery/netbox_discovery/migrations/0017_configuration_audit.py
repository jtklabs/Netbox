from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0016_upgradejob_standards_snapshot')]

    operations = [
        migrations.AlterField(
            model_name='upgradejob', name='operation',
            field=models.CharField(max_length=16, default='audit', choices=[
                ('audit', 'Pre-upgrade audit'),
                ('audit_config', 'Audit configuration standards (read-only)'),
                ('stage', 'Stage image only'), ('upgrade', 'Install upgrade'),
                ('remediate', 'Remediate configuration'),
            ]),
        ),
    ]
