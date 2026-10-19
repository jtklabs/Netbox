"""Server-side portal integration. Keep its reader token out of browsers."""
import json
import os
import ssl
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DiscoveryClient:
    def __init__(self, url, token_file, ca_file=None):
        if not url.startswith("https://"):
            raise ValueError("HTTPS is required")
        self.base = url.rstrip("/")
        self.token = Path(token_file).read_text().strip()
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def get(self, path, **filters):
        request = Request(self.base + "/v1/" + path + "?" + urlencode(filters),
                          headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"})
        with self.opener.open(request, timeout=30) as response:
            return json.load(response)

    def history(self, **filters):
        cursor = 0
        while True:
            page = self.get("observations", current="false", after_id=cursor, **filters)
            yield from page["items"]
            cursor = page["next_after_id"]
            if cursor is None:
                return


if __name__ == "__main__":
    client = DiscoveryClient(os.environ["DISCOVERY_URL"], os.environ["DISCOVERY_TOKEN_FILE"],
                             os.getenv("DISCOVERY_CA_FILE"))
    print(json.dumps(client.get("locate", ip="192.0.2.25", scope="example/branch-1"), indent=2))
