import logging

from netops.debuglog import SecretFilter


def test_device_credentials_are_redacted_before_parsing():
    redactor = SecretFilter()
    record = logging.LogRecord('netmiko', logging.DEBUG, '', 0,
        'snmp-server community private RO\nusername admin secret 9 stored-hash', (), None)
    assert redactor.filter(record)
    assert 'private' not in record.getMessage()
    assert 'stored-hash' not in record.getMessage()


def test_longest_secret_is_removed_first():
    redactor = SecretFilter()
    redactor.add(['prefix', 'prefix-and-suffix'])
    assert redactor.clean('prefix-and-suffix') == '<redacted>'
