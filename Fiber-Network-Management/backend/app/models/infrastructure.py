"""Passive physical infrastructure: splitters, FATs, distribution points, cables, cores and links.

Passive elements are never polled. Their state is maintained by people (and inferred by
outage correlation), not retrieved from equipment.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, TimestampMixin


class SplitterType(Base, TimestampMixin):
    """Configurable splitter ratios (1:2 … 1:64, 2:32, ...)."""

    __tablename__ = "splitter_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. "1:8"
    input_ports: Mapped[int] = mapped_column(default=1)
    output_ports: Mapped[int]
    nominal_loss_db: Mapped[float | None] = mapped_column(Float)
    splitter_kind: Mapped[str] = mapped_column(String(16), default="plc")  # plc | fbt


class Splitter(Base, TimestampMixin):
    __tablename__ = "splitters"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    splitter_type_id: Mapped[int] = mapped_column(ForeignKey("splitter_types.id"))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    fat_id: Mapped[int | None] = mapped_column(ForeignKey("fats.id", ondelete="SET NULL"))  # housed in a FAT
    dpoint_id: Mapped[int | None] = mapped_column(ForeignKey("distribution_points.id", ondelete="SET NULL"))
    location: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="active")
    estimated_loss_db: Mapped[float | None] = mapped_column(Float)
    installation_date: Mapped[date | None]
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)
    splitter_type: Mapped[SplitterType] = relationship(lazy="selectin")


class Fat(Base, TimestampMixin):
    """Fiber Access Terminal (a.k.a. FDB / FDT / distribution terminal). Passive."""

    __tablename__ = "fats"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    code: Mapped[str | None] = mapped_column(String(64), unique=True)  # identification number
    fat_type: Mapped[str] = mapped_column(String(32), default="pole")  # pole | wall | pedestal | underground | indoor
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    address: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text, default="")
    manufacturer: Mapped[str | None] = mapped_column(String(64))
    installation_date: Mapped[date | None]
    capacity: Mapped[int]  # number of subscriber (output) ports
    status: Mapped[str] = mapped_column(String(16), default="active")
    photos: Mapped[list[Any] | None] = mapped_column(JSON)  # list of {url, caption}
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)


class DistributionPoint(Base, TimestampMixin):
    """ODF / ODB / splice closure / splice point / termination point."""

    __tablename__ = "distribution_points"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    point_type: Mapped[str] = mapped_column(String(16))
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    olt_id: Mapped[int | None] = mapped_column(ForeignKey("olts.id", ondelete="SET NULL"))  # e.g. ODF beside an OLT
    address: Mapped[str] = mapped_column(Text, default="")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    port_count: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), default="active")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)


class InfraPort(Base, TimestampMixin):
    """A port on a passive element (splitter in/out, FAT port, ODF/closure position)."""

    __tablename__ = "infra_ports"
    __table_args__ = (UniqueConstraint("owner_type", "owner_id", "direction", "number"),
                      Index("ix_infra_ports_owner", "owner_type", "owner_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(16))  # splitter | fat | dpoint
    owner_id: Mapped[int]
    direction: Mapped[str] = mapped_column(String(8))    # in | out | through
    number: Mapped[int]
    label: Mapped[str] = mapped_column(String(64), default="")
    # Administrative status. "occupied" is derived from live links; only available/reserved/faulty are set by users.
    status: Mapped[str] = mapped_column(String(16), default="available")
    reserved_for: Mapped[str | None] = mapped_column(String(128))
    reserved_until: Mapped[date | None]
    drop_cable_id: Mapped[int | None] = mapped_column(ForeignKey("cables.id", ondelete="SET NULL"))
    connected_since: Mapped[date | None]
    notes: Mapped[str] = mapped_column(Text, default="")


class Cable(Base, TimestampMixin):
    __tablename__ = "cables"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    cable_type: Mapped[str] = mapped_column(String(16))  # feeder | distribution | drop | backbone | patch
    fiber_count: Mapped[int]
    parent_cable_id: Mapped[int | None] = mapped_column(ForeignKey("cables.id", ondelete="SET NULL"))  # segment of
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    from_description: Mapped[str] = mapped_column(String(255), default="")
    to_description: Mapped[str] = mapped_column(String(255), default="")
    length_m: Mapped[float | None] = mapped_column(Float)
    route: Mapped[list[Any] | None] = mapped_column(JSON)  # [[lat, lng], ...] for GIS
    manufacturer: Mapped[str | None] = mapped_column(String(64))
    installation_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(String(16), default="active")
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)


class FiberCore(Base, TimestampMixin):
    __tablename__ = "fiber_cores"
    __table_args__ = (UniqueConstraint("cable_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    cable_id: Mapped[int] = mapped_column(ForeignKey("cables.id", ondelete="CASCADE"), index=True)
    number: Mapped[int]
    color: Mapped[str | None] = mapped_column(String(32))
    tube: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(16), default="free")  # free | used | reserved | damaged
    notes: Mapped[str] = mapped_column(Text, default="")


class FiberLink(Base, TimestampMixin):
    """A directed physical connection: ``a`` is upstream (towards the OLT), ``b`` is downstream.

    Endpoints are (type, id) pairs: ``olt_port``, ``infra_port`` or ``ont``.
    ``verification`` distinguishes confirmed connections from planned/unverified records.
    """

    __tablename__ = "fiber_links"
    __table_args__ = (Index("ix_links_a", "a_type", "a_id"), Index("ix_links_b", "b_type", "b_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    a_type: Mapped[str] = mapped_column(String(16))
    a_id: Mapped[int]
    b_type: Mapped[str] = mapped_column(String(16))
    b_id: Mapped[int]
    link_type: Mapped[str] = mapped_column(String(16), default="feeder")  # feeder | distribution | drop | patch | splice
    cable_id: Mapped[int | None] = mapped_column(ForeignKey("cables.id", ondelete="SET NULL"))
    core_id: Mapped[int | None] = mapped_column(ForeignKey("fiber_cores.id", ondelete="SET NULL"))
    verification: Mapped[str] = mapped_column(String(16), default="unverified")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | faulty | planned | decommissioned
    length_m: Mapped[float | None] = mapped_column(Float)
    loss_db: Mapped[float | None] = mapped_column(Float)
    verified_at: Mapped[datetime | None]
    verified_by: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, default="")
    is_simulated: Mapped[bool] = mapped_column(default=False)
    cable: Mapped[Cable | None] = relationship(lazy="selectin")
    core: Mapped[FiberCore | None] = relationship(lazy="selectin")
