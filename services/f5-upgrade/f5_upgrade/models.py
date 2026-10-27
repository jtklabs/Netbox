from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")]
Version = Annotated[str, Field(pattern=r"^\d+(?:\.\d+){1,5}$")]
Volume = Annotated[str, Field(pattern=r"^(?:HD|MD)\d+\.\d+$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Image(Model):
    name: Name
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    version: Version
    build: Version
    kind: Literal["image", "hotfix"] = "image"


class JobRequest(Model):
    device: Name
    transfer: bool = False
    images_on_device: bool = False
    images: list[Image] = Field(default_factory=list, max_length=10)
    create_ucs: bool = False
    ucs_unencrypted: bool = False
    backup_directory: str | None = Field(default=None, max_length=240)
    install: bool = False
    target_volume: Volume | None = None
    allowed_source_versions: list[Version] = Field(default_factory=list, max_length=100)
    expected_version: Version | None = None
    expected_build: Version | None = None
    reboot: bool = False
    drain: bool = False
    drain_connections: int | None = Field(default=None, ge=0)
    drain_percent: float | None = Field(default=None, ge=0, le=100)
    drain_timeout: int = Field(default=300, ge=0, le=86400)
    reboot_on_drain_timeout: bool = False
    install_timeout: int = Field(default=3600, ge=60, le=86400)
    reboot_timeout: int = Field(default=900, ge=60, le=7200)
    validation_timeout: int = Field(default=180, ge=0, le=3600)

    @model_validator(mode="after")
    def coherent(self):
        if self.backup_directory is not None:
            p = PurePosixPath(self.backup_directory)
            if p.is_absolute() or ".." in p.parts or not p.parts:
                raise ValueError("backup_directory must be a relative directory under BACKUP_ROOT")
        if self.drain_connections is not None and self.drain_percent is not None:
            raise ValueError("choose a connection count or a percent to drain, not both")
        if self.drain_connections is not None or self.drain_percent is not None:
            self.drain = True
        if self.drain and not self.reboot:
            raise ValueError("drain requires reboot")
        if self.reboot_on_drain_timeout and not self.drain:
            raise ValueError("reboot_on_drain_timeout requires drain")
        if self.images_on_device and self.transfer:
            raise ValueError("images_on_device conflicts with transfer")
        if (self.install or self.transfer) and not self.images:
            raise ValueError("install and transfer require images with SHA-256, version and build")
        if len({i.name for i in self.images}) != len(self.images):
            raise ValueError("image names must be unique")
        if self.install or self.reboot:
            if not self.target_volume or not self.allowed_source_versions:
                raise ValueError("install/reboot require target_volume and allowed_source_versions")
        if self.install:
            final = self.images[-1]
            if self.expected_version and self.expected_version != final.version:
                raise ValueError("expected_version must match final image")
            if self.expected_build and self.expected_build != final.build:
                raise ValueError("expected_build must match final image")
            self.expected_version, self.expected_build = final.version, final.build
        if self.reboot and (not self.expected_version or not self.expected_build):
            raise ValueError("reboot requires expected_version and expected_build")
        return self

    def plan(self):
        steps = ["prevalidate"]
        if self.create_ucs or self.ucs_unencrypted or self.backup_directory or self.install or self.reboot:
            steps += ["save_config", "create_ucs", "copy_ucs"]
        if self.transfer or (self.install and not self.images_on_device):
            steps += ["transfer"]
        if self.install:
            steps += ["verify_images", "install"]
        if self.drain:
            steps += ["drain"]
        if self.reboot:
            steps += ["reboot", "postvalidate"]
        else:
            steps += ["postvalidate_no_reboot"]
        return steps


class RollbackRequest(Model):
    reboot_timeout: int = Field(default=900, ge=60, le=7200)
    validation_timeout: int = Field(default=180, ge=0, le=3600)


class RecoveryResolution(Model):
    worker_stopped: Literal[True]
    device_inspected: Literal[True]
    note: str = Field(min_length=10, max_length=1000)
