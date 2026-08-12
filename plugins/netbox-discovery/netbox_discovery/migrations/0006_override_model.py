from django.db import migrations, models


class Migration(migrations.Migration):
    """A reviewer-supplied model, for platforms that publish none.

    Blank by default and never read unless the scan found no model of its own,
    so this changes nothing about any request already in the queue.
    """

    dependencies = [
        ('netbox_discovery', '0005_discoveryissue'),
    ]

    operations = [
        migrations.AddField(
            model_name='onboardingrequest',
            name='override_model',
            field=models.CharField(
                blank=True,
                # An explicit default rather than relying on Django to supply
                # '' from the model. The column is NOT NULL, so during a deploy
                # where the migration has run but the application has not yet
                # been restarted, the still-old code inserts rows without this
                # column and every write fails on the constraint. A database
                # default makes that window harmless instead of an outage.
                default='',
                max_length=100,
                help_text='Used when the device reports no model of its own. Some '
                          'platforms publish none at all — a Firepower 2120 among '
                          'them — and without one there is no device type to create.',
                verbose_name='Model override',
            ),
        ),
    ]
