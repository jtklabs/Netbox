"""Real SSH negotiations over local sockets using Netmiko's connection settings.

Do not mock Transport: a new dependency can remove an algorithm even when the
worker's inventory and connection-parameter tests still pass.
"""
import socket
import threading

import paramiko
import pytest
from netmiko.base_connection import BaseConnection


class PasswordServer(paramiko.ServerInterface):
    def check_auth_password(self, username, password):
        if (username, password) == ('test-user', 'test-password'):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'password'


@pytest.fixture(scope='module')
def host_key():
    return paramiko.RSAKey.generate(2048)


@pytest.mark.parametrize('keys,kex,cipher,mac,expected_key', [
    # ssh-rsa host signatures with otherwise modern algorithms.
    (('ssh-rsa',), 'diffie-hellman-group14-sha256', 'aes128-ctr', 'hmac-sha2-256', 'ssh-rsa'),
    # SHA-1 key exchanges, CBC/3DES encryption, and SHA-1 integrity in older firmware.
    (('ssh-rsa',), 'diffie-hellman-group14-sha1', 'aes128-cbc', 'hmac-sha1', 'ssh-rsa'),
    (('ssh-rsa',), 'diffie-hellman-group1-sha1', '3des-cbc', 'hmac-sha1', 'ssh-rsa'),
    (('rsa-sha2-512',), 'diffie-hellman-group14-sha256', 'aes128-ctr', 'hmac-sha2-256', 'rsa-sha2-512'),
    # Modern RSA signatures still win when both old and new signatures are offered.
    (('ssh-rsa', 'rsa-sha2-256', 'rsa-sha2-512'), 'diffie-hellman-group14-sha256',
     'aes128-ctr', 'hmac-sha2-256', 'rsa-sha2-512'),
])
def test_netmiko_negotiates_legacy_peers_and_prefers_modern_signatures(host_key, keys, kex, cipher, mac, expected_key):
    local, remote = socket.socketpair()
    server = paramiko.Transport(remote)
    client = None
    try:
        security = server.get_security_options()
        security.key_types = keys
        security.kex = (kex,)
        security.ciphers = (cipher,)
        security.digests = (mac,)
        server.add_server_key(host_key)
        ready = threading.Event()
        server.start_server(event=ready, server=PasswordServer())
        connection = BaseConnection(
            host='test-device.invalid', username='test-user', password='test-password',
            sock=local, auto_connect=False, conn_timeout=5, banner_timeout=5, auth_timeout=5,
        )
        client = connection._build_ssh_client()
        client.connect(**connection._connect_params_dict())
        transport = client.get_transport()
        assert transport.is_authenticated()
        assert transport.host_key_type == expected_key
        assert transport.local_cipher == transport.remote_cipher == cipher
        assert transport.local_mac == transport.remote_mac == mac
        assert ready.wait(5)
        assert server.get_exception() is None
    finally:
        if client is not None:
            client.close()
        server.close()
        local.close()
        remote.close()
        if server.ident is not None:
            server.join(timeout=5)
