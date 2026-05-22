"""
DebrisLink — Application Configuration
---------------------------------------
Single source of truth for runtime configuration. Loads from `.env`
using pydantic-settings v2, validates types, and exposes a `settings`
singleton importable from anywhere:

    from config import settings
    print(settings.whatsapp_messages_endpoint)

Tests / overrides:
    Use the lru-cached `get_settings()` if you want to inject overrides
    via FastAPI's `Depends(get_settings)`. The module-level `settings`
    is fine for non-DI imports (services, routers, scripts).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration. Field names map to UPPER_CASE env keys."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ----------------------------------------------------------------
    # Meta WhatsApp Cloud API
    # Empty string = not configured yet (app still boots; WhatsApp
    # features return errors only when actually invoked).
    # ----------------------------------------------------------------
    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "debrislink-hook-secret"
    whatsapp_api_version: str = "v21.0"
    whatsapp_graph_base_url: str = "https://graph.facebook.com"

    # App Secret used to verify inbound webhook HMAC signatures.
    # Empty string ⇒ signature verification is bypassed (local dev only).
    whatsapp_app_secret: str = ""

    # When true, outbound calls are short-circuited (no HTTP to Meta).
    whatsapp_dry_run: bool = True

    # ----------------------------------------------------------------
    # Public media hosting (Meta requires HTTPS URLs for documents)
    # ----------------------------------------------------------------
    public_media_base_url: str = "https://debrislink.in/certificates"

    # ----------------------------------------------------------------
    # Certificate hashing
    # ----------------------------------------------------------------
    debrislink_hash_pepper: str = "debrislink-mvp-pepper"

    # ----------------------------------------------------------------
    # Derived values
    # ----------------------------------------------------------------
    @property
    def whatsapp_messages_endpoint(self) -> str:
        """Full Graph API endpoint for POSTing messages to this phone number."""
        return (
            f"{self.whatsapp_graph_base_url.rstrip('/')}"
            f"/{self.whatsapp_api_version}"
            f"/{self.whatsapp_phone_number_id}/messages"
        )


@lru_cache
def get_settings() -> Settings:
    """Cached factory — useful as a FastAPI dependency override target."""
    return Settings()


# Module-level singleton — pre-evaluated at import time so missing required
# secrets fail-fast on app startup rather than at first message.
settings = get_settings()
