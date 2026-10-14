"""Turn an exception into one line a human can act on.

netmiko is helpful to a fault: a single connection timeout is a ten-line
message with a bulleted list of common causes. Across a fleet that is hundreds
of lines of identical advice, and the one useful fact -- which devices failed
and why -- is buried in it. The full exception and traceback go to the debug
log; the terminal gets the summary from here.
"""

from __future__ import annotations

#: Longest summary we will print before truncating.
MAX_LENGTH = 160

#: Keyed on the exception class name rather than the class, so this module
#: stays importable without netmiko/paramiko installed.
KNOWN = {
    "NetmikoTimeoutException": "timed out connecting -- unreachable, filtered, or wrong port",
    "ConfigInvalidException": "the device rejected the configuration",
    "ReadTimeout": "the device stopped responding partway through a command",
    "ReadException": "lost the session while reading from the device",
    "WriteException": "lost the session while sending to the device",
    "SSHException": "SSH negotiation failed",
    "NoValidConnectionsError": "nothing accepted an SSH connection on that address and port",
    "ConnectionRefusedError": "connection refused -- SSH is not listening on that port",
    "ConnectionResetError": "the device reset the connection",
    "TimeoutError": "timed out",
    "socket.timeout": "timed out",
    "gaierror": "name lookup failed -- check the address in the CSV",
    "PermissionError": "permission denied",
    "ConnectTimeout": "connection timed out -- check reachability, management port and firewall rules",
    "Timeout": "request timed out -- check device reachability and management service",
    "SSLError": "TLS connection failed -- check the device certificate and TLS settings",
    "ConnectionError": "connection failed -- check the management address, port and service",
}


def authentication_summary(exc: BaseException) -> str:
    """Netmiko wraps Paramiko timeouts and lost transports as auth failures too.

    Inspect the wrapped detail and exception chain, but only return fixed text:
    raw connection errors can contain usernames and other sensitive details.
    """
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    detail = '\n'.join(str(error).lower() for error in chain)
    if (any(type(error).__name__ in ('TimeoutError', 'timeout') for error in chain)
            or any(marker in detail for marker in (
                'authentication timeout', 'authentication timed out', 'authentication timed-out'))):
        return 'SSH authentication timed out -- no login response before the deadline; credentials were not confirmed rejected'
    if (any(isinstance(error, (EOFError, ConnectionError)) for error in chain)
            or any(marker in detail for marker in (
                'transport shut down', 'saw eof', 'connection reset', 'connection closed', 'connection refused'))):
        return 'SSH connection lost during authentication -- credentials were not confirmed rejected; check the network and SSH service'
    return 'SSH authentication failed -- check login credentials and the device authentication policy'


class DeviceHTTPError(RuntimeError):
    """An HTTP failure with status metadata, without credentials or response bodies."""
    def __init__(self, service, method, path, status_code):
        self.status_code = status_code
        super().__init__(f'{service} {method} {path} failed (HTTP {status_code})')


def first_line(text: str) -> str:
    """The first line with anything on it. netmiko puts the useful sentence
    first and the advice below a blank line, which is exactly what we want."""
    for line in str(text).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def summarize(exc: BaseException) -> str:
    """One line: what went wrong, and enough of the detail to act on it."""
    name = type(exc).__name__
    status = getattr(exc, 'status_code', None)
    response = getattr(exc, 'response', None)
    if response is not None:
        status = getattr(response, 'status_code', status)
    if status == 401:
        return 'authentication failed (HTTP 401) -- check the configured credentials or token'
    if status == 403:
        return 'access denied (HTTP 403) -- check the account role and API permissions'
    if name in ('NetmikoAuthenticationException', 'NetmikoAuthError', 'AuthenticationException'):
        return authentication_summary(exc)
    # Requests and Netmiko both use the name ReadTimeout for different stages.
    if name == 'ReadTimeout' and type(exc).__module__.startswith('requests.'):
        return 'HTTP response timed out -- the management service did not respond before the deadline'
    friendly = KNOWN.get(name)
    if friendly:
        return friendly

    detail = first_line(str(exc))
    if not detail:
        detail = name
    elif name not in (
        "ValueError",
        "UnsupportedPlatform",
        "NotApplicable",
        "CredentialError",
        "AmbiguousSource",
    ):
        # An exception we have no wording for: keep the class name, since it is
        # the thing worth grepping the debug log for.
        detail = f"{name}: {detail}"

    if len(detail) > MAX_LENGTH:
        detail = detail[: MAX_LENGTH - 1].rstrip() + "…"
    return detail
