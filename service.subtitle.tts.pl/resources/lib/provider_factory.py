"""Provider boundary for local-free and legacy speech providers.

The default is intentionally the installed Kodi/device TTS service.  It never
uses a key or makes a network call.  ``elevenlabs`` remains an explicit legacy
choice only, so a pre-existing user can migrate without an unexpected paid
request.  Unknown values never select a provider.
"""

from __future__ import unicode_literals

from kodi_tts import KodiTtsClient
from speech import ElevenLabsClient, SpeechError


DEFAULT_PROVIDER_ID = "kodi_tts"
PROVIDER_SETTING_ID = "speech_provider"
KODI_TTS_PROVIDER_ID = "kodi_tts"
ELEVENLABS_PROVIDER_ID = "elevenlabs"


class ProviderConfigurationError(SpeechError):
    """A safe provider-selection failure suitable for Kodi notifications."""


def configured_provider_id(settings):
    """Choose the free local provider and replace a retained legacy value.

    Kodi preserves add-on settings across an update.  A device which used an
    older ElevenLabs edition must therefore be migrated deliberately instead
    of continuing a paid request in the background.
    """

    try:
        settings.setString(PROVIDER_SETTING_ID, DEFAULT_PROVIDER_ID)
    except Exception:
        # A hot update may briefly expose the older settings schema.  The
        # in-memory selection remains local-free either way.
        pass
    return DEFAULT_PROVIDER_ID


def resolve_provider_id(provider_id=None):
    """Resolve missing values to the free local provider safely."""

    if provider_id is None:
        value = ""
    elif isinstance(provider_id, str):
        value = provider_id.strip().casefold()
    else:
        raise ProviderConfigurationError(
            "Wybrano nieobsługiwanego dostawcę głosu. Otwórz ustawienia dodatku."
        )
    if not value:
        return DEFAULT_PROVIDER_ID
    if value in (KODI_TTS_PROVIDER_ID, ELEVENLABS_PROVIDER_ID):
        return value
    raise ProviderConfigurationError(
        "Wybrano nieobsługiwanego dostawcę głosu. Otwórz ustawienia dodatku."
    )


def create_speech_provider(provider_id=None, *args, **kwargs):
    """Create a provider without cross-provider fallback."""

    # Resolve before constructing anything.  An unknown explicit value must not
    # instantiate a provider client or trigger any provider-side operation.
    resolved = resolve_provider_id(provider_id)
    if resolved == KODI_TTS_PROVIDER_ID:
        return KodiTtsClient(*args, **kwargs)
    return ElevenLabsClient(*args, **kwargs)


def usage_fetcher_for(provider_id, elevenlabs_fetcher):
    """Select the quota fetcher, rejecting unknown providers before a request."""

    if resolve_provider_id(provider_id) != ELEVENLABS_PROVIDER_ID:
        raise ProviderConfigurationError(
            "Darmowy głos urządzenia nie ma kredytów ani licznika użycia."
        )
    return elevenlabs_fetcher
