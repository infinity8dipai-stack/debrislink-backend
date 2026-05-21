"""
DebrisLink — SQLAlchemy ORM Models
-----------------------------------
Three core entities power the MVP:

  Builder  ── 1 ──< Trip >── 1 ── Truck

A Trip is the central transactional record that ties a Builder's debris
request to the Truck that hauled it, and ultimately to the EPR certificate
generated upon disposal at an authorized recycling plant.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database.connection import Base


# ---------------------------------------------------------------------------
# Enum: lifecycle states of a Trip
# ---------------------------------------------------------------------------
class TripStatus(str, enum.Enum):
    PENDING = "Pending"          # Builder has requested; no truck yet assigned.
    DISPATCHED = "Dispatched"    # Truck en route or actively hauling debris.
    COMPLETED = "Completed"      # Waste disposed; certificate eligible.
    CANCELLED = "Cancelled"      # Aborted by builder, driver, or system.


# ---------------------------------------------------------------------------
# Builder (a.k.a. construction site / client)
# ---------------------------------------------------------------------------
class Builder(Base):
    """
    Represents the construction company or site that generates C&D waste
    and is legally responsible for EPR compliance.
    """

    __tablename__ = "builders"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String(255), nullable=False, index=True)

    # Site location — stored both as free-text and structured GPS for routing.
    site_address = Column(Text, nullable=False)
    gps_latitude = Column(Float, nullable=True)
    gps_longitude = Column(Float, nullable=True)

    # Contact channels — phone is mandatory (WhatsApp), email needed for cert delivery.
    contact_number = Column(String(20), nullable=False, index=True)
    email = Column(String(255), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ORM relationships
    trips = relationship(
        "Trip",
        back_populates="builder",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Builder id={self.id} company='{self.company_name}'>"


# ---------------------------------------------------------------------------
# Truck / Driver
# ---------------------------------------------------------------------------
class Truck(Base):
    """
    Independent lorry driver in the DebrisLink fleet network.
    A single physical lorry is registered against a single driver for the MVP.
    """

    __tablename__ = "trucks"

    id = Column(Integer, primary_key=True, index=True)
    driver_name = Column(String(255), nullable=False)
    phone_number = Column(String(20), nullable=False, unique=True, index=True)

    # Indian vehicle plate format e.g. "KA-01-AB-1234" — kept as string.
    lorry_registration_number = Column(
        String(20), nullable=False, unique=True, index=True
    )

    # Soft availability flag — toggled when driver goes on/off duty.
    active_status = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ORM relationships
    trips = relationship("Trip", back_populates="truck")

    def __repr__(self) -> str:
        return (
            f"<Truck id={self.id} driver='{self.driver_name}' "
            f"reg='{self.lorry_registration_number}'>"
        )


# ---------------------------------------------------------------------------
# Trip / Order
# ---------------------------------------------------------------------------
class Trip(Base):
    """
    A single debris-haul transaction from a Builder's site to a recycling plant.
    The certificate_hash is generated at completion and embedded in the PDF
    + QR code for tamper-evident verification.
    """

    __tablename__ = "trips"

    id = Column(Integer, primary_key=True, index=True)

    # Foreign keys
    builder_id = Column(
        Integer, ForeignKey("builders.id"), nullable=False, index=True
    )
    truck_id = Column(
        Integer, ForeignKey("trucks.id"), nullable=True, index=True
    )

    # Lifecycle
    status = Column(
        Enum(TripStatus, native_enum=False, length=20),
        default=TripStatus.PENDING,
        nullable=False,
        index=True,
    )

    # Measurement — both captured because plants weigh and builders volume-estimate.
    weight_tons = Column(Float, nullable=True)
    volume_cubic_meters = Column(Float, nullable=True)

    # Disposal endpoint
    recycling_plant_name = Column(String(255), nullable=True)

    # Timestamps
    pickup_timestamp = Column(DateTime, nullable=True)
    disposal_timestamp = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # SHA-256 hex digest — populated only when status flips to COMPLETED.
    certificate_hash = Column(
        String(64), nullable=True, unique=True, index=True
    )

    # ORM relationships
    builder = relationship("Builder", back_populates="trips")
    truck = relationship("Truck", back_populates="trips")

    def __repr__(self) -> str:
        return (
            f"<Trip id={self.id} status={self.status.value} "
            f"builder_id={self.builder_id} truck_id={self.truck_id}>"
        )
