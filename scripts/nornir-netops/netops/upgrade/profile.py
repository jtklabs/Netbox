"""An explicit, locally validated upgrade path; no implicit version selection."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def version(value):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)([a-z]?)", str(value))
    if not match:
        raise ValueError(f"invalid IOS XE release: {value!r}")
    return tuple(int(x) for x in match.groups()[:3]) + (match[4],)


@dataclass(frozen=True)
class Profile:
    name: str
    models: tuple
    starting_versions: tuple
    target_version: str
    image: str
    md5: str
    minimum_free_bytes: int
    bundle_conversion_validated: bool = False
    image_source: str = ""

    @classmethod
    def load(cls, path):
        return cls.from_mapping(yaml.safe_load(Path(path).read_text()))

    @classmethod
    def from_mapping(cls, data):
        if not isinstance(data, dict):
            raise ValueError("upgrade profile must be a mapping")
        data = dict(data)
        allowed = set(cls.__dataclass_fields__)
        if set(data) - allowed:
            raise ValueError(f"unknown upgrade profile fields: {sorted(set(data) - allowed)}")
        for key in ("models", "starting_versions"):
            if not isinstance(data.get(key), list) or not data[key]:
                raise ValueError(f"profile {key} must be a nonempty list")
            data[key] = tuple(data[key])
        try:
            result = cls(**data)
        except TypeError as exc:
            raise ValueError(f"incomplete upgrade profile: {exc}") from None
        if not isinstance(result.name, str) or not result.name.strip():
            raise ValueError("profile name is required")
        # Both families use IOS XE, but their image packages are different.
        # A NetBox cisco_ios platform alone cannot authorize either image.
        if not all(isinstance(m, str) and re.fullmatch(r"C93(?:00|50)-[A-Z0-9-]+|C9300[LX]-[A-Z0-9-]+", m) for m in result.models):
            raise ValueError("models must be exact C9300-family or C9350 PIDs")
        families = {"C9350" if m.startswith("C9350-") else "C9300" for m in result.models}
        if len(families) != 1:
            raise ValueError("C9300 and C9350 require separate profiles and image packages")
        family = next(iter(families))
        minimum = (17, 18, 1, "") if family == "C9350" else (16, 6, 2, "")
        # These two PIDs were introduced after the original C9350 models.
        if set(result.models) & {"C9350-24HX", "C9350-48HXN"}:
            minimum = (26, 1, 1, "a")
        target = version(result.target_version)
        for start in result.starting_versions:
            if version(start) < minimum or version(start) >= target:
                floor = ".".join(str(x) for x in minimum[:3]) + minimum[3]
                raise ValueError(f"{family} starting versions must be >={floor} and older than the target")
        prefix = r"cisco9k_iosxe(?:_npe)?" if family == "C9350" else "cat9k_iosxe"
        if not isinstance(result.image, str) or not re.fullmatch(prefix + r"\.[A-Za-z0-9_.-]+\.bin", result.image):
            raise ValueError(f"{family} image must use the {'cisco9k_iosxe (or cisco9k_iosxe_npe)' if family == 'C9350' else 'cat9k_iosxe'} .bin package")
        image_release = re.match(prefix + r"\.(\d+\.\d+\.\d+[a-z]?)\.", result.image)
        if not image_release or version(image_release[1]) != target:
            raise ValueError("image filename release must match target_version")
        if not isinstance(result.md5, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", result.md5):
            raise ValueError("md5 must be the Cisco-published 32-digit image checksum")
        if type(result.minimum_free_bytes) is not int or result.minimum_free_bytes < 1_500_000_000:
            raise ValueError("minimum_free_bytes must reserve at least 1500000000 bytes for expansion")
        if type(result.bundle_conversion_validated) is not bool:
            raise ValueError("bundle_conversion_validated must be a boolean")
        if result.image_source:
            parsed = urlsplit(result.image_source)
            if (parsed.scheme not in {"https", "http", "tftp"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or not re.fullmatch(r"[A-Za-z0-9:/_.%~-]+", result.image_source)):
                raise ValueError("image_source must be an HTTP(S)/TFTP URL without credentials or query")
            if Path(parsed.path).name != result.image:
                raise ValueError("image_source basename must match image")
        return result
