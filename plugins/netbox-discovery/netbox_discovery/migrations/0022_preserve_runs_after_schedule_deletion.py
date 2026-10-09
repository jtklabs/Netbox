from django.db import migrations, models
import django.db.models.deletion


def preserve_schedule_names(apps, schema_editor):
    Schedule = apps.get_model('netbox_discovery', 'AuditSchedule')
    Run = apps.get_model('netbox_discovery', 'AuditRun')
    database = schema_editor.connection.alias
    for pk, name in Schedule.objects.using(database).values_list('pk', 'name').iterator():
        Run.objects.using(database).filter(schedule_id=pk).update(schedule_name=name)


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0021_auditschedule_remediate')]

    operations = [
        migrations.AddField(
            model_name='auditrun', name='schedule_name',
            field=models.CharField(max_length=100, blank=True, editable=False),
        ),
        migrations.RunPython(preserve_schedule_names, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='auditrun', name='schedule',
            field=models.ForeignKey(to='netbox_discovery.auditschedule',
                                    on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='runs', null=True, blank=True),
        ),
    ]
