"""An explicit, locally validated upgrade path; no implicit version selection.

Three families share one profile shape: Catalyst 9000 IOS XE images, Arista
EOS `.swi` images and BIG-IP `.iso` images. The image filename decides the
family, and each family applies its own rules for models, releases and image
sources.
"""

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def version(value):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)([a-z]?)", str(value))
    if not match:
        raise ValueError(f"invalid IOS XE release: {value!r}")
    return tuple(int(x) for x in match.groups()[:3]) + (match[4],)


def f5_version(value):
    if not re.fullmatch(r"\d+(?:\.\d+){2,3}", str(value)):
        raise ValueError(f"invalid BIG-IP release: {value!r}")
    parts = [int(x) for x in str(value).split(".")]
    return tuple(parts + [0] * (4 - len(parts)))


def eos_version(value):
    """EOS releases: 4.32.1F, 4.30.5M, 4.28.10.1M. Numbers first, then the train letter."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?([A-Z]{0,3})", str(value))
    if not match:
        raise ValueError(f"invalid EOS release: {value!r}")
    return tuple(int(x or 0) for x in match.groups()[:4]) + (match[5],)


F5_IMAGE = re.compile(r"^(?:Hotfix-)?BIGIP-(\d+(?:\.\d+){2,3})-(\d+\.\d+\.\d+)(?:[.-][A-Za-z0-9.-]+)?\.iso$")
F5_MODEL = re.compile(r"^(?:BIG-IP .+|[A-Z][A-Z0-9]{2,5})$")
# EOS-4.32.1F.swi, EOS64-4.32.1F.swi and the 2GB/INT variants Arista publishes.
EOS_IMAGE = re.compile(r"^EOS(?:64)?(?:-2GB)?(?:-INT)?-(\d+\.\d+\.\d+(?:\.\d+)?[A-Z]{0,3})\.swi$")
EOS_MODEL = re.compile(r"^(?:(?:DCS|CCS)-[A-Z0-9][A-Z0-9-]*|vEOS(?:-lab)?)$")
# Reserve floors: IOS XE expands packages on flash and BIG-IP unpacks the ISO;
# an EOS .swi is mounted in place, so only headroom for logs and cores is needed.
FREE_SPACE_FLOOR = {"eos": 100_000_000}
DEFAULT_FREE_SPACE_FLOOR = 1_500_000_000


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
    # BIG-IP only.
    volume: str = ""
    allow_active: bool = False
    ucs_backup: bool = True
    license_check_date: str = ""

    @property
    def family(self):
        image = str(self.image).lower()
        if image.endswith(".iso"):
            return "f5"
        if image.endswith(".swi"):
            return "eos"
        return "C9350" if self.models and str(self.models[0]).startswith("C9350-") else "C9300"

    def release(self, value):
        if self.family == "f5":
            return f5_version(value)
        if self.family == "eos":
            return eos_version(value)
        return version(value)

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
        if not isinstance(result.image, str) or not result.image:
            raise ValueError("image must be the image filename")
        if not isinstance(result.md5, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", result.md5):
            raise ValueError("md5 must be the vendor-published 32-digit image checksum")
        floor = FREE_SPACE_FLOOR.get(result.family, DEFAULT_FREE_SPACE_FLOOR)
        if type(result.minimum_free_bytes) is not int or result.minimum_free_bytes < floor:
            raise ValueError(f"minimum_free_bytes must reserve at least {floor} bytes for expansion")
        for key in ("bundle_conversion_validated", "allow_active", "ucs_backup"):
            if type(getattr(result, key)) is not bool:
                raise ValueError(f"{key} must be a boolean")
        for key in ("image_source", "volume", "license_check_date"):
            if not isinstance(getattr(result, key), str):
                raise ValueError(f"{key} must be a string")
        if result.family == "f5":
            return cls._validate_f5(result)
        if result.family == "eos":
            return cls._validate_eos(result)
        return cls._validate_ios_xe(result)

    @staticmethod
    def _validate_url_source(result, schemes, message):
        parsed = urlsplit(result.image_source)
        if (parsed.scheme not in schemes or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not re.fullmatch(r"[A-Za-z0-9:/_.%~-]+", result.image_source)):
            raise ValueError(message)
        if Path(parsed.path).name != result.image:
            raise ValueError("image_source basename must match image")

    @classmethod
    def _validate_eos(cls, result):
        if result.volume or result.allow_active or result.license_check_date:
            raise ValueError("volume, allow_active and license_check_date apply to BIG-IP profiles only")
        if result.bundle_conversion_validated:
            raise ValueError("bundle_conversion_validated applies to IOS XE profiles only")
        if not all(isinstance(m, str) and EOS_MODEL.match(m) for m in result.models):
            raise ValueError("EOS models must be exact Arista model names such as DCS-7050SX3-48YC8 or CCS-720XP-48Y6")
        match = EOS_IMAGE.match(result.image)
        if not match:
            raise ValueError("EOS image must be an EOS-<release>.swi or EOS64-<release>.swi filename")
        target = eos_version(result.target_version)
        if eos_version(match[1]) != target:
            raise ValueError("image filename release must match target_version")
        for start in result.starting_versions:
            if eos_version(start) >= target:
                raise ValueError("EOS starting versions must be older than the target")
        if result.image_source:
            cls._validate_url_source(result, {"https", "http", "tftp"},
                                     "image_source must be an HTTP(S)/TFTP URL without credentials or query")
        return result

    @classmethod
    def _validate_ios_xe(cls, result):
        if result.volume or result.allow_active or result.license_check_date:
            raise ValueError("volume, allow_active and license_check_date apply to BIG-IP profiles only")
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
        if not re.fullmatch(prefix + r"\.[A-Za-z0-9_.-]+\.bin", result.image):
            raise ValueError(f"{family} image must use the {'cisco9k_iosxe (or cisco9k_iosxe_npe)' if family == 'C9350' else 'cat9k_iosxe'} .bin package")
        image_release = re.match(prefix + r"\.(\d+\.\d+\.\d+[a-z]?)\.", result.image)
        if not image_release or version(image_release[1]) != target:
            raise ValueError("image filename release must match target_version")
        if result.image_source:
            cls._validate_url_source(result, {"https", "http", "tftp"},
                                     "image_source must be an HTTP(S)/TFTP URL without credentials or query")
        return result

    @classmethod
    def _validate_f5(cls, result):
        if not all(isinstance(m, str) and F5_MODEL.match(m) for m in result.models):
            raise ValueError("BIG-IP models must be marketing names such as 'BIG-IP i5800' or platform IDs such as C119")
        match = F5_IMAGE.match(result.image)
        if not match:
            raise ValueError("BIG-IP image must be a BIGIP-<version>-<build>.iso filename")
        target = f5_version(result.target_version)
        if f5_version(match[1]) != target:
            raise ValueError("image filename release must match target_version")
        for start in result.starting_versions:
            if f5_version(start) >= target:
                raise ValueError("BIG-IP starting versions must be older than the target")
        if result.volume and not re.fullmatch(r"HD\d+\.\d+", result.volume):
            raise ValueError("volume must be a boot location such as HD1.2")
        if result.license_check_date:
            try:
                date.fromisoformat(result.license_check_date)
            except ValueError:
                raise ValueError("license_check_date must be YYYY-MM-DD") from None
        if result.image_source:
            if re.match(r"^https?://", result.image_source):
                parsed = urlsplit(result.image_source)
                if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                        or not re.fullmatch(r"[A-Za-z0-9:/_.%~-]+", result.image_source)):
                    raise ValueError("image_source must be an HTTPS URL without credentials or query, or an absolute path on the worker")
                if Path(parsed.path).name != result.image:
                    raise ValueError("image_source basename must match image")
            else:
                source = Path(result.image_source)
                if not source.is_absolute() or source.name != result.image:
                    raise ValueError("image_source must be an absolute path on the worker ending in the image filename")
        return result
