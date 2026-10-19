from urllib.parse import urlsplit


def secure_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("an HTTPS URL without embedded credentials, query, or fragment is required")
    return value.rstrip("/")
