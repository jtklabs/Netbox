"""The observation database is independent of NetBox's schema."""
from sqlalchemy import BigInteger, Boolean, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), index=True)
    poller: Mapped[str] = mapped_column(String(120), index=True)
    config: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(120))
    device_key: Mapped[str] = mapped_column(String(120))
    device_name: Mapped[str] = mapped_column(String(255))
    netbox_device_id: Mapped[int | None] = mapped_column(Integer)
    netbox_interface_id: Mapped[int | None] = mapped_column(Integer)
    context: Mapped[str] = mapped_column(String(120))
    vrf: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    mac: Mapped[str] = mapped_column(String(17))
    ip: Mapped[str] = mapped_column(String(45))
    hostname: Mapped[str] = mapped_column(String(255))
    vlan: Mapped[int] = mapped_column(Integer)
    ifindex: Mapped[int] = mapped_column(Integer)
    interface: Mapped[str] = mapped_column(String(255))
    port_role: Mapped[str] = mapped_column(String(16))
    first_seen: Mapped[int] = mapped_column(BigInteger)
    last_seen: Mapped[int] = mapped_column(BigInteger)
    ended_at: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (
        Index("obs_mac", "scope", "mac", "ended_at"),
        Index("obs_ip", "scope", "ip", "ended_at"),
        Index("obs_table", "device_key", "context", "kind", "ended_at"),
        Index("obs_retention", "ended_at"),
        Index("obs_one_active", "device_key", "context", "kind", "mac", "ip", "vlan", "ifindex",
              unique=True, postgresql_where=text("ended_at IS NULL"), sqlite_where=text("ended_at IS NULL")),
    )


class Collection(Base):
    __tablename__ = "collections"
    device_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    context: Mapped[str] = mapped_column(String(120), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope: Mapped[str] = mapped_column(String(120), index=True)
    poller: Mapped[str] = mapped_column(String(120))
    attempted_at: Mapped[int] = mapped_column(BigInteger)
    succeeded_at: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str] = mapped_column(String(255), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)


class Receipt(Base):
    __tablename__ = "receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    principal: Mapped[str] = mapped_column(String(120))
    digest: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[int] = mapped_column(BigInteger, index=True)


class PollerHeartbeat(Base):
    __tablename__ = "poller_heartbeats"
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    last_seen: Mapped[int] = mapped_column(BigInteger)


class SchemaVersion(Base):
    __tablename__ = "schema_version"
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
