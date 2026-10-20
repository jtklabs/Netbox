from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0024_job_cancellation')]
    operations = [
        migrations.AddField(model_name='upgradejob', name='rollback_of',
            field=models.ForeignKey(to='netbox_discovery.upgradejob', on_delete=django.db.models.deletion.PROTECT,
                                    null=True, blank=True, editable=False, related_name='rollback_jobs')),
        migrations.AddField(model_name='upgradejob', name='rollback_plan',
            field=models.JSONField(default=dict, blank=True, editable=False)),
        migrations.AlterField(model_name='upgradejob', name='operation',
            field=models.CharField(max_length=16, default='audit', choices=[
                ('audit', 'Pre-upgrade audit'), ('audit_config', 'Audit configuration standards (read-only)'),
                ('stage', 'Stage image only'), ('upgrade', 'Install upgrade'),
                ('f5_rollback', 'F5 boot partition rollback'), ('remediate', 'Remediate configuration')])),
    ]
