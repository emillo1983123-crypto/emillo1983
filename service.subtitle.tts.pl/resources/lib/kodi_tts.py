"""Free speech hand-off for the local KWPJ Voice Bridge.

Kodi itself cannot invoke Android's TextToSpeech API. The KWPJ Voice OS
companion exposes a loopback-only bridge on the same television, using the
Polish voice installed in Android. Nothing is sent to a cloud service.

The client only prepares a result in the worker thread.  ``deliver`` is called
later by the Kodi main service thread, which avoids invoking Kodi GUI APIs from
a background thread.
"""

from __future__ import unicode_literals

import json
try:
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover
    from urllib2 import Request, urlopen

from speech import SpeechCancelled, SpeechError, SpeechResult
from text_normalizer import compress_for_economy, normalize_for_speech


BRIDGE_BASE_URL = "http://127.0.0.1:8765"
BRIDGE_HEALTH_URL = BRIDGE_BASE_URL + "/health"
BRIDGE_SPEAK_URL = BRIDGE_BASE_URL + "/speak"


def _clamp_speed(value):
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        value = 95
    return max(70, min(120, value))


def _estimated_duration(text, speed_percent):
    """Conservative queue duration for a normal Polish narration tempo."""

    words = max(1, len((text or "").split()))
    # 2.45 words/s at 100% is calm narration, with a small phrase margin.
    words_per_second = 2.45 * (_clamp_speed(speed_percent) / 100.0)
    return max(0.7, (words / words_per_second) + 0.25)


def _read_json(response):
    try:
        return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def is_available(opener=None):
    """Return whether KWPJ Voice OS is running on this television."""

    if opener is None:
        opener = urlopen
    try:
        response = opener(BRIDGE_HEALTH_URL, timeout=0.35)
        return bool(_read_json(response).get("ready"))
    except Exception:
        return False


def _bridge_request(text, interrupt=False):
    if not isinstance(text, str) or not text.strip():
        raise SpeechError("Brak tekstu do przeczytania.")
    payload = json.dumps(
        {"text": text, "interrupt": bool(interrupt)},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return Request(
        BRIDGE_SPEAK_URL,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )


def deliver(text, interrupt=False, opener=None):
    """Hand a subtitle line to KWPJ Voice OS on the same television."""

    if opener is None:
        opener = urlopen
    request = _bridge_request(text, interrupt)
    try:
        response = opener(request, timeout=0.75)
        if not bool(_read_json(response).get("ready")):
            raise SpeechError("Polski głos KWPJ nie jest jeszcze gotowy.")
    except Exception as exc:
        if isinstance(exc, SpeechError):
            raise
        raise SpeechError(
            "Nie widzę KWPJ Voice OS. Otwórz aplikację KWPJ na telewizorze "
            "i wybierz Włącz głos dla Kodi."
        ) from exc
    return True


def stop():
    """The bridge holds only short queued turns; Kodi can change playback safely."""

    return True


class KodiTtsClient:
    """Speech-provider adapter with no network, credentials, or audio files."""

    def __init__(
        self,
        _api_key="",
        _voice_id="",
        _model_id="",
        _cache_dir="",
        timeout=15,
        speech_speed_percent=95,
        voice_profile="classic",
    ):
        del timeout, voice_profile
        self.speech_speed_percent = _clamp_speed(speech_speed_percent)

    def list_voices(self):
        raise SpeechError(
            "Darmowy silnik wybiera głos w ustawieniach urządzenia lub usługi TTS."
        )

    def synthesize(
        self,
        text,
        previous_text="",
        next_text="",
        economy_mode=False,
        cancelled=None,
    ):
        del previous_text, next_text
        if callable(cancelled) and cancelled():
            raise SpeechCancelled()
        spoken = compress_for_economy(text) if economy_mode else normalize_for_speech(text)
        if not spoken:
            raise SpeechError("Brak tekstu do przeczytania.")
        return SpeechResult(
            path="",
            duration=_estimated_duration(spoken, self.speech_speed_percent),
            cached=False,
            delivery="kodi_tts",
            text=spoken,
        )
