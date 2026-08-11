"""Pure helpers for ElevenLabs quota display and subtitle cost estimates.

The ElevenLabs subscription endpoint still exposes the historical
``character_*`` field names even though the product UI calls the units
credits.  These helpers accept both the current fields and likely future
``credit_*`` equivalents so the Kodi UI never has to guess which schema it
received.

Film estimates are deliberately marked approximate.  They describe the text
that this add-on plans to submit, not an invoice: cached audio may lower the
real usage, while a Voice Library custom rate or enterprise agreement may
raise or otherwise change it.
"""

from __future__ import unicode_literals

import json
import math
import re
from dataclasses import dataclass
from typing import Optional


FLASH_TURBO_CREDITS_PER_CHARACTER = 0.5
DEFAULT_CREDITS_PER_CHARACTER = 1.0
ELEVENLABS_DEVELOPERS_URL = "https://elevenlabs.io/app/developers"
ELEVENLABS_SUBSCRIPTION_URL = "https://elevenlabs.io/app/subscription"
ELEVENLABS_API_PRICING_URL = "https://elevenlabs.io/pricing/api"
_INTEGER_RE = re.compile(r"^[+]?[0-9]+$")


class UsageDataError(ValueError):
    """Raised when a subscription response cannot be interpreted safely."""


@dataclass(frozen=True)
class SubscriptionUsage:
    """Relevant, non-sensitive fields from ``GET /v1/user/subscription``."""

    used: int
    limit: int
    remaining: int
    over_limit: int
    tier: str = ""
    status: str = ""
    reset_unix: Optional[int] = None
    overage_amount: str = "0"
    overage_currency: str = ""
    max_credit_limit_extension: object = 0
    source_units: str = "credits"

    @property
    def used_percent(self):
        if self.limit <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.used * 100.0) / self.limit))

    def remaining_after(self, estimated_credits):
        """Return included quota left after a conservative rounded estimate."""

        try:
            value = float(estimated_credits)
        except (TypeError, ValueError, OverflowError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        return max(0, self.remaining - int(math.ceil(max(0.0, value))))


@dataclass(frozen=True)
class FilmUsageEstimate:
    """Approximate usage for one complete subtitle track."""

    text_characters: int
    estimated_credits: float
    rounded_credits: int
    cue_count: int
    spoken_cue_count: int
    base_credits_per_character: float
    voice_credit_multiplier: float
    voice_multiplier_known: bool
    model_id: str
    approximate: bool = True


def _mapping(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            raise UsageDataError("ElevenLabs returned invalid subscription JSON.")
        if isinstance(decoded, dict):
            return decoded
    raise UsageDataError("ElevenLabs returned an invalid subscription response.")


def _first_value(payload, names):
    for name in names:
        if name in payload and payload.get(name) is not None:
            return payload.get(name), name
    return None, ""


def _nonnegative_integer(value, field_name, required=False):
    if value is None:
        if required:
            raise UsageDataError("Missing ElevenLabs field: %s." % field_name)
        return None
    if isinstance(value, bool):
        raise UsageDataError("Invalid ElevenLabs field: %s." % field_name)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float) and math.isfinite(value) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and _INTEGER_RE.match(value.strip()):
        parsed = int(value.strip())
    else:
        raise UsageDataError("Invalid ElevenLabs field: %s." % field_name)
    if parsed < 0:
        raise UsageDataError("Invalid ElevenLabs field: %s." % field_name)
    return parsed


def _optional_text(value):
    return "" if value is None else str(value).strip()


def parse_subscription_usage(response):
    """Parse the official subscription response without retaining the API key.

    ``GET /v1/user/subscription`` returns the subscription object directly.
    The function also accepts the nested object returned by ``GET /v1/user``
    because both official endpoints have existed for a long time.
    """

    payload = _mapping(response)
    if isinstance(payload.get("subscription"), dict):
        payload = payload["subscription"]

    used_value, used_name = _first_value(
        payload,
        ("credit_count", "credits_used", "credit_used", "character_count"),
    )
    limit_value, limit_name = _first_value(
        payload,
        ("credit_limit", "credits_limit", "character_limit"),
    )
    used = _nonnegative_integer(used_value, used_name or "credit_count", required=True)
    limit = _nonnegative_integer(limit_value, limit_name or "credit_limit", required=True)

    remaining_value, remaining_name = _first_value(
        payload,
        ("credit_remaining", "credits_remaining", "character_remaining"),
    )
    if remaining_name:
        remaining = _nonnegative_integer(remaining_value, remaining_name, required=True)
        # Do not let an inconsistent response claim more included quota than
        # its own limit permits.
        remaining = min(remaining, max(0, limit - used))
    else:
        remaining = max(0, limit - used)

    reset_value, reset_name = _first_value(
        payload,
        (
            "next_credit_count_reset_unix",
            "next_credits_reset_unix",
            "next_character_count_reset_unix",
        ),
    )
    reset_unix = _nonnegative_integer(reset_value, reset_name, required=False)

    overage = payload.get("current_overage")
    if not isinstance(overage, dict):
        overage = payload.get("overage") if isinstance(payload.get("overage"), dict) else {}
    extension = payload.get(
        "max_credit_limit_extension",
        payload.get("max_character_limit_extension", 0),
    )
    if extension != "unlimited":
        try:
            extension = _nonnegative_integer(extension, "max_credit_limit_extension", required=False)
        except UsageDataError:
            extension = 0
        if extension is None:
            extension = 0

    source_units = "credits" if used_name.startswith("credit") else "characters"
    return SubscriptionUsage(
        used=used,
        limit=limit,
        remaining=remaining,
        over_limit=max(0, used - limit),
        tier=_optional_text(payload.get("tier")),
        status=_optional_text(payload.get("status")),
        reset_unix=reset_unix,
        overage_amount=_optional_text(overage.get("amount")) or "0",
        overage_currency=_optional_text(overage.get("currency") or payload.get("currency")),
        max_credit_limit_extension=extension,
        source_units=source_units,
    )


def base_credits_per_character(model_id):
    """Return the current self-serve base rate for an ElevenLabs TTS model.

    ElevenLabs currently gives Flash and Turbo twice as many generated text
    characters per included credit as Multilingual models.  Unknown/future
    models use the conservative 1-credit-per-character estimate.
    """

    folded = str(model_id or "").strip().casefold()
    if "flash" in folded or "turbo" in folded:
        return FLASH_TURBO_CREDITS_PER_CHARACTER
    return DEFAULT_CREDITS_PER_CHARACTER


def estimate_film_usage(cues, prepare_text, model_id, voice_credit_multiplier=None):
    """Estimate full-track credits after the add-on's filter/normalizer.

    ``prepare_text`` must be the same callback used before speech generation
    (for example family filtering plus normal/economy normalization).  Empty
    results are intentionally excluded.  Every subtitle cue is counted once;
    cached WAV files can make the real usage lower.
    """

    if not callable(prepare_text):
        raise TypeError("prepare_text must be callable")

    if voice_credit_multiplier is None:
        voice_multiplier = 1.0
        multiplier_known = False
    else:
        try:
            voice_multiplier = float(voice_credit_multiplier)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("voice_credit_multiplier must be a positive number")
        if not math.isfinite(voice_multiplier) or voice_multiplier <= 0:
            raise ValueError("voice_credit_multiplier must be a positive number")
        multiplier_known = True

    cue_count = 0
    spoken_cue_count = 0
    text_characters = 0
    for cue in cues or ():
        cue_count += 1
        source_text = getattr(cue, "text", cue)
        prepared = prepare_text("" if source_text is None else str(source_text))
        prepared = "" if prepared is None else str(prepared).strip()
        if not prepared:
            continue
        spoken_cue_count += 1
        text_characters += len(prepared)

    base_rate = base_credits_per_character(model_id)
    estimated_credits = text_characters * base_rate * voice_multiplier
    return FilmUsageEstimate(
        text_characters=text_characters,
        estimated_credits=estimated_credits,
        rounded_credits=int(math.ceil(estimated_credits)),
        cue_count=cue_count,
        spoken_cue_count=spoken_cue_count,
        base_credits_per_character=base_rate,
        voice_credit_multiplier=voice_multiplier,
        voice_multiplier_known=multiplier_known,
        model_id=str(model_id or ""),
    )
