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
