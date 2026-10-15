"""Authenticated UCS downloads from explicitly configured, private poller roots."""
import os
import re
import stat

from django.conf import settings
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views import View
from dcim.models import Device

from .models import UpgradeJob


def backup_record(job):
    record = (job.summary or {}).get('ucs_backup')
    if not isinstance(record, dict):
        return None
    name = record.get('name', '')
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*\.ucs', name)
            or record.get('md5_verified') is not True or type(record.get('bytes')) is not int
            or record['bytes'] <= 0):
        return None
    return record


def open_backup(job):
    """Never use a worker-supplied path; reject symlinks and incomplete files."""
    record = backup_record(job)
    roots = settings.PLUGINS_CONFIG['netbox_discovery'].get('ucs_download_roots', {})
    root = roots.get(str(job.poller_id))
    if not record or not root:
        raise FileNotFoundError
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(record['name'], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != record['bytes']:
            raise FileNotFoundError
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def backup_context(job, user):
    if not (user.has_perm('netbox_discovery.view_upgradejob')
            and user.has_perm('netbox_discovery.apply_upgradejob')):
        return None
    if not UpgradeJob.objects.restrict(user, 'view').restrict(user, 'apply').filter(pk=job.pk).exists():
        return None
    if not Device.objects.restrict(user, 'view').filter(pk=job.device_id).exists():
        return None
    record = backup_record(job)
    if not record:
        return None
    try:
        with open_backup(job):
            available = True
    except OSError:
        available = False
    return {'job': job, 'name': record['name'], 'bytes': record['bytes'],
            'encrypted': record.get('encrypted', False), 'available': available}


class UpgradeBackupDownloadView(PermissionRequiredMixin, View):
    permission_required = ('netbox_discovery.view_upgradejob', 'netbox_discovery.apply_upgradejob')
    raise_exception = True

    def get(self, request, pk):
        job = get_object_or_404(UpgradeJob.objects.restrict(request.user, 'view').restrict(request.user, 'apply'), pk=pk)
        get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=job.device_id)
        try:
            handle = open_backup(job)
        except OSError as exc:
            raise Http404('Verified UCS backup is not available on this NetBox server.') from exc
        response = FileResponse(handle, as_attachment=True, filename=backup_record(job)['name'],
                                content_type='application/octet-stream')
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
