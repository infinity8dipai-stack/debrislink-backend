"""
DebrisLink — Auth / Onboarding Router
--------------------------------------
Lightweight registration endpoints for the two human actors in the system:
  * Builders (construction companies generating C&D waste)
  * Drivers  (independent lorry operators in the fleet)

In production these would sit behind WhatsApp OTP verification, but for the
MVP we trust the inbound payload and simply persist it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import Builder, Truck
from schemas import BuilderCreateIn, BuilderOut, DriverCreateIn, DriverOut


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Builder onboarding
# ---------------------------------------------------------------------------
@router.post(
    "/register-builder",
    response_model=BuilderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new builder / construction site",
)
def register_builder(
    payload: BuilderCreateIn,
    db: Session = Depends(get_db),
) -> Builder:
    """Persist a new Builder row and return the serialized record."""
    builder = Builder(
        company_name=payload.company_name,
        site_address=payload.site_address,
        gps_latitude=payload.gps_latitude,
        gps_longitude=payload.gps_longitude,
        contact_number=payload.contact_number,
        email=str(payload.email),
    )
    db.add(builder)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Builder could not be created (integrity violation).",
        ) from exc

    db.refresh(builder)
    return builder


# ---------------------------------------------------------------------------
# Driver onboarding
# ---------------------------------------------------------------------------
@router.post(
    "/register-driver",
    response_model=DriverOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new driver / lorry",
)
def register_driver(
    payload: DriverCreateIn,
    db: Session = Depends(get_db),
) -> Truck:
    """Persist a new Truck row and return the serialized record."""
    truck = Truck(
        driver_name=payload.driver_name,
        phone_number=payload.phone_number,
        lorry_registration_number=payload.lorry_registration_number.upper(),
        active_status=True,
    )
    db.add(truck)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Unique constraint on phone_number or lorry_registration_number.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Phone number or lorry registration is already on file.",
        ) from exc

    db.refresh(truck)
    return truck
