"""
DebrisLink — Outbound WhatsApp Notification Service (Meta Cloud API)
---------------------------------------------------------------------
Sends text and document messages via the official Meta Graph endpoint:

    POST https://graph.facebook.com/{version}/{phone_id}/messages
    Authorization: Bearer {WHATSAPP_ACCESS_TOKEN}
    Content-Type:  application/json

Behavior:
  * `WHATSAPP_DRY_RUN=true`  → log + record to `_OUTBOX`, skip the network
                               call. Use during local dev and tests.
  * `WHATSAPP_DRY_RUN=false` → real HTTP POST to Meta.

Every dispatch (live or dry-run) appends a record to the in-memory
`_OUTBOX` for observability and assertion in tests.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings


logger = logging.getLogger("debrislink.whatsapp")


# ---------------------------------------------------------------------------
# Observability outbox — populated on every send (dry-run or live)
# ---------------------------------------------------------------------------
_OUTBOX: list[dict[str, Any]] = []


def get_outbox() -> list[dict[str, Any]]:
    """Return a shallow copy of every dispatch recorded so far."""
    return list(_OUTBOX)


def clear_outbox() -> None:
    """Reset the outbox — call this between test scenarios."""
    _OUTBOX.clear()


# ---------------------------------------------------------------------------
# Shared async HTTP client — created lazily, reused across requests
# ---------------------------------------------------------------------------
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Build the singleton client on first use; reuse thereafter."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
            headers={
                "Authorization": f"Bearer {settings.whatsapp_access_token}",
                "Content-Type": "application/json",
                "User-Agent": "DebrisLink/0.4 (+https://debrislink.in)",
            },
        )
    return _client


async def close_whatsapp_client() -> None:
    """Close the shared client. Wire into FastAPI's lifespan shutdown hook."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def send_whatsapp_text(to_number: str, body: str) -> dict[str, Any]:
    """
    Send a plain-text WhatsApp message.

    Meta payload:
        {
          "messaging_product": "whatsapp",
          "recipient_type":    "individual",
          "to":   "<E.164 without '+'>",
          "type": "text",
          "text": {"preview_url": false, "body": "..."}
        }
    """
    meta_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _to_meta_format(to_number),
        "type": "text",
        "text": {
            "preview_url": False,
            "body": body,
        },
    }
    record = _make_record(
        kind="text",
        to=to_number,
        body=body,
        payload=meta_payload,
    )
    await _dispatch(record)
    return record


async def send_whatsapp_media(
    to_number: str,
    media_url: str,
    caption: str,
) -> dict[str, Any]:
    """
    Send a document (PDF) WhatsApp message by HTTPS link. WhatsApp will
    render this as a downloadable attachment native to the chat thread.

    Meta payload:
        {
          "messaging_product": "whatsapp",
          "recipient_type":    "individual",
          "to":   "<E.164 without '+'>",
          "type": "document",
          "document": {
            "link":     "https://.../Certificate.pdf",
            "caption":  "...",
            "filename": "Certificate.pdf"
          }
        }
    """
    filename = media_url.rstrip("/").rsplit("/", 1)[-1] or "Certificate.pdf"

    meta_payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": _to_meta_format(to_number),
        "type": "document",
        "document": {
            "link": media_url,
            "caption": caption,
            "filename": filename,
        },
    }
    record = _make_record(
        kind="media",
        to=to_number,
        caption=caption,
        media_url=media_url,
        payload=meta_payload,
    )
    await _dispatch(record)
    return record


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _to_meta_format(phone: str) -> str:
    """E.164 without the leading '+' — Meta's required `to` format."""
    phone = phone.strip()
    if phone.lower().startswith("whatsapp:"):
        phone = phone[len("whatsapp:"):].strip()
    if phone.startswith("+"):
        phone = phone[1:]
    return phone


def _make_record(
    *,
    kind: str,
    to: str,
    payload: dict[str, Any],
    body: str | None = None,
    caption: str | None = None,
    media_url: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"local-{uuid.uuid4().hex[:16]}",
        "kind": kind,
        "to": to,
        "body": body,
        "caption": caption,
        "media_url": media_url,
        "payload": payload,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response": None,
        "error": None,
    }


async def _dispatch(record: dict[str, Any]) -> None:
    """Record + render + (optionally) hit Meta."""
    _OUTBOX.append(record)
    _render_block(record)

    if settings.whatsapp_dry_run:
        record["response"] = {"dry_run": True, "messages": [{"id": "mock-meta-id"}]}
        logger.info("WHATSAPP DRY_RUN — skipped HTTP call to Meta for %s", record["to"])
        return

    url = settings.whatsapp_messages_endpoint
    client = _get_client()

    try:
        resp = await client.post(url, json=record["payload"])
    except httpx.RequestError as exc:
        logger.error("Network error contacting Meta Graph API: %s", exc)
        record["error"] = {"type": "network", "detail": str(exc)}
        return

    if resp.status_code >= 400:
        logger.error(
            "Meta Graph API returned %s: %s", resp.status_code, resp.text[:500]
        )
        record["error"] = {
            "type": "http",
            "status": resp.status_code,
            "detail": resp.text,
        }
        return

    try:
        record["response"] = resp.json()
    except ValueError:
        record["response"] = {"raw": resp.text}

    logger.info(
        "Meta accepted %s message to %s (wamid=%s)",
        record["kind"],
        record["to"],
        (record["response"].get("messages") or [{}])[0].get("id", "?"),
    )


def _render_block(msg: dict[str, Any]) -> None:
    """Pretty-print a highly visible outbound API log block."""
    width = 78
    bar_top = "┏" + "━" * (width - 2) + "┓"
    bar_mid = "┣" + "━" * (width - 2) + "┫"
    bar_bot = "┗" + "━" * (width - 2) + "┛"

    def row(s: str = "") -> str:
        s = s[: width - 4]
        return f"┃ {s.ljust(width - 4)} ┃"

    mode = "DRY_RUN" if settings.whatsapp_dry_run else "LIVE"
    endpoint = settings.whatsapp_messages_endpoint

    print()
    print(bar_top)
    print(
        row(
            f"📤  OUTBOUND WHATSAPP · {msg['kind'].upper():<5} [{mode}]   "
            f"{msg['timestamp']}"
        )
    )
    print(row(f"POST {endpoint}"))
    print(row(f"TO:  {msg['to']}"))
    print(row(f"ID:  {msg['id']}"))
    print(bar_mid)

    if msg["kind"] == "text":
        for line in (msg["body"] or "").splitlines() or [""]:
            print(row(line))
    else:
        print(row(f"MEDIA URL: {msg['media_url']}"))
        print(row("CAPTION:"))
        for line in (msg["caption"] or "").splitlines() or [""]:
            print(row(line))

    print(bar_bot)
