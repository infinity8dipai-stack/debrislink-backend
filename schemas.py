"""
DebrisLink — Pydantic v2 schemas
---------------------------------
Strict request / response contracts for the FastAPI layer.

Two suffix conventions:
  *In   = inbound payload (request body validation)
  *Out  = outbound payload (ORM → JSON serialization)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# =========================================================================
# Builder schemas
# =========================================================================
class BuilderCreateIn(BaseModel):
    """Payload accepted by POST /api/v1/auth/register-builder."""

    company_name: str = Field(..., min_length=2, max_length=255)
    site_address: str = Field(..., min_length=5)
    gps_latitude: float | None = Field(default=None, ge=-90, le=90)
    gps_longitude: float | None = Field(default=None, ge=-180, le=180)
    contact_number: str = Field(..., min_length=7, max_length=20)
    email: EmailStr


class BuilderOut(BaseModel):
    """Serialized Builder record."""

    id: int
    company_name: str
    site_address: str
    gps_latitude: float | None
    gps_longitude: float | None
    contact_number: str
    email: EmailStr
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =========================================================================
# Driver / Truck schemas
# =========================================================================
class DriverCreateIn(BaseModel):
    """Payload accepted by POST /api/v1/auth/register-driver."""

    driver_name: str = Field(..., min_length=2, max_length=255)
    phone_number: str = Field(..., min_length=7, max_length=20)
    lorry_registration_number: str = Field(..., min_length=4, max_length=20)


class DriverOut(BaseModel):
    """Serialized Truck/Driver record."""

    id: int
    driver_name: str
    phone_number: str
    lorry_registration_number: str
    active_status: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =========================================================================
# Trip schemas
# =========================================================================
class TripRequestIn(BaseModel):
    """POST /api/v1/trips/request — builder requests a pickup."""

    builder_id: int = Field(..., gt=0)


class TripAssignIn(BaseModel):
    """POST /api/v1/trips/assign/{trip_id} — dispatcher assigns a driver."""

    driver_id: int = Field(..., gt=0)


class TripCompleteIn(BaseModel):
    """POST /api/v1/trips/complete/{trip_id} — driver dumps at recycler."""

    weight_tons: float = Field(..., gt=0, le=100)
    recycling_plant_name: str = Field(..., min_length=2, max_length=255)


class TripOut(BaseModel):
    """Serialized Trip record returned by all trip endpoints."""

    id: int
    builder_id: int
    truck_id: int | None
    status: str  # Enum is serialized as its `.value` string.
    weight_tons: float | None
    volume_cubic_meters: float | None
    recycling_plant_name: str | None
    pickup_timestamp: datetime | None
    disposal_timestamp: datetime | None
    certificate_hash: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TripCompleteOut(TripOut):
    """Completion endpoint also returns the on-disk path of the generated PDF."""

    certificate_pdf_path: str


# =========================================================================
# Public certificate verification (resolves the QR-code hash in the PDF)
# =========================================================================
class CertVerifyOut(BaseModel):
    """
    Public response for GET /v/{cert_hash}.

    Intentionally omits PII (email, phone, GPS, full site address) — this
    endpoint is open to the internet so auditors can confirm a certificate
    is genuine without exposing builder/driver contact details.
    """

    valid: bool
    trip_id: int
    certificate_hash: str
    issued_at: datetime
    builder_company_name: str
    lorry_registration_number: str
    weight_tons: float
    recycling_plant_name: str
