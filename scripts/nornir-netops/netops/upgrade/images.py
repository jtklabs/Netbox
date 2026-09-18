"""Images on the worker: a cache of downloads from an image_source URL.

BIG-IP uploads always go through here; IOS XE and EOS use it only when the
switch could not fetch the image itself and the worker pushes it over SCP.
"""

import hashlib
import os
import re
from pathlib import Path


def local_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, destination):
    """Stream an image from the distribution server into the worker cache."""
    import requests
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_name(destination.name + ".part")
    try:
        with requests.get(url, stream=True, timeout=120, allow_redirects=False) as response:
            if response.status_code != 200:
                raise RuntimeError(f"image download returned HTTP {response.status_code}")
            with open(temporary, "wb") as handle:
                for block in response.iter_content(chunk_size=4 * 1024 * 1024):
                    handle.write(block)
        os.replace(temporary, destination)
    except requests.RequestException as exc:
        raise RuntimeError(f"image download failed ({type(exc).__name__})") from None
    finally:
        if temporary.exists():
            temporary.unlink()


def cache_dir(options):
    from ..cli import PROJECT_ROOT
    return Path(getattr(options, "image_cache", None) or PROJECT_ROOT / ".images")


def local_image(profile, options, emit):
    """The image on this worker: a local path, or a cached download of the profile URL."""
    source = profile.image_source
    if not source:
        return None
    if re.match(r"^https?://", source):
        path = cache_dir(options) / profile.image
        if path.is_file() and local_md5(path) == profile.md5.lower():
            return path
        emit("staging", f"Downloading {profile.image} to the worker image cache")
        download(source, path)
        if local_md5(path) != profile.md5.lower():
            path.unlink()
            raise ValueError("downloaded image checksum does not match the approved profile")
        return path
    path = Path(source)
    if not path.is_file():
        raise ValueError("image_source file is not present on this worker")
    if local_md5(path) != profile.md5.lower():
        raise ValueError("local image checksum does not match the approved profile")
    return path
