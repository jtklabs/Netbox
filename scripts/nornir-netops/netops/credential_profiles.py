"""Resolve locally deployed accounts once, before any device commands are sent.

NetBox stores profile names and serializes discovery across workers. Passwords
never leave the worker except when authenticating to the device itself.
"""
from dataclasses import dataclass, field
import os
import re

from nornir.core.inventory import Host, ConnectionOptions

from .credentials import CredentialError, Credentials
from .debuglog import protect

PROFILE_NAME = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
ENDPOINT = 'plugins/discovery/device-credentials/'


@dataclass(frozen=True)
class Profile:
    name: str
    username: str
    password: str = field(repr=False)
    secret: str = field(default='', repr=False)


def load_profiles():
    names = [name.strip() for name in os.environ.get('NET_CREDENTIAL_PROFILES', '').split(',')]
    if not names or len(names) > 10 or len(set(names)) != len(names) or any(not PROFILE_NAME.fullmatch(n) for n in names):
        raise CredentialError('NET_CREDENTIAL_PROFILES needs 1–10 unique lowercase profile names (letters, digits, underscores)')
    profiles = []
    for name in names:
        prefix = 'NET_' + name.upper()
        username, password = os.environ.get(prefix + '_USER'), os.environ.get(prefix + '_PASS')
        if not username or not password:
            raise CredentialError(f'Profile {name!r} missing on this poller: set {prefix}_USER and {prefix}_PASS')
        profile = Profile(name, username, password, os.environ.get(prefix + '_ENABLE', ''))
        protect([profile.password, profile.secret])
        profiles.append(profile)
    return tuple(profiles)


def rejected(exc):
    """Only a positive login rejection permits trying a different identity."""
    from .errors import authentication_summary, DeviceHTTPError
    if isinstance(exc, DeviceHTTPError):
        return exc.status_code == 401
    from paramiko.ssh_exception import AuthenticationException, BadAuthenticationType, PartialAuthentication
    if isinstance(exc, (BadAuthenticationType, PartialAuthentication)):
        return False
    if isinstance(exc, AuthenticationException):
        if not authentication_summary(exc).startswith('SSH authentication failed'):
            return False
        # Netmiko's wrapper also covers unsupported/partial authentication.
        # Prefer the deepest cause instead of trusting its generic heading.
        current, seen = exc, set()
        while id(current) not in seen:
            seen.add(id(current))
            cause = current.__cause__ or current.__context__
            if cause is None or id(cause) in seen:
                break
            current = cause
        if isinstance(current, (BadAuthenticationType, PartialAuthentication)):
            return False
        detail = str(current).lower()
        return 'authentication failed' in detail or 'authentication to device failed' in detail
    return False


class Selector:
    def __init__(self, host, profiles, client, *, max_attempts=3):
        self.host, self.profiles, self.client = host, {p.name: p for p in profiles}, client
        self.max_attempts = max_attempts
        self.selected = None
        self.error = None

    def api(self, payload):
        return self.client.request_object('POST', ENDPOINT + str(self.host.data['netbox_id']) + '/', payload)

    def use(self, profile):
        self.host.username, self.host.password = profile.username, profile.password
        options = self.host.get_connection_parameters('netmiko')
        extras = dict(options.extras or {})
        extras.pop('key_file', None)
        extras.update(secret=profile.secret, use_keys=False, allow_agent=False)
        # Never mutate inherited/default options shared by other hosts.
        self.host.connection_options['netmiko'] = ConnectionOptions(
            hostname=options.hostname, port=options.port, platform=options.platform,
            username=profile.username, password=profile.password, extras=extras)

    def run(self, authenticate, transport='ssh'):
        if self.error:
            raise CredentialError(self.error)
        if self.selected:
            return authenticate()  # Reconnect with the same identity; never replay work.
        lease = self.api({'action': 'begin', 'transport': transport,
                          'profiles': list(self.profiles), 'max_attempts': self.max_attempts})
        if lease.get('error'):
            self.error = lease['error']
            raise CredentialError(self.error)
        token, candidates = lease.get('token'), lease.get('profiles')
        if not token or not isinstance(candidates, list) or not candidates or any(n not in self.profiles for n in candidates):
            raise CredentialError('NetBox returned an invalid credential selection')
        protect([token])
        attempted = []
        outcome, selected = 'unavailable', ''
        try:
            for name in candidates[:self.max_attempts]:
                self.use(self.profiles[name])
                attempted.append(name)
                outcome = 'unavailable'
                try:
                    connection = authenticate()
                except Exception as exc:
                    if not rejected(exc):
                        self.error = 'Credential verification stopped: connection or authorization failure; no account fallback'
                        raise
                    outcome = 'rejected'
                    continue
                selected, outcome = name, 'authenticated'
                self.selected = self.profiles[name]
                self.host.data['credential_profile'] = name
                print(f'{self.host.name}: authenticated with credential profile {name}', flush=True)
                return connection
            self.error = ('The saved or assigned credential profile was rejected; selection is unchanged. '
                          'Rediscovery must be requested explicitly in NetBox; shared cooldown is active'
                          if lease.get('pinned') else
                          'Device rejected the eligible credential profiles; shared cooldown is active')
            raise CredentialError(self.error)
        finally:
            # A failed writeback stops the caller before it can send commands.
            # An abandoned lease expires on the server, never locally bypassed.
            try:
                result = self.api({'action': 'finish', 'transport': transport, 'token': token,
                                   'attempted': attempted, 'profile': selected, 'status': outcome})
                if result.get('error'):
                    raise CredentialError(result['error'])
            except Exception:
                self.error = 'Credential verification could not be recorded in NetBox; no device work started'
                self.selected = None
                self.host.close_connections()
                raise

    def credentials(self):
        if not self.selected:
            raise CredentialError('A device profile has not been selected')
        return Credentials(username=self.selected.username, password=self.selected.password,
                           secret=self.selected.secret, source='profile ' + self.selected.name)


class ProfileHost(Host):
    __slots__ = ('selector',)

    def get_connection(self, connection, configuration):
        def connect():
            return super(ProfileHost, self).get_connection(connection, configuration)
        if connection == 'netmiko' and connection not in self.connections:
            return self.selector.run(connect)
        return connect()


def prepare(nr, credentials, client=None):
    if not getattr(credentials, 'profiles', ()):
        return
    try:
        attempts = int(os.environ.get('NET_CREDENTIAL_MAX_ATTEMPTS', '3'))
    except ValueError:
        raise CredentialError('NET_CREDENTIAL_MAX_ATTEMPTS must be 1, 2 or 3') from None
    if attempts not in (1, 2, 3):
        raise CredentialError('NET_CREDENTIAL_MAX_ATTEMPTS must be 1, 2 or 3')
    for name, host in list(nr.inventory.hosts.items()):
        if host.data.get('_explicit_credentials'):
            continue
        if client is None or not host.data.get('netbox_id'):
            raise CredentialError('Credential profiles require NetBox device inventory or a queued job for shared selection and cooldowns')
        wrapped = ProfileHost(name=host.name, hostname=host.hostname, port=host.port,
                              platform=host.platform, username=host.username, password=host.password,
                              groups=host.groups, data=host.data, defaults=host.defaults,
                              connection_options=host.connection_options)
        wrapped.selector = Selector(wrapped, credentials.profiles, client, max_attempts=attempts)
        wrapped.selector.use(credentials.profiles[0])
        nr.inventory.hosts[name] = wrapped


def authenticate(host, callback, transport='ssh'):
    selector = getattr(host, 'selector', None)
    return selector.run(callback, transport) if selector else callback()


def profile_health():
    """Only names/readiness are published, including while the queue is idle."""
    if not os.environ.get('NET_CREDENTIAL_PROFILES'):
        return {'profiles': [], 'status': 'legacy', 'message': 'Using single-account settings'}
    try:
        profiles = load_profiles()
    except CredentialError as exc:
        return {'profiles': [], 'status': 'incomplete', 'message': str(exc)}
    return {'profiles': [p.name for p in profiles], 'status': 'ready', 'message': 'All configured profiles are present'}


def validate_access(task, args):
    """Authenticate and check privileged CLI access without changing configuration."""
    from nornir.core.task import Result
    if task.host.platform == 'aruba_clearpass':
        from .clearpass_ntp import Client
        with Client(task.host):
            pass
    elif task.host.platform == 'aruba_os':
        from .aruba_ntp import Client
        with Client(task.host, task.host.hostname, {'port': 4343, 'timeout': 30}):
            pass
    elif task.host.platform == 'f5_tmsh':
        from .f5_waf import Client
        from .features.waf import connection_settings
        with Client(task.host, **connection_settings(args)) as client:
            client.request('GET', '/mgmt/tm/sys/version')
    else:
        if not task.host.platform:
            from .runner import detect_platform
            detect_platform(task)
        connection = task.host.get_connection('netmiko', task.nornir.config)
        # Some appliance shells have no privileged-mode distinction.
        if task.host.platform not in ('aruba_clearpass', 'generic'):
            connection.enable()
    return Result(host=task.host, result={'credential_profile': task.host.data.get('credential_profile'),
                                         'status': 'access_verified'})


def run_validation(args, style):
    from .cli import _connect
    from .errors import summarize
    from . import archive
    targets, credentials, code = _connect(args, style)
    if targets is None:
        return code
    try:
        results = targets.run(task=validate_access, args=args)
        for name, result in results.items():
            if result.failed:
                print(f'{name}: {summarize(result.exception)}')
            else:
                print(f'{name}: access verified')
        archive.capture({name: {'status': 'failed' if result.failed else 'access_verified',
                                'credential_profile': targets.inventory.hosts[name].data.get('credential_profile'),
                                **({'error': summarize(result.exception)} if result.failed else {})}
                         for name, result in results.items()})
        return int(results.failed)
    finally:
        targets.close_connections()
