from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('netbox_discovery', '0027_device_credential_state')]
    operations = [migrations.RemoveField(model_name='devicecredentialstate', name='allow_fallback')]
