"""
DebrisLink — Meta WhatsApp Cloud API Webhook Router
----------------------------------------------------
Two endpoints share the same path `/api/v1/webhooks/whatsapp`:

  GET  → Meta's one-time webhook verification handshake.
         Returns the `hub.challenge` value as plain text iff the
         `hub.verify_token` matches our configured secret.

  POST → Inbound message events from Meta. Parses Meta's nested
         payload (entry[0].changes[0].value.messages[0].…), normalizes
         the sender phone to E.164 with '+', and dispatches to the
         Builder / Driver handlers from Step 3.

The POST handler ALWAYS returns 200 to Meta — provider retries on
non-2xx, which would amplify bad payloads. User-facing errors flow
back as WhatsApp replies via `services.whatsapp_service`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from config import settings
from database.connection import get_db
from database.models import Builder, Trip, Truck
from routers.trips import complete_trip, request_trip
from schemas import TripCompleteIn, TripRequestIn
from services.message_parser import CommandType, parse_message
from services.whatsapp_service import send_whatsapp_media, send_whatsapp_text


logger = logging.getLogger("debrislink.webhooks")

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


# ===========================================================================
# HMAC signature verification (X-Hub-Signature-256)
# ===========================================================================
def _verify_meta_signature(raw_body: bytes, signature_header: str | None) -> None:
    """
    Verify Meta's X-Hub-Signature-256 HMAC-SHA256 over the raw request body
    using our App Secret. Bypassed when:
      * WHATSAPP_DRY_RUN=true  (local dev / tests), OR
      * WHATSAPP_APP_SECRET is empty (operator hasn't configured it yet).

    In both bypass cases we log a warning so an unconfigured production
    deploy is loud rather than silent.
    """
    if settings.whatsapp_dry_run:
        return
    if not settings.whatsapp_app_secret:
        logger.warning(
            "WHATSAPP_APP_SECRET is empty — webhook signature verification "
            "is DISABLED. Configure it before going live."
        )
        return

    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing or malformed X-Hub-Signature-256 header.",
        )

    expected = "sha256=" + hmac.new(
        settings.whatsapp_app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(signature_header, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        )


# ===========================================================================
# Meta inbound payload models — model only the fields we read; allow extras.
# ===========================================================================
class MetaTextBody(BaseModel):
    body: str

    model_config = ConfigDict(extra="ignore")


class MetaMessage(BaseModel):
    from_: str = Field(..., alias="from", description="E.164 sender (no '+')")
    id: str
    timestamp: str
    type: str
    text: MetaTextBody | None = None

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class MetaValue(BaseModel):
    messaging_product: str | None = None
    messages: list[MetaMessage] | None = None
    # Delivery/read receipts arrive here. We ignore them but accept the shape.
    statuses: list[dict] | None = None

    model_config = ConfigDict(extra="ignore")


class MetaChange(BaseModel):
    value: MetaValue
    field: str | None = None

    model_config = ConfigDict(extra="ignore")


class MetaEntry(BaseModel):
    id: str
    changes: list[MetaChange]

    model_config = ConfigDict(extra="ignore")


class MetaWebhookPayload(BaseModel):
    object: str
    entry: list[MetaEntry]

    model_config = ConfigDict(extra="ignore")


# ===========================================================================
# GET — Meta verification handshake
# ===========================================================================
@router.get(
    "/whatsapp",
    summary="Meta webhook verification handshake",
    response_class=PlainTextResponse,
)
async def verify_whatsapp_webhook(
    mode: str = Query(..., alias="hub.mode"),
    token: str = Query(..., alias="hub.verify_token"),
    challenge: str = Query(..., alias="hub.challenge"),
) -> PlainTextResponse:
    """
    Meta calls this once when you subscribe a webhook URL in the
    App Dashboard. We must echo `hub.challenge` verbatim as the raw
    body of a 200 response, but only when the verify token matches.
    """
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return PlainTextResponse(content=challenge, status_code=status.HTTP_200_OK)

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Verification token mismatch.",
    )


# ===========================================================================
# POST — Inbound message dispatch
# ===========================================================================
@router.post(
    "/whatsapp",
    summary="Receive inbound WhatsApp messages from Meta Cloud API",
)
async def whatsapp_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """
    Iterates every text message inside the payload and dispatches it.
    Status callbacks (delivery/read receipts) are silently acknowledged.

    Reads the raw body before Pydantic parsing so we can verify Meta's
    X-Hub-Signature-256 HMAC before trusting any payload contents.
    """
    raw_body = await request.body()
    _verify_meta_signature(raw_body, request.headers.get("x-hub-signature-256"))

    try:
        payload = MetaWebhookPayload.model_validate_json(raw_body)
    except ValidationError as exc:
        # Ack to Meta with 200 so they don't retry a malformed payload
        # indefinitely, but log loudly so we can investigate.
        logger.warning("Malformed Meta payload rejected: %s", exc.errors()[:3])
        return {"status": "ignored", "reason": "validation_error"}

    if payload.object != "whatsapp_business_account":
        # Not for us — ack anyway so Meta doesn't retry.
        return {"status": "ignored", "reason": "unsupported object"}

    for entry in payload.entry:
        for change in entry.changes:
            for msg in change.value.messages or []:
                # Skip non-text events (audio, image, stickers, etc.).
                if msg.type != "text" or msg.text is None:
                    continue

                sender = _normalize_phone(msg.from_)
                body = msg.text.body.strip()
                if not body:
                    continue

                await _dispatch_inbound(sender, body, db)

    return {"status": "ack"}


# ===========================================================================
# Sender identification + role dispatch
# ===========================================================================
def _normalize_phone(phone: str) -> str:
    """
    Canonicalize an inbound phone to E.164 with '+' so it matches the
    format we stored on registration. Meta sends '919812345678' (no '+');
    Twilio prefixes with 'whatsapp:'. We handle both.
    """
    phone = phone.strip()
    if phone.lower().startswith("whatsapp:"):
        phone = phone[len("whatsapp:") :].strip()
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone


async def _dispatch_inbound(sender: str, body: str, db: Session) -> None:
    """Look up sender role and route to the appropriate handler."""
    builder = db.query(Builder).filter(Builder.contact_number == sender).first()
    driver = db.query(Truck).filter(Truck.phone_number == sender).first()

    if builder is not None:
        await _handle_builder_message(builder, body, db)
    elif driver is not None:
        await _handle_driver_message(driver, body, db)
    else:
        await send_whatsapp_text(
            sender,
            "👷 Hello! This number isn't registered with DebrisLink.\n"
            "Please complete onboarding at https://debrislink.in/onboard "
            "to request pickups or accept hauls.",
        )


# ===========================================================================
# Builder branch
# ===========================================================================
async def _handle_builder_message(builder: Builder, body: str, db: Session) -> None:
    parsed = parse_message(body)

    if parsed.command == CommandType.REQUEST:
        site_id = parsed.data["site_id"]

        # Security: a builder can only request pickups for their own site.
        if site_id != builder.id:
            await send_whatsapp_text(
                builder.contact_number,
                f"⚠ Site #{site_id} is not registered under your account.\n"
                f"Your registered site ID is #{builder.id}.",
            )
            return

        try:
            trip = request_trip(TripRequestIn(builder_id=builder.id), db=db)
        except HTTPException as exc:
            await send_whatsapp_text(
                builder.contact_number,
                f"⚠ Could not log your request: {exc.detail}",
            )
            return

        await send_whatsapp_text(
            builder.contact_number,
            "✅ Your debris pickup request has been logged.\n"
            f"Trip ID: #{trip.id}\n"
            "Status: Pending dispatch\n"
            "You'll receive a confirmation once a driver is on the way.",
        )

    elif parsed.command == CommandType.COMPLETE:
        await send_whatsapp_text(
            builder.contact_number,
            "ℹ The COMPLETE command is reserved for drivers.\n"
            "Send `REQUEST <SITE_ID>` to request a new pickup.",
        )

    elif parsed.command == CommandType.INVALID:
        await send_whatsapp_text(builder.contact_number, f"⚠ {parsed.error}")

    else:  # UNKNOWN
        await send_whatsapp_text(
            builder.contact_number,
            "ℹ DebrisLink commands:\n"
            f"  • REQUEST {builder.id}   — request a pickup at your site\n"
            "Reply STOP to opt out.",
        )


# ===========================================================================
# Driver branch
# ===========================================================================
async def _handle_driver_message(driver: Truck, body: str, db: Session) -> None:
    parsed = parse_message(body)

    if parsed.command == CommandType.COMPLETE:
        trip_id = parsed.data["trip_id"]

        # Pre-flight: trip must exist and be assigned to THIS driver.
        trip = db.get(Trip, trip_id)
        if trip is None:
            await send_whatsapp_text(
                driver.phone_number,
                f"⚠ Trip #{trip_id} not found. Please double-check the ID.",
            )
            return
        if trip.truck_id != driver.id:
            await send_whatsapp_text(
                driver.phone_number,
                f"⚠ Trip #{trip_id} is not assigned to your lorry.",
            )
            return

        # Validate completion payload via the same Pydantic contract the
        # REST endpoint uses, so rules stay consistent across channels.
        try:
            completion = TripCompleteIn(
                weight_tons=parsed.data["weight_tons"],
                recycling_plant_name=parsed.data["plant_name"],
            )
        except ValidationError as exc:
            first = exc.errors()[0]
            await send_whatsapp_text(
                driver.phone_number,
                f"⚠ Invalid completion payload: {first['msg']}",
            )
            return

        try:
            result = complete_trip(trip_id=trip_id, payload=completion, db=db)
        except HTTPException as exc:
            await send_whatsapp_text(
                driver.phone_number,
                f"⚠ Could not complete trip: {exc.detail}",
            )
            return

        # Re-fetch with relationships populated for downstream messaging.
        completed = db.get(Trip, trip_id)
        builder = completed.builder

        # Resolve the public HTTPS URL Meta needs to serve the document.
        # The local PDF must be uploaded / fronted at PUBLIC_MEDIA_BASE_URL.
        pdf_path = Path(result["certificate_pdf_path"])
        public_media_url = (
            f"{settings.public_media_base_url.rstrip('/')}/{pdf_path.name}"
        )

        # 1. Acknowledge the driver.
        await send_whatsapp_text(
            driver.phone_number,
            f"✅ Trip #{trip_id} completed.\n"
            f"Weight: {completed.weight_tons:.2f} tonnes\n"
            f"Plant: {completed.recycling_plant_name}\n"
            "Your compliance certificate has been generated "
            "and sent to the builder.",
        )

        # 2. Deliver the certificate PDF (document) to the builder.
        await send_whatsapp_media(
            builder.contact_number,
            media_url=public_media_url,
            caption=(
                f"📄 EPR Compliance Certificate — Trip #{trip_id}\n"
                f"Plant: {completed.recycling_plant_name}\n"
                f"Weight: {completed.weight_tons:.2f} tonnes\n"
                f"Hash: {completed.certificate_hash[:16]}…\n"
                "Keep this on file to satisfy E(C&D)WM Rules audit."
            ),
        )

    elif parsed.command == CommandType.REQUEST:
        await send_whatsapp_text(
            driver.phone_number,
            "ℹ The REQUEST command is reserved for builders.\n"
            "Send `COMPLETE <TRIP_ID> <WEIGHT_TONS> <PLANT_NAME>` "
            "when you finish a haul.",
        )

    elif parsed.command == CommandType.INVALID:
        await send_whatsapp_text(driver.phone_number, f"⚠ {parsed.error}")

    else:  # UNKNOWN
        await send_whatsapp_text(
            driver.phone_number,
            "ℹ DebrisLink commands:\n"
            "  • COMPLETE <TRIP_ID> <WEIGHT_TONS> <PLANT_NAME>\n"
            "    e.g.  COMPLETE 42 12 Dahisar Plant",
        )
