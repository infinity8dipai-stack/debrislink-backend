"""
DebrisLink — Public Certificate Verification Router
-----------------------------------------------------
Resolves the SHA-256 hash baked into every EPR Compliance Certificate
PDF (and its embedded QR code) into a minimal, PII-free fact sheet that
auditors, builders, or third parties can use to confirm a certificate
is genuine.

URL shape:  GET /v/{cert_hash}

Mount at the application root (NOT under /api/v1) so the QR code points
to a short, friendly URL like https://debrislink.in/v/<hash>.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database.connection import get_db
from database.models import Trip, TripStatus
from schemas import CertVerifyOut


router = APIRouter(tags=["verification"])


@router.get(
    "/v/{cert_hash}",
    response_model=CertVerifyOut,
    summary="Verify the authenticity of an EPR Compliance Certificate",
)
def verify_certificate(
    cert_hash: str,
    db: Session = Depends(get_db),
) -> CertVerifyOut:
    """
    Look up a Trip by its certificate hash. Returns a minimal, public-safe
    fact sheet if the hash corresponds to a completed disposal.

    404 is returned for unknown hashes AND for trips that exist but are
    not yet in the COMPLETED state — both mean "no valid certificate
    here" from the verifier's point of view.
    """
    # Tight length check — hashes are 64-char hex. Reject obvious garbage
    # before hitting the DB.
    if len(cert_hash) != 64 or not all(c in "0123456789abcdef" for c in cert_hash.lower()):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Certificate not found.",
        )

    trip = (
        db.query(Trip)
        .filter(Trip.certificate_hash == cert_hash.lower())
        .first()
    )
    if trip is None or trip.status != TripStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Certificate not found.",
        )

    return CertVerifyOut(
        valid=True,
        trip_id=trip.id,
        certificate_hash=trip.certificate_hash,
        issued_at=trip.disposal_timestamp,
        builder_company_name=trip.builder.company_name,
        lorry_registration_number=trip.truck.lorry_registration_number,
        weight_tons=trip.weight_tons,
        recycling_plant_name=trip.recycling_plant_name,
    )
