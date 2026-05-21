"""
DebrisLink — WhatsApp Message Parser
-------------------------------------
Decodes free-text inbound WhatsApp messages into a structured `ParsedCommand`
that downstream business logic can act on.

Supported grammars (case-insensitive, whitespace-tolerant):

    REQUEST  <SITE_ID>
        e.g.  "REQUEST 1"
        → builder asks for a debris pickup at their site

    COMPLETE <TRIP_ID> <WEIGHT_TONS> <PLANT_NAME...>
        e.g.  "COMPLETE 42 12 Dahisar Plant"
              "complete 42 12.75 GreenCycle Processing Yard"
        → driver reports dump at a recycling plant

Anything else returns `CommandType.UNKNOWN` so the webhook can reply with
help text instead of a silent failure.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------
class CommandType(str, enum.Enum):
    REQUEST = "REQUEST"      # Builder requesting a pickup
    COMPLETE = "COMPLETE"    # Driver reporting disposal
    INVALID = "INVALID"      # Recognized verb, malformed arguments
    UNKNOWN = "UNKNOWN"      # Verb not recognized at all


@dataclass
class ParsedCommand:
    command: CommandType
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def is_actionable(self) -> bool:
        return self.command in (CommandType.REQUEST, CommandType.COMPLETE)


# ---------------------------------------------------------------------------
# Compiled regexes — anchored, case-insensitive, whitespace-tolerant
# ---------------------------------------------------------------------------
_REQUEST_RE = re.compile(
    r"^\s*REQUEST\s+(?P<site_id>\d+)\s*$",
    re.IGNORECASE,
)

_COMPLETE_RE = re.compile(
    r"""^\s*
        COMPLETE\s+
        (?P<trip_id>\d+)\s+
        (?P<weight>\d+(?:\.\d+)?)\s+
        (?P<plant>.+?)
        \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def parse_message(text: str) -> ParsedCommand:
    """Translate a raw inbound message into a `ParsedCommand`."""
    if not text or not text.strip():
        return ParsedCommand(CommandType.INVALID, error="Empty message received.")

    # ---- REQUEST -------------------------------------------------------
    if (m := _REQUEST_RE.match(text)) is not None:
        return ParsedCommand(
            command=CommandType.REQUEST,
            data={"site_id": int(m.group("site_id"))},
        )

    # ---- COMPLETE ------------------------------------------------------
    if (m := _COMPLETE_RE.match(text)) is not None:
        weight = float(m.group("weight"))
        plant = m.group("plant").strip()

        if weight <= 0:
            return ParsedCommand(
                CommandType.INVALID,
                error="Weight must be greater than zero.",
            )
        if len(plant) < 2:
            return ParsedCommand(
                CommandType.INVALID,
                error="Plant name is too short.",
            )

        return ParsedCommand(
            command=CommandType.COMPLETE,
            data={
                "trip_id": int(m.group("trip_id")),
                "weight_tons": weight,
                "plant_name": plant,
            },
        )

    # ---- Verb recognized but args malformed → helpful error -----------
    head = text.strip().split(maxsplit=1)[0].upper() if text.strip() else ""
    if head == "REQUEST":
        return ParsedCommand(
            CommandType.INVALID,
            error="Invalid REQUEST format. Use: REQUEST <SITE_ID>",
        )
    if head == "COMPLETE":
        return ParsedCommand(
            CommandType.INVALID,
            error=(
                "Invalid COMPLETE format. "
                "Use: COMPLETE <TRIP_ID> <WEIGHT_TONS> <PLANT_NAME>"
            ),
        )

    # ---- Verb not recognized at all -----------------------------------
    return ParsedCommand(command=CommandType.UNKNOWN)
