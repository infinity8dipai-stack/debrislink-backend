"""
DebrisLink — Trip Lifecycle Router
-----------------------------------
State machine endpoints driving a single haul through its lifecycle:

   request   ──▶  PENDING
   assign    ──▶  DISPATCHED   (driver attached, pickup timestamped)
   complete  ──▶  COMPLETED    (weight + plant logged, hash + PDF emitted)

Any out-of-order transition is rejected with HTTP 400.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import Builder, Trip, TripStatus, Truck
from schemas import (
    TripAssignIn,
    TripCompleteIn,
    TripCompleteOut,
    TripOut,
    TripRequestIn,
)
from services.certificate import generate_certificate


router = APIRouter(prefix="/api/v1/trips", tags=["trips"])


# ---------------------------------------------------------------------------
# Storage / hash configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CERTIFICATE_DIR = PROJECT_ROOT / "storage" / "certificates"

# Pepper added to the hash payload. Override in production via env var.
HASH_PEPPER = os.getenv("DEBRISLINK_HASH_PEPPER", "debrislink-mvp-pepper")


def _build_certificate_hash(trip: Trip) -> str:
    """SHA-256 over the immutable disposal facts + a server-side pepper."""
    payload = "|".join(
        [
            str(trip.id),
            str(trip.builder_id),
            str(trip.truck_id),
            (trip.disposal_timestamp or datetime.now(timezone.utc)).isoformat(),
            f"{trip.weight_tons:.4f}" if trip.weight_tons else "0",
            trip.recycling_plant_name or "",
            HASH_PEPPER,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# GET /api/v1/trips  — list with optional status filter
# ---------------------------------------------------------------------------
# NOTE: this endpoint is currently open. Before going live add admin auth
# (e.g. an API key dependency) so it doesn't leak the full trip log.
@router.get(
    "",
    response_model=list[TripOut],
    summary="List trips (newest first). Optionally filter by status.",
)
def list_trips(
    db: Session = Depends(get_db),
    status_filter: TripStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Trip]:
    query = db.query(Trip)
    if status_filter is not None:
        query = query.filter(Trip.status == status_filter)
    return (
        query.order_by(Trip.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# ---------------------------------------------------------------------------
# GET /api/v1/trips/{trip_id}  — detail
# ---------------------------------------------------------------------------
@router.get(
    "/{trip_id}",
    response_model=TripOut,
    summary="Get a single trip by ID.",
)
def get_trip(trip_id: int, db: Session = Depends(get_db)) -> Trip:
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trip id={trip_id} not found.",
        )
    return trip


# ---------------------------------------------------------------------------
# POST /api/v1/trips/request
# ---------------------------------------------------------------------------
@router.post(
    "/request",
    response_model=TripOut,
    status_code=status.HTTP_201_CREATED,
    summary="Builder requests a debris pickup (creates PENDING trip)",
)
def request_trip(
    payload: TripRequestIn,
    db: Session = Depends(get_db),
) -> Trip:
    builder = db.get(Builder, payload.builder_id)
    if builder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Builder id={payload.builder_id} not found.",
        )

    trip = Trip(
        builder_id=builder.id,
        status=TripStatus.PENDING,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


# ---------------------------------------------------------------------------
# POST /api/v1/trips/assign/{trip_id}
# ---------------------------------------------------------------------------
@router.post(
    "/assign/{trip_id}",
    response_model=TripOut,
    summary="Assign a driver to a PENDING trip (transitions to DISPATCHED)",
)
def assign_trip(
    trip_id: int,
    payload: TripAssignIn,
    db: Session = Depends(get_db),
) -> Trip:
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trip id={trip_id} not found.",
        )

    if trip.status != TripStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Trip is in state '{trip.status.value}'. "
                "Only PENDING trips can be assigned."
            ),
        )

    driver = db.get(Truck, payload.driver_id)
    if driver is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Driver id={payload.driver_id} not found.",
        )
    if not driver.active_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Driver id={driver.id} is not active.",
        )

    trip.truck_id = driver.id
    trip.status = TripStatus.DISPATCHED
    trip.pickup_timestamp = datetime.now(timezone.utc)

    db.commit()
    db.refresh(trip)
    return trip


# ---------------------------------------------------------------------------
# POST /api/v1/trips/complete/{trip_id}
# ---------------------------------------------------------------------------
@router.post(
    "/complete/{trip_id}",
    response_model=TripCompleteOut,
    summary="Mark trip COMPLETED, hash it, and emit the EPR PDF certificate",
)
def complete_trip(
    trip_id: int,
    payload: TripCompleteIn,
    db: Session = Depends(get_db),
) -> dict:
    trip = db.get(Trip, trip_id)
    if trip is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trip id={trip_id} not found.",
        )

    if trip.status != TripStatus.DISPATCHED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Trip is in state '{trip.status.value}'. "
                "Only DISPATCHED trips can be completed."
            ),
        )

    # --- Apply final disposal facts ---
    trip.weight_tons = payload.weight_tons
    trip.recycling_plant_name = payload.recycling_plant_name
    trip.disposal_timestamp = datetime.now(timezone.utc)
    trip.status = TripStatus.COMPLETED
    trip.certificate_hash = _build_certificate_hash(trip)

    db.commit()
    db.refresh(trip)

    # --- Render the PDF certificate to disk ---
    try:
        pdf_path = generate_certificate(trip, CERTIFICATE_DIR)
    except Exception as exc:  # noqa: BLE001 — surface any rendering failure
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Certificate generation failed: {exc}",
        ) from exc

    # FastAPI will serialize the dict against TripCompleteOut.
    return {
        **TripOut.model_validate(trip).model_dump(),
        "certificate_pdf_path": str(pdf_path),
    }
