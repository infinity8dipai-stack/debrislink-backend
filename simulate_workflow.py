"""
DebrisLink — End-to-End Workflow Simulation
--------------------------------------------
Drives the full business flow against an in-process FastAPI TestClient:

    1. Register a Builder
    2. Register a Driver
    3. Builder requests a pickup       → trip PENDING
    4. Dispatcher assigns the driver   → trip DISPATCHED
    5. Driver completes the dump       → trip COMPLETED + PDF emitted
    6. Verify the PDF landed on disk

Identifiers are timestamp-suffixed so repeated runs don't trip the unique
constraints on driver phone / lorry registration.

Run:
    python simulate_workflow.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from main import app


# ---------------------------------------------------------------------------
# Tiny console helpers (no rich/colorama dependency)
# ---------------------------------------------------------------------------
def _step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")
    print("    " + "─" * (len(title) + 4))


def _ok(label: str, detail: str = "") -> None:
    suffix = f" — {detail}" if detail else ""
    print(f"    ✓ {label}{suffix}")


def _fail(label: str, detail: str) -> None:
    print(f"    ✗ {label} — {detail}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
def main() -> None:
    suffix = str(int(time.time()))  # uniquifier for re-runnable seeding
    client = TestClient(app)

    print("═" * 60)
    print("  DebrisLink — End-to-End Workflow Simulation")
    print("═" * 60)

    # -----------------------------------------------------------------
    _step(1, "Register Builder")
    builder_payload = {
        "company_name": f"Skyline Constructions Pvt Ltd #{suffix}",
        "site_address": "Plot 14, Whitefield Industrial Area, Bengaluru, KA",
        "gps_latitude": 12.9698,
        "gps_longitude": 77.7500,
        "contact_number": f"+9198{suffix[-8:]}",
        "email": f"ops+{suffix}@skyline.test",
    }
    r = client.post("/api/v1/auth/register-builder", json=builder_payload)
    if r.status_code != 201:
        _fail("register-builder", f"{r.status_code} {r.text}")
    builder = r.json()
    _ok("Builder created", f"id={builder['id']} • {builder['company_name']}")

    # -----------------------------------------------------------------
    _step(2, "Register Driver")
    driver_payload = {
        "driver_name": f"Ramesh K. #{suffix}",
        "phone_number": f"+9197{suffix[-8:]}",
        "lorry_registration_number": f"KA01HX{suffix[-4:]}",
    }
    r = client.post("/api/v1/auth/register-driver", json=driver_payload)
    if r.status_code != 201:
        _fail("register-driver", f"{r.status_code} {r.text}")
    driver = r.json()
    _ok(
        "Driver created",
        f"id={driver['id']} • {driver['driver_name']} • {driver['lorry_registration_number']}",
    )

    # -----------------------------------------------------------------
    _step(3, "Builder requests a pickup")
    r = client.post("/api/v1/trips/request", json={"builder_id": builder["id"]})
    if r.status_code != 201:
        _fail("trips/request", f"{r.status_code} {r.text}")
    trip = r.json()
    _ok("Trip created", f"id={trip['id']} • status={trip['status']}")
    assert trip["status"] == "Pending"

    # -----------------------------------------------------------------
    _step(4, "Dispatcher assigns driver to trip")
    r = client.post(
        f"/api/v1/trips/assign/{trip['id']}",
        json={"driver_id": driver["id"]},
    )
    if r.status_code != 200:
        _fail("trips/assign", f"{r.status_code} {r.text}")
    trip = r.json()
    _ok("Trip dispatched", f"truck_id={trip['truck_id']} • status={trip['status']}")
    assert trip["status"] == "Dispatched"

    # -----------------------------------------------------------------
    _step(5, "Driver completes dump at recycling plant")
    completion_payload = {
        "weight_tons": 4.75,
        "recycling_plant_name": "GreenCycle C&D Recyclers, Hoskote",
    }
    r = client.post(
        f"/api/v1/trips/complete/{trip['id']}",
        json=completion_payload,
    )
    if r.status_code != 200:
        _fail("trips/complete", f"{r.status_code} {r.text}")
    trip = r.json()
    _ok("Trip completed", f"status={trip['status']}")
    _ok("Hash generated", trip["certificate_hash"])
    _ok("PDF path", trip["certificate_pdf_path"])
    assert trip["status"] == "Completed"
    assert trip["certificate_hash"] and len(trip["certificate_hash"]) == 64

    # -----------------------------------------------------------------
    _step(6, "Verify PDF certificate on disk")
    pdf_path = Path(trip["certificate_pdf_path"])
    if not pdf_path.exists():
        _fail("PDF existence", f"file missing at {pdf_path}")
    size_kb = pdf_path.stat().st_size / 1024
    _ok("PDF written", f"{pdf_path.name} ({size_kb:.1f} KB)")

    # -----------------------------------------------------------------
    _step(7, "Negative test — re-completing an already-completed trip must 400")
    r = client.post(
        f"/api/v1/trips/complete/{trip['id']}",
        json=completion_payload,
    )
    if r.status_code == 400:
        _ok("State machine enforced", "400 returned as expected")
    else:
        _fail("State guard", f"expected 400, got {r.status_code}: {r.text}")

    # -----------------------------------------------------------------
    _step(8, "Negative test — 404 on unknown trip id")
    r = client.post("/api/v1/trips/assign/999999", json={"driver_id": driver["id"]})
    if r.status_code == 404:
        _ok("Unknown trip rejected", "404 returned as expected")
    else:
        _fail("404 guard", f"expected 404, got {r.status_code}: {r.text}")

    print("\n" + "═" * 60)
    print("  ✅  Full workflow simulation passed.")
    print(f"  📄  Certificate: {pdf_path}")
    print("═" * 60)


if __name__ == "__main__":
    main()
