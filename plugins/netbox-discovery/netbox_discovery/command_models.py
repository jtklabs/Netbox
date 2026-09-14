"""Show-command outputs collected by the pollers and stored as files in NetBox.

A plain model on purpose: one row per device and command, replaced on every
collection, holding metadata plus the file in NetBox's media storage. It is
not change-logged, so a nightly sweep of a large fleet leaves no snapshots of
configuration text in the changelog.
"""
import hashlib

from django.core.files.base import ContentFile
from django.db import models
from django.urls import reverse
from django.utils import timezone


def command_output_path(instance, filename):
    return f'discovery/commands/{instance.device_id}/{filename}'


class CommandOutput(models.Model):
    device = models.ForeignKey('dcim.Device', on_delete=models.CASCADE, related_name='command_outputs')
    poller = models.ForeignKey('netbox_discovery.DiscoveryPoller', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    platform = models.CharField(max_length=50, blank=True)
    command = models.CharField(max_length=200)
    filename = models.CharField(max_length=200)
    file = models.FileField(upload_to=command_output_path)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    ok = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    collected_at = models.DateTimeField(default=timezone.now)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('device', 'command')
        constraints = [models.UniqueConstraint(fields=('device', 'command'), name='one_output_per_device_command')]
        verbose_name = 'command output'

    def __str__(self):
        return self.filename

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:commandoutput_raw', args=[self.pk])

    def store(self, text):
        """Replace the stored file with this text and update the metadata."""
        data = text.encode('utf-8')
        if self.file:
            self.file.delete(save=False)
        self.size, self.sha256 = len(data), hashlib.sha256(data).hexdigest()
        self.file.save(self.filename, ContentFile(data), save=False)
