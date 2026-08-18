"""A fake Cisco IOS SSH target that replays a recorded running-config.

This is how the checker is tested without a switch. Paramiko does the SSH work
— key exchange, password authentication, the channel — and a small CLI answers
the handful of commands the tool actually sends. From netmiko's point of view
there is no difference between this and a real device: it negotiates a real SSH
session, disables paging, reads a prompt, and gets `show running-config` back
over the wire.

What it proves and what it does not:

    proves      that the SSH path works end to end — prompt detection, paging,
                enable, config mode, `write memory` — and that everything above
                it (parsing, redaction, evaluation, remediation planning, the
                lockout guards, the payload posted to NetBox) behaves against
                text a device really sent rather than a string in a test.
    proves not  that any particular IOS release accepts any particular command.
                Only a real switch can tell you that. The fixtures encode what
                a C9300 running 17.9 is documented to produce.

Config commands are applied to the in-memory configuration, so remediation is
observable: send `no ip http server` and the next `show running-config` shows
it gone. Everything received is recorded, which is what lets a test assert that
a guard prevented a command rather than merely that a plan said it would.

Paramiko is netmiko's own dependency, so the emulator adds nothing to install
beyond what the tool already needs.
"""

from __future__ import annotations

import socket
import threading

import paramiko

DEFAULT_USERNAME = 'netops'
DEFAULT_PASSWORD = 'labpassword123'
DEFAULT_ENABLE = 'labenable123'


class _Server(paramiko.ServerInterface):
    def __init__(self, username, password):
        self.username = username
        self.password = password
        self.event = threading.Event()

    def check_auth_password(self, username, password):
        if username == self.username and password == self.password:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'password'

    def check_channel_request(self, kind, chanid):
        if kind == 'session':
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_shell_request(self, channel):
        self.event.set()
        return True

    def check_channel_pty_request(self, *args, **kwargs):
        return True


class IosCli:
    """The command loop: just enough IOS to be indistinguishable to netmiko."""

    def __init__(self, config, hostname='lab-sw', enable_secret=DEFAULT_ENABLE,
                 enabled=True):
        self.lines = [line.rstrip() for line in config.splitlines()]
        self.hostname = hostname
        self.enable_secret = enable_secret
        # Privileged exec on connect is the realistic default: the accounts
        # these tools use are privilege 15, and IOS drops them straight into
        # `#`. Pass enabled=False to emulate a box that makes you type `enable`.
        self.enabled = enabled
        self.config_mode = False
        self.block = ''        # the config block we are inside, '' at top level
        self.received = []     # every command line, in order
        self.saved = False

    # ------------------------------------------------------------------ #
    @property
    def prompt(self):
        if self.config_mode:
            return '%s(config%s)#' % (self.hostname, '-line' if self.block else '')
        return '%s#' % self.hostname if self.enabled else '%s>' % self.hostname

    def config_text(self):
        return '\n'.join(self.lines)

    # ------------------------------------------------------------------ #
    def handle(self, command):
        """Run one command line, returning the output to send back.

        Unknown commands get IOS's own error rather than silence — a test that
        sends a typo should see the switch complain, exactly as it would in
        real life.
        """
        self.received.append(command)
        text = command.strip()
        if not text:
            return ''

        if self.config_mode:
            return self._config_command(text)

        if text.startswith('terminal '):
            return ''
        if text in ('enable', 'en'):
            self.enabled = True
            return ''
        if text.startswith('show running-config') or text.startswith('show run'):
            return self.config_text()
        if text.startswith('show version'):
            return ('Cisco IOS XE Software, Version 17.09.04a\n'
                    'cisco C9300-48P (X86) processor')
        if text in ('configure terminal', 'conf t', 'config t'):
            if not self.enabled:
                return '%% Invalid input detected at \'^\' marker.'
            self.config_mode = True
            return 'Enter configuration commands, one per line.  End with CNTL/Z.'
        # netmiko's save_config() sends `write mem`, not `write memory` — an
        # exact-match list here silently fails the save and the test passes
        # anyway, which is precisely the bug an emulator is supposed to catch.
        if text.startswith(('write', 'wr')) or text.startswith('copy running-config'):
            self.saved = True
            return 'Building configuration...\n[OK]'
        if text in ('exit', 'quit', 'logout'):
            return None  # close the session
        return '%% Invalid input detected at \'^\' marker.'

    # ------------------------------------------------------------------ #
    def _config_command(self, text):
        if text in ('end', '^Z'):
            self.config_mode = False
            self.block = ''
            return ''
        if text == 'exit':
            if self.block:
                self.block = ''
            else:
                self.config_mode = False
            return ''

        if text.startswith('no '):
            self._remove(text[3:].strip())
        else:
            self._add(text)
        return ''

    def _add(self, text):
        """Apply a configuration line, entering a block when one is named.

        Block detection is the same rule the real thing uses from the outside:
        a line that opens a context (`line vty 0 4`, `interface ...`) becomes
        the parent for the indented lines that follow.
        """
        if self._opens_block(text):
            self.block = text
            if text not in self.lines:
                self._insert(text, indent=0)
            return
        indent = 1 if self.block else 0
        rendered = '%s%s' % (' ' * indent, text)
        if rendered in self.lines:
            return
        self._insert(text, indent=indent)

    def _remove(self, text):
        indent = 1 if self.block else 0
        rendered = '%s%s' % (' ' * indent, text)
        if rendered in self.lines:
            self.lines.remove(rendered)
            return
        # `no username olduser` removes the whole `username olduser ...` line,
        # which is how IOS behaves and what the local-users standard relies on.
        prefix = '%s%s ' % (' ' * indent, text)
        for line in list(self.lines):
            if line == rendered or line.startswith(prefix):
                self.lines.remove(line)
                return
        # Negating something already absent is not an error on IOS.

    def _insert(self, text, indent):
        rendered = '%s%s' % (' ' * indent, text)
        if indent and self.block in self.lines:
            at = self.lines.index(self.block) + 1
            while at < len(self.lines) and self.lines[at].startswith(' '):
                at += 1
            self.lines.insert(at, rendered)
            return
        # Top level: before the trailing `end` if there is one.
        if 'end' in self.lines:
            self.lines.insert(self.lines.index('end'), rendered)
        else:
            self.lines.append(rendered)

    @staticmethod
    def _opens_block(text):
        openers = ('interface ', 'line ', 'router ', 'key chain ', 'vlan ',
                   'class-map ', 'policy-map ', 'ip access-list ')
        return any(text.startswith(opener) for opener in openers)


class FakeIosDevice:
    """One emulated switch listening on a loopback port.

    Each instance gets its own ephemeral port, so a whole fleet can run at once
    and the checker can be pointed at 127.0.0.1 with different ports.
    """

    def __init__(self, config, hostname='lab-sw', username=DEFAULT_USERNAME,
                 password=DEFAULT_PASSWORD, enable_secret=DEFAULT_ENABLE,
                 enabled=True):
        self.cli = IosCli(config, hostname=hostname, enable_secret=enable_secret,
                          enabled=enabled)
        self.username = username
        self.password = password
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind(('127.0.0.1', 0))
        self._socket.listen(8)
        self.port = self._socket.getsockname()[1]
        # Generated per instance rather than committed: a private key in the
        # repository is a private key in the repository, whatever it is for.
        self._host_key = paramiko.RSAKey.generate(2048)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    # ------------------------------------------------------------------ #
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        try:
            self._socket.close()
        except OSError:
            pass

    # ------------------------------------------------------------------ #
    def _serve(self):
        while not self._stop.is_set():
            try:
                client, _address = self._socket.accept()
            except OSError:
                return
            threading.Thread(target=self._session, args=(client,), daemon=True).start()

    def _session(self, client):
        transport = paramiko.Transport(client)
        transport.add_server_key(self._host_key)
        server = _Server(self.username, self.password)
        try:
            transport.start_server(server=server)
            channel = transport.accept(20)
            if channel is None:
                return
            server.event.wait(10)
            self._shell(channel)
        except Exception:  # noqa: BLE001 - a dead test connection is not a failure
            pass
        finally:
            try:
                transport.close()
            except Exception:  # noqa: BLE001
                pass

    def _shell(self, channel):
        """Line-oriented shell.

        netmiko sends a command and reads until it sees the prompt again, so the
        prompt has to come back after every command and never before one — the
        single thing an emulator like this most often gets wrong, and the reason
        the echo is written back explicitly.
        """
        channel.send(('\r\n%s' % self.cli.prompt).encode())
        buffer = ''
        while not self._stop.is_set():
            try:
                data = channel.recv(4096)
            except Exception:  # noqa: BLE001
                return
            if not data:
                return
            buffer += data.decode(errors='replace')
            while '\n' in buffer or '\r' in buffer:
                line, _sep, rest = buffer.replace('\r\n', '\n').replace('\r', '\n').partition('\n')
                buffer = rest
                channel.send((line + '\r\n').encode())   # echo, as a real device does
                output = self.cli.handle(line)
                if output is None:
                    # Close the way a device does — status first, then the
                    # channel — so the client is not left writing into a socket
                    # that has already gone, which shows up as a spurious
                    # "Connection reset by peer" in otherwise clean output.
                    channel.send_exit_status(0)
                    channel.shutdown_write()
                    channel.close()
                    return
                if output:
                    channel.send((output + '\r\n').encode())
                channel.send(self.cli.prompt.encode())
