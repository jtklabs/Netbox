"""PAN-OS firewall upgrades through Panorama: requests, policies and batches."""

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Model, Name, ScheduledRequest

Serial = Annotated[str, Field(pattern=r"^[0-9A-Za-z]{6,32}$")]
PanosVersion = Annotated[str, Field(pattern=r"^\d{1,2}\.\d{1,2}\.\d{1,3}(?:-[a-z]{1,3}\d{1,4})?$")]


def feature_release(version):
    return ".".join(version.split(".")[:2])


def within_window(batch, request):
    """Per-device windows may narrow the batch window but never widen it."""
    starts = [value for value in (batch.start_at, request.start_at) if value is not None]
    deadlines = [value for value in (batch.latest_start_at, request.latest_start_at) if value is not None]
    return type(request).model_validate(
        {
            **request.model_dump(),
            "start_at": max(starts) if starts else None,
            "latest_start_at": min(deadlines) if deadlines else None,
        }
    )


class PanosValidationPolicy(Model):
    noncritical_differences: Literal["warn", "require_review"] = "warn"
    on_timeout: Literal["needs_review", "fail"] = "needs_review"
    consecutive_passes: int = Field(default=2, ge=1, le=10)
    settling_seconds: int = Field(default=60, ge=0, le=3600)
    sample_interval_seconds: int = Field(default=15, ge=1, le=300)
    restore_required: bool = Field(
        default=True,
        description="Interfaces that were up, BGP peers that were Established and IPsec tunnels that were active must recover; false reports them as warnings.",
    )
    min_route_percent: float | None = Field(default=90, ge=0, le=100)
    min_session_percent: float | None = Field(default=None, ge=0, le=100)
    min_arp_percent: float | None = Field(default=None, ge=0, le=100)


class PanosHAPolicy(Model):
    upgrade_order: Literal["standby_first", "active_first"] = Field(
        default="standby_first",
        description="standby_first upgrades the passive (active/passive) or active-secondary (active/active) member first, validates it, then fails over to it.",
    )
    require_running_sync: bool = True
    disable_preemption: bool = Field(
        default=False,
        description="Before the first suspend, set preemptive no on members that have it enabled and commit; restore and commit after both members validate.",
    )
    failback: bool = Field(
        default=True,
        description="When the member left carrying traffic fails validation after a failover, return traffic to the other member before stopping.",
    )


class PanosUpgradeRequest(ScheduledRequest):
    serial: Serial
    panorama: Name = Field(default="panorama", description="HTTP target ID of the managing Panorama")
    credential_profiles: list[Name] | None = Field(default=None, min_length=1, max_length=10)
    target_version: PanosVersion
    download_versions: list[PanosVersion] = Field(
        default_factory=list,
        max_length=5,
        description="Extra versions to download but not install, such as intermediate base images.",
    )
    auto_base_image: bool = Field(
        default=True,
        description="Download the X.Y.0 base image first when the target is a maintenance release of another feature release.",
    )
    software_source: Literal["panorama", "update_server"] = Field(
        default="panorama",
        description="panorama pushes images from Panorama's software repository, so firewalls need no internet access; update_server makes each firewall download its own.",
    )
    panorama_download: bool = Field(
        default=True,
        description="Let Panorama download a missing image from its update server; false requires the file already in Panorama's repository.",
    )
    software_family: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9]{1,16}$",
        description="Override the platform family Panorama reports, used in image filenames such as PanOS_3200-11.1.6-h3.",
    )
    allowed_source_versions: list[PanosVersion] = Field(min_length=1, max_length=200)
    approved_models: list[str] = Field(default_factory=list, max_length=100)
    stage_only: bool = Field(
        default=False, description="Run prechecks and downloads; never install or reboot."
    )
    config_backup: bool = True
    tech_support: bool = Field(
        default=True, description="Save a tech support file from each member before changes."
    )
    backup_directory: str | None = Field(default=None, max_length=240)
    validation_policy: PanosValidationPolicy = Field(default_factory=PanosValidationPolicy)
    ha_policy: PanosHAPolicy = Field(default_factory=PanosHAPolicy)
    job_wait_timeout: int = Field(default=600, ge=0, le=7200)
    tech_support_timeout: int = Field(default=1800, ge=60, le=7200)
    commit_timeout: int = Field(default=900, ge=60, le=7200)
    download_timeout: int = Field(default=3600, ge=60, le=14400)
    install_timeout: int = Field(default=3600, ge=60, le=14400)
    reboot_timeout: int = Field(default=2700, ge=300, le=7200)
    validation_timeout: int = Field(default=900, ge=0, le=86400)

    @model_validator(mode="after")
    def coherent(self):
        if self.backup_directory is not None:
            p = PurePosixPath(self.backup_directory)
            if p.is_absolute() or ".." in p.parts or not p.parts:
                raise ValueError("backup_directory must be a relative directory under BACKUP_ROOT")
        self.download_versions = [
            v for v in dict.fromkeys(self.download_versions) if v != self.target_version
        ]
        return self

    def plan(self):
        """Steps per member; an HA pair runs them for each member that is not on the target."""
        steps = ["discover_ha", "prevalidate"]
        if self.config_backup or self.tech_support:
            steps += ["backup"]
        if self.software_source == "panorama":
            steps += ["panorama_repository", "upload_to_firewall"]
        else:
            steps += ["software_check", "download"]
        if self.stage_only:
            return steps
        preemption = self.ha_policy.disable_preemption
        return (
            steps
            + (["ha_disable_preemption_and_commit"] if preemption else [])
            + [
                "ha_suspend_or_handoff_with_survivor_validation",
                "install",
                "reboot",
                "validate_system",
                "ha_functional",
                "validate_pair_or_traffic",
            ]
            + (["ha_restore_preemption_and_commit"] if preemption else [])
            + ["validate_final"]
        )


class PanosWave(Model):
    wave: int = Field(ge=1)
    upgrade_at_a_time: int = Field(default=1, ge=1, le=100)
    serials: list[Serial] = Field(min_length=1, max_length=1000)

    @property
    def members(self):
        return self.serials


class PanosBatch(ScheduledRequest):
    name: str = Field(min_length=1, max_length=160)
    defaults: dict = Field(default_factory=dict)
    overrides: dict[str, dict] = Field(default_factory=dict)
    waves: list[PanosWave] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid(self):
        numbers = [w.wave for w in self.waves]
        if numbers != sorted(set(numbers)):
            raise ValueError("waves must be unique and increasing")
        serials = [s for w in self.waves for s in w.serials]
        if len(serials) != len(set(serials)) or len(serials) > 1000:
            raise ValueError("batch must contain at most 1000 unique serials")
        if not set(self.overrides) <= set(serials):
            raise ValueError("overrides must reference serials in this batch")
        for serial in serials:
            self.request_for(serial)
        return self

    def request_for(self, serial):
        request = PanosUpgradeRequest.model_validate(
            {**self.defaults, **self.overrides.get(serial, {}), "serial": serial}
        )
        return within_window(self, request)
