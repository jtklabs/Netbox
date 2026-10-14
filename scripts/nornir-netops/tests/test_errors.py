import pytest

from netops.errors import MAX_LENGTH, first_line, summarize

# What netmiko actually raises. One useful sentence, then eight lines of advice
# that are identical on every device that timed out.
NETMIKO_TIMEOUT = """\
Connection to device timed-out: cisco_ios 10.1.10.11:22

TCP connection to device failed.

Common causes of this problem are:
1. Incorrect hostname or IP address.
2. Wrong TCP port.
3. Intermediate firewall blocking access.

Device settings: cisco_ios 10.1.10.11:22
"""


class NetmikoTimeoutException(Exception):
    pass


class NetmikoAuthenticationException(Exception):
    pass


class ConfigInvalidException(Exception):
    pass


def test_first_line_skips_blanks():
    assert first_line("\n\n  the useful bit  \nthe advice\n") == "the useful bit"


def test_first_line_of_nothing():
    assert first_line("") == ""


def test_timeout_becomes_one_actionable_line():
    summary = summarize(NetmikoTimeoutException(NETMIKO_TIMEOUT))
    assert summary == "timed out connecting -- unreachable, filtered, or wrong port"
    assert "\n" not in summary
    assert "Common causes" not in summary


def test_authentication_failure():
    assert "authentication failed" in summarize(NetmikoAuthenticationException("nope"))


@pytest.mark.parametrize('chained', [False, True])
@pytest.mark.parametrize('detail,expected', [
    ('Authentication timeout.', 'SSH authentication timed out'),
    ('Authentication failed: transport shut down or saw EOF', 'SSH connection lost during authentication'),
])
def test_netmiko_auth_wrapper_does_not_blame_credentials_for_transport_failures(chained, detail, expected):
    from netmiko.exceptions import NetmikoAuthenticationException as AuthError
    from paramiko.ssh_exception import AuthenticationException

    cause = AuthenticationException(detail)
    error = AuthError('Authentication to device failed.' if chained else
                      f'Authentication to device failed.\nDevice settings: private-host\n{detail}')
    if chained:
        error.__context__ = cause
    for failure in (error, cause):
        summary = summarize(failure)
        assert summary.startswith(expected)
        assert 'credentials were not confirmed rejected' in summary
        assert 'private-host' not in summary
        assert len(summary) <= MAX_LENGTH


def test_explicit_ssh_rejection_remains_an_authentication_failure():
    from netmiko.exceptions import NetmikoAuthenticationException as AuthError
    from paramiko.ssh_exception import AuthenticationException

    error = AuthError('Authentication to device failed.')
    error.__cause__ = AuthenticationException('Authentication failed.')
    assert 'authentication failed' in summarize(error)
    assert 'timed out' not in summarize(error)


def test_host_name_containing_timeout_does_not_change_auth_failure_classification():
    error = NetmikoAuthenticationException('Authentication to device failed.\nDevice settings: timeout-host')
    assert 'authentication failed' in summarize(error)


@pytest.mark.parametrize('cause,expected', [
    (TimeoutError(), 'SSH authentication timed out'),
    (ConnectionResetError(), 'SSH connection lost during authentication'),
    (EOFError(), 'SSH connection lost during authentication'),
])
def test_auth_wrapper_uses_transport_cause_without_a_message(cause, expected):
    error = NetmikoAuthenticationException('Authentication to device failed.')
    error.__cause__ = cause
    assert summarize(error).startswith(expected)


def test_config_rejected():
    assert summarize(ConfigInvalidException("% Invalid input")) == (
        "the device rejected the configuration"
    )


def test_builtin_connection_errors():
    assert "connection refused" in summarize(ConnectionRefusedError(61, "Connection refused"))
    assert summarize(TimeoutError()) == "timed out"


def test_unknown_exception_keeps_its_class_name():
    """Something we have no wording for is still one line, and still greppable."""
    summary = summarize(RuntimeError("something odd\nwith detail below"))
    assert summary == "RuntimeError: something odd"


def test_our_own_errors_read_as_plain_sentences():
    from netops.core import UnsupportedPlatform

    assert summarize(UnsupportedPlatform("platform 'x' has no 'ntp' support")) == (
        "platform 'x' has no 'ntp' support"
    )
    assert summarize(ValueError("could not autodetect the platform")) == (
        "could not autodetect the platform"
    )


def test_a_very_long_message_is_truncated():
    summary = summarize(RuntimeError("x" * 500))
    assert len(summary) <= MAX_LENGTH
    assert summary.endswith("…")


def test_an_exception_with_no_message_still_says_something():
    assert summarize(RuntimeError()) == "RuntimeError"


@pytest.mark.parametrize(
    "exc", [NetmikoTimeoutException(NETMIKO_TIMEOUT), RuntimeError("a\nb"), TimeoutError()]
)
def test_every_summary_is_a_single_short_line(exc):
    summary = summarize(exc)
    assert "\n" not in summary and 0 < len(summary) <= MAX_LENGTH


@pytest.mark.parametrize('status,expected', [(401, 'authentication failed'), (403, 'access denied')])
def test_http_credentials_and_permissions_are_distinguished(status, expected):
    from netops.errors import DeviceHTTPError
    from requests import Response
    from requests.exceptions import HTTPError
    response = Response()
    response.status_code = status
    for error in (DeviceHTTPError('F5', 'POST', '/login', status), HTTPError('sensitive body', response=response)):
        assert expected in summarize(error)
        assert str(status) in summarize(error)
        assert 'sensitive body' not in summarize(error)


def test_https_connection_timeout_and_tls_errors_are_actionable():
    from requests.exceptions import ConnectTimeout, ReadTimeout, SSLError
    assert 'connection timed out' in summarize(ConnectTimeout('raw transport detail'))
    assert 'HTTP response timed out' in summarize(ReadTimeout('raw transport detail'))
    assert 'certificate' in summarize(SSLError('raw TLS detail'))
