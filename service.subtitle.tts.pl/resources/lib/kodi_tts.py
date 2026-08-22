"""Free speech hand-off for a compatible Kodi device TTS service.

Kodi itself does not include a cross-platform API that returns a WAV from the
operating system's speech engine.  The small ``service.xbmc.tts`` protocol is
the established Kodi add-on contract for that job: the receiving service
chooses the voice installed on the device and plays it through the normal
audio output.  This module deliberately does *not* download a voice, send text
to a cloud service, or silently fall back to a paid provider.

The client only prepares a result in the worker thread.  ``deliver`` is called
later by the Kodi main service thread, which avoids invoking Kodi GUI APIs from
a background thread.
"""

from __future__ import unicode_literals

import json

from speech import SpeechCancelled, SpeechError, SpeechResult
from text_normalizer import compress_for_economy, normalize_for_speech


TTS_SERVICE_ADDON_ID = "service.xbmc.tts"
TTS_NOTIFICATION_SENDER = "service.xbmc.tts"
TTS_NOTIFICATION_METHOD = "SAY"


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


def is_available(condition=None):
    """Check only whether a compatible Kodi TTS service is installed.

    The result cannot certify a particular voice; that belongs to the device
    and the service's own settings.  It is nevertheless enough to prevent the
    silent no-op that previously looked like a working narrator.
    """

    if condition is None:
        try:
            import xbmc

            condition = xbmc.getCondVisibility
        except Exception:
            return False
    try:
        return bool(condition("System.HasAddon(%s)" % TTS_SERVICE_ADDON_ID))
    except Exception:
        return False


def _notification_command(text, interrupt=False):
    if not isinstance(text, str) or not text.strip():
        raise SpeechError("Brak tekstu do przeczytania.")
    payload = json.dumps(
        {"text": text, "interrupt": bool(interrupt)},
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "NotifyAll(%s,%s,%s)" % (
        TTS_NOTIFICATION_SENDER,
        TTS_NOTIFICATION_METHOD,
        payload,
    )


def deliver(text, interrupt=False, execute_builtin=None):
    """Ask the installed TTS service to say ``text`` on the device."""

    if execute_builtin is None:
        try:
            import xbmc

            execute_builtin = xbmc.executebuiltin
        except Exception as exc:
            raise SpeechError("Kodi nie udostępnia darmowego silnika mowy.") from exc
    if not callable(execute_builtin):
        raise SpeechError("Kodi nie udostępnia darmowego silnika mowy.")
    command = _notification_command(text, interrupt)
    try:
        execute_builtin(command)
    except Exception as exc:
        raise SpeechError("Kodi nie przekazał tekstu do darmowego silnika mowy.") from exc
    return command


def stop(execute_builtin=None):
    """Cancel queued device speech when a film is paused, changed, or sought."""

    if execute_builtin is None:
        try:
            import xbmc

            execute_builtin = xbmc.executebuiltin
        except Exception:
            return False
    if not callable(execute_builtin):
        return False
    try:
        execute_builtin("NotifyAll(%s,STOP)" % TTS_NOTIFICATION_SENDER)
    except Exception:
        return False
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
