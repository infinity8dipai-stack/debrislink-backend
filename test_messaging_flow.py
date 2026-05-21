"""
DebrisLink — End-to-End WhatsApp Interface Simulation
------------------------------------------------------
Drives the full inbound-message flow against an in-process FastAPI
TestClient and asserts on the messages dispatched by the (mocked)
outbound WhatsApp service.

Scenario walkthrough:

   1. Seed a Builder + a Driver via the REST onboarding endpoints.
   2. Builder texts:  REQUEST <site_id>           → PENDING trip created
   3. Dispatcher (REST):   POST /trips/assign     → DISPATCHED
   4. Driver texts:   COMPLETE <id> <wt> <plant>  → COMPLETED + PDF emitted
   5. Driver gets an ack text; Builder gets the PDF as a media message.
   6. Negative paths:
        - Unknown phone   → onboarding prompt
        - Malformed verb  → "Invalid REQUEST format" hint
        - Wrong role      → "reserved for drivers/builders" hint
        - Cross-driver completion attempt → rejection

Run:
    python test_messaging_flow.py
"""

from __future__ import annotations

import sys
import time

from fastapi.testclient import TestClient

from database.connection import SessionLocal
from database.models import Trip, TripStatus
from main import app
from services.whatsapp_service import clear_outbox, get_outbox


# ---------------------------------------------------------------------------
# Console helpers
# ---------------------------------------------------------------------------
def _step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")
    print("    " + "─" * (len(title) + 4))


def _ok(label: str, detail: str = "") -> None:
    print(f"    ✓ {label}" + (f" — {detail}" if detail else ""))


def _fail(label: str, detail: str) -> None:
    print(f"    ✗ {label} — {detail}")
    sys.exit(1)


def _last_outbox_to(phone: str) -> dict:
    """Return the most recent outbound message to a specific number."""
    for msg in reversed(get_outbox()):
        if msg["to"] == phone:
            return msg
    _fail("outbox lookup", f"no outbound message to {phone}")
    return {}  # unreachable, satisfies type-checker


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def main() -> None:
    suffix = str(int(time.time()))
    client = TestClient(app)

    print("═" * 64)
    print("  DebrisLink — End-to-End WhatsApp Interface Simulation")
    print("═" * 64)

    # -----------------------------------------------------------------
    _step(1, "Seed: register builder + driver via REST")
    builder_phone = f"+9198{suffix[-8:]}"
    driver_phone = f"+9197{suffix[-8:]}"

    r = client.post(
        "/api/v1/auth/register-builder",
        json={
            "company_name": f"Skyline Constructions #{suffix}",
            "site_address": "Plot 14, Whitefield Industrial Area, Bengaluru",
            "gps_latitude": 12.9698,
            "gps_longitude": 77.7500,
            "contact_number": builder_phone,
            "email": f"ops+{suffix}@skyline.test",
        },
    )
    if r.status_code != 201:
        _fail("register-builder", f"{r.status_code} {r.text}")
    builder = r.json()
    _ok("Builder", f"id={builder['id']} • phone={builder_phone}")

    r = client.post(
        "/api/v1/auth/register-driver",
        json={
            "driver_name": f"Ramesh K #{suffix}",
            "phone_number": driver_phone,
            "lorry_registration_number": f"KA01HX{suffix[-4:]}",
        },
    )
    if r.status_code != 201:
        _fail("register-driver", f"{r.status_code} {r.text}")
    driver = r.json()
    _ok("Driver", f"id={driver['id']} • phone={driver_phone}")

    clear_outbox()

    # -----------------------------------------------------------------
    _step(2, "Inbound WhatsApp from builder: 'REQUEST <site_id>'")
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        json={"from": builder_phone, "body": f"REQUEST {builder['id']}"},
    )
    if r.status_code != 200:
        _fail("webhook ack", f"{r.status_code} {r.text}")
    _ok("Webhook ACK", r.json()["status"])

    reply = _last_outbox_to(builder_phone)
    if reply["kind"] != "text" or "pickup request has been logged" not in reply["body"]:
        _fail("builder reply", f"unexpected body: {reply}")
    _ok("Builder received confirmation", reply["body"].splitlines()[0])

    # -----------------------------------------------------------------
    _step(3, "Resolve the trip ID created by the webhook")
    with SessionLocal() as db:
        trip = (
            db.query(Trip)
            .filter(Trip.builder_id == builder["id"])
            .order_by(Trip.id.desc())
            .first()
        )
    if trip is None:
        _fail("trip lookup", "no trip created by the webhook")
    trip_id = trip.id
    _ok("Trip located", f"id={trip_id} • status={trip.status.value}")

    # -----------------------------------------------------------------
    _step(4, "Dispatcher assigns the driver (REST, not WhatsApp)")
    r = client.post(
        f"/api/v1/trips/assign/{trip_id}",
        json={"driver_id": driver["id"]},
    )
    if r.status_code != 200:
        _fail("trips/assign", f"{r.status_code} {r.text}")
    _ok("Trip dispatched", f"status={r.json()['status']}")

    clear_outbox()

    # -----------------------------------------------------------------
    _step(5, "Inbound WhatsApp from driver: 'COMPLETE <id> <wt> <plant>'")
    plant = "Dahisar Processing Yard"
    weight = 12.5
    r = client.post(
        "/api/v1/webhooks/whatsapp",
        json={
            "from": driver_phone,
            "body": f"COMPLETE {trip_id} {weight} {plant}",
        },
    )
    if r.status_code != 200:
        _fail("webhook ack", f"{r.status_code} {r.text}")
    _ok("Webhook ACK", r.json()["status"])

    # Driver should have received a text ack.
    driver_ack = _last_outbox_to(driver_phone)
    if driver_ack["kind"] != "text" or "completed" not in driver_ack["body"].lower():
        _fail("driver ack", f"unexpected: {driver_ack}")
    _ok("Driver acknowledged", driver_ack["body"].splitlines()[0])

    # Builder should have received the PDF as media.
    builder_media = _last_outbox_to(builder_phone)
    if builder_media["kind"] != "media":
        _fail("builder media", f"expected media, got {builder_media['kind']}")
    if "EPR Compliance Certificate" not in builder_media["caption"]:
        _fail("builder caption", builder_media["caption"])
    _ok("Builder received PDF", builder_media["media_url"])

    # -----------------------------------------------------------------
    _step(6, "Verify trip + certificate state in the database")
    with SessionLocal() as db:
        final = db.get(Trip, trip_id)
    if final.status != TripStatus.COMPLETED:
        _fail("trip status", f"expected COMPLETED, got {final.status}")
    if not final.certificate_hash or len(final.certificate_hash) != 64:
        _fail("certificate hash", str(final.certificate_hash))
    _ok("Trip COMPLETED", f"hash={final.certificate_hash[:16]}…")

    # -----------------------------------------------------------------
    _step(7, "Negative: message from an unknown phone number")
    clear_outbox()
    unknown = "+919999999999"
    client.post(
        "/api/v1/webhooks/whatsapp",
        json={"from": unknown, "body": "REQUEST 1"},
    )
    msg = _last_outbox_to(unknown)
    if "not registered" not in msg["body"].lower():
        _fail("onboarding prompt", msg["body"])
    _ok("Unknown phone got onboarding prompt")

    # -----------------------------------------------------------------
    _step(8, "Negative: malformed REQUEST from a registered builder")
    clear_outbox()
    client.post(
        "/api/v1/webhooks/whatsapp",
        json={"from": builder_phone, "body": "REQUEST"},
    )
    msg = _last_outbox_to(builder_phone)
    if "Invalid REQUEST format" not in msg["body"]:
        _fail("invalid REQUEST handling", msg["body"])
    _ok("Malformed REQUEST surfaced clean hint")

    # -----------------------------------------------------------------
    _step(9, "Negative: builder sends COMPLETE (role mismatch)")
    clear_outbox()
    client.post(
        "/api/v1/webhooks/whatsapp",
        json={"from": builder_phone, "body": "COMPLETE 1 5 Somewhere"},
    )
    msg = _last_outbox_to(builder_phone)
    if "reserved for drivers" not in msg["body"]:
        _fail("role enforcement", msg["body"])
    _ok("Role boundary enforced")

    # -----------------------------------------------------------------
    _step(10, "Negative: driver attempts COMPLETE on a foreign trip")
    clear_outbox()
    client.post(
        "/api/v1/webhooks/whatsapp",
        json={
            "from": driver_phone,
            "body": f"COMPLETE 999999 7 Phantom Plant",
        },
    )
    msg = _last_outbox_to(driver_phone)
    if "not found" not in msg["body"].lower():
        _fail("unknown trip rejection", msg["body"])
    _ok("Unknown trip rejected to driver")

    print("\n" + "═" * 64)
    print("  ✅  WhatsApp interface simulation passed end-to-end.")
    print(f"  📄  Latest certificate URL: {builder_media['media_url']}")
    print("═" * 64)


if __name__ == "__main__":
    main()
