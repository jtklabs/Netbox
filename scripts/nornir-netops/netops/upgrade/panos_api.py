"""Bounded XML API transport, including Panorama serial targeting.

No write is retried automatically. A lost response to install or reboot is an
uncertain outcome which the workflow must reconcile or fence for recovery.
"""
import hashlib
import xml.etree.ElementTree as ET
from types import SimpleNamespace

from .. import cancellation
from ..debuglog import protect


class ApiError(RuntimeError):
    pass


class Client:
    vsys = None

    def __init__(self, host, settings, member, *, central=False):
        import requests
        self.settings, self.member = settings, member
        self.target = member['serial'] if settings.get('panorama') and not central else None
        address = settings.get('panorama') or member['address']
        address = f'[{address}]' if ':' in address else address
        self.url = f"https://{address}:{settings.get('port', 443)}/api/"
        self.session = requests.Session()
        self.session.verify = settings.get('verify_tls', True)
        self.session.trust_env = False
        self.xapi = self  # Read-only FirewallProxy adapter; credentials stay here.
        self.key = None
        try:
            root = self.call({'type': 'keygen', 'user': host.username, 'password': host.password}, targeted=False)
            self.key = root.findtext('./result/key')
            if not self.key:
                raise ApiError('PAN-OS API authentication returned no key')
            protect([self.key, host.password])
        except Exception:
            self.close()
            raise

    def close(self):
        self.session.close()
        self.key = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def call(self, data, *, targeted=True, timeout=90, upload=None):
        import requests
        cancellation.checkpoint()
        fields = dict(data)
        if targeted and self.target:
            fields['target'] = self.target
        headers = {'X-PAN-KEY': self.key} if self.key else {}
        if upload is not None:
            from requests_toolbelt import MultipartEncoder
            fields = MultipartEncoder(fields={**fields, 'file': upload})
            headers['Content-Type'] = fields.content_type
        try:
            response = self.session.post(self.url, data=fields, headers=headers, timeout=timeout, allow_redirects=False)
            if response.status_code != 200:
                raise ApiError(f'PAN-OS API returned HTTP {response.status_code}')
            root = ET.fromstring(response.content)
        except requests.RequestException as exc:
            raise ApiError(f'PAN-OS API transport failed ({type(exc).__name__}); outcome may be unknown') from None
        except ET.ParseError:
            raise ApiError('PAN-OS API returned malformed XML') from None
        if root.tag != 'response' or root.get('status') != 'success':
            # XML errors may echo credentials/configuration; retain no raw body.
            raise ApiError(f"PAN-OS rejected request (code {root.get('code', 'unknown')})")
        if root.find('result') is None and root.find('msg') is None:
            raise ApiError('PAN-OS API response has no result or acknowledgment')
        return root

    def op(self, cmd, xml=False, cmd_xml=True, vsys=None):
        if cmd_xml and not cmd.lstrip().startswith('<'):
            from panos import string_to_xml
            cmd = string_to_xml(cmd)
        root = self.call({'type': 'op', 'cmd': cmd})
        return ET.tostring(root) if xml else root

    def get(self, xpath):
        return self.call({'type': 'config', 'action': 'get', 'xpath': xpath})

    def result(self, cmd):
        result = self.op(cmd).find('result')
        if result is None:
            raise ApiError('PAN-OS read returned no result')
        return result

    def system(self):
        node = self.result('show system info').find('system')
        if node is None or not all(node.findtext(k) for k in ('hostname', 'model', 'sw-version', 'serial', 'uptime')):
            raise ApiError('PAN-OS system identity is incomplete')
        return {child.tag: child.text for child in node}

    def refresh_system_info(self):
        info = self.system()
        self.version = info['sw-version']
        return SimpleNamespace(version=self.version, model=info['model'], serial=info['serial'])

    def get_device_version(self):
        return self.refresh_system_info().version

    def installed_images(self, batch=False):
        result = self.result('request batch software info' if batch else 'request system software info')
        versions = result.find('sw-updates/versions')
        if versions is None:
            raise ApiError('PAN-OS software inventory is unreadable')
        return {e.findtext('version'): {n.tag: n.text for n in e} for e in versions.findall('entry')}

    def upload(self, path):
        if self.target:
            raise ValueError('import software to Panorama first, then distribute with upload-only')
        with path.open('rb') as handle:
            self.call({'type': 'import', 'category': 'software'},
                      upload=(path.name, handle, 'application/octet-stream'), timeout=1800)

    def distribute(self, filename, serial):
        if self.target:
            raise ValueError('software distribution must target Panorama itself')
        root = ET.Element('request')
        upload = ET.SubElement(ET.SubElement(ET.SubElement(root, 'batch'), 'software'), 'upload')
        ET.SubElement(upload, 'file').text = filename
        ET.SubElement(upload, 'devices').text = serial
        response = self.op(ET.tostring(root, encoding='unicode'))
        job = response.findtext('./result/job')
        if not job or not job.isdigit():
            raise ApiError('Panorama upload-only returned no job ID; inspect jobs before retrying')
        return job

    def install(self, version):
        root = ET.Element('request')
        install = ET.SubElement(ET.SubElement(ET.SubElement(root, 'system'), 'software'), 'install')
        ET.SubElement(install, 'version').text = version
        response = self.op(ET.tostring(root, encoding='unicode'))
        job = response.findtext('./result/job')
        if not job or not job.isdigit():
            raise ApiError('PAN-OS install returned no job ID; inspect jobs before retrying')
        return job

    def job(self, job_id):
        if not str(job_id).isdigit():
            raise ValueError('invalid PAN-OS job ID')
        job = self.result(f'<show><jobs><id>{job_id}</id></jobs></show>').find('job')
        if job is None or job.findtext('id') != str(job_id):
            raise ApiError('PAN-OS returned no matching asynchronous job')
        row = {key: job.findtext(key) for key in ('id', 'type', 'status', 'result', 'progress')}
        row['devices'] = [{key: entry.findtext(key) for key in ('serial-no', 'status', 'result')}
                          for entry in job.findall('./devices/entry')]
        return row

    def ha_state(self, state):
        if state not in {'suspend', 'functional'}:
            raise ValueError('unsupported HA state')
        return self.op(f'<request><high-availability><state><{state}/></state></high-availability></request>')

    def reboot(self):
        return self.op('<request><restart><system/></restart></request>')

    def configuration(self):
        node = self.result('show config running').find('config')
        if node is None or node.find('devices') is None:
            raise ApiError('PAN-OS running configuration is incomplete')
        return ET.tostring(node, encoding='utf-8')


def configuration_digest(raw):
    """Hash semantics without publishing secrets; rule/member order is significant."""
    node = ET.fromstring(raw)
    for element in node.iter():
        for name in ('admin', 'dirtyId', 'time'):
            element.attrib.pop(name, None)
        if element is node:
            element.attrib.pop('version', None)
        element.attrib.update(sorted(element.attrib.items()))
        element.tail = None
        if element.text is not None:
            element.text = element.text.strip() or None
    canonical = ET.canonicalize(ET.tostring(node, encoding='unicode'))
    return hashlib.sha256(canonical.encode()).hexdigest()
