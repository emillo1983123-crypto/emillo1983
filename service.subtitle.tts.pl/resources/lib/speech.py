"""ElevenLabs HTTP client producing Kodi-compatible WAV files."""

from __future__ import unicode_literals

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass

from text_normalizer import compress_for_economy, normalize_for_speech


API_BASE = "https://api.elevenlabs.io/v1"
MAX_AUDIO_BYTES = 64 * 1024 * 1024
SAMPLE_RATE = 24000
CONTEXT_LIMIT = 250
MAX_REQUEST_TEXT_CHARS = 9000
MAX_TOTAL_TEXT_CHARS = 18000
SPEED_PERCENT_MIN = 70
SPEED_PERCENT_MAX = 120
SENTENCE_BREAK_RE = re.compile(r"[.!?\u2026](?=\s)", re.UNICODE)
VOICE_PROFILES = {
    "classic": {
        "stability": 0.70,
        "similarity_boost": 0.75,
        "style": 0.0,
        "use_speaker_boost": False,
    },
    "natural": {
        "stability": 0.55,
        "similarity_boost": 0.75,
        "style": 0.05,
        "use_speaker_boost": True,
    },
    "warm": {
        "stability": 0.65,
        "similarity_boost": 0.80,
        "style": 0.10,
        "use_speaker_boost": True,
    },
    "dynamic": {
        "stability": 0.40,
        "similarity_boost": 0.70,
        "style": 0.25,
        "use_speaker_boost": True,
    },
}


def _clamped_speed_percent(value):
    try:
        value = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        value = 95
    return max(SPEED_PERCENT_MIN, min(SPEED_PERCENT_MAX, value))


def _split_text_for_api(text):
    """Split normalized text at a sentence or word boundary without dropping words."""
    remaining = text.strip()
    chunks = []
    while len(remaining) > MAX_REQUEST_TEXT_CHARS:
        sentence_cut = 0
        for match in SENTENCE_BREAK_RE.finditer(remaining, 0, MAX_REQUEST_TEXT_CHARS):
            sentence_cut = match.end()
        word_cut = remaining.rfind(" ", 0, MAX_REQUEST_TEXT_CHARS + 1)
        if sentence_cut >= MAX_REQUEST_TEXT_CHARS // 2:
            cut = sentence_cut
        elif word_cut > 0:
            cut = word_cut
        else:
            cut = MAX_REQUEST_TEXT_CHARS
        chunk = remaining[:cut].strip()
        if not chunk:
            chunk = remaining[:MAX_REQUEST_TEXT_CHARS]
            cut = MAX_REQUEST_TEXT_CHARS
        chunks.append(chunk)
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


class SpeechError(Exception):
    def __init__(self, user_message, status=0, retryable=False):
        super().__init__(user_message)
        self.user_message = user_message
        self.status = status
        self.retryable = retryable


class SpeechCancelled(Exception):
    """Internal cancellation that must not be shown as a playback error."""


@dataclass(frozen=True)
class SpeechResult:
    path: str
    duration: float
    cached: bool


def _error_message(status, body):
    detail = ""
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
        value = payload.get("detail", payload)
        if isinstance(value, dict):
            detail = str(value.get("message") or value.get("status") or value.get("code") or "")
        else:
            detail = str(value)
    except Exception:
        detail = ""
    folded = detail.casefold()
    if status in (401, 403) or "invalid_api_key" in folded or "api key" in folded and "invalid" in folded:
        return "Klucz API ElevenLabs jest nieprawidłowy. Wpisz nowy klucz w ustawieniach dodatku."
    if "exactly 51 characters" in folded or "api_key_length" in folded:
        return "Klucz API ma złą długość. Wklej pełny klucz ElevenLabs bez spacji."
    if status == 429:
        return "ElevenLabs chwilowo ograniczył liczbę zapytań albo skończył się limit konta."
    if status == 422:
        return "ElevenLabs odrzucił głos lub model. Wybierz głos ponownie w menu dodatku."
    if status >= 500:
        return "Usługa ElevenLabs jest chwilowo niedostępna."
    if detail:
        return "ElevenLabs: %s" % detail[:180]
    return "Nie udało się pobrać głosu z ElevenLabs (HTTP %s)." % status


class ElevenLabsClient:
    def __init__(
        self,
        api_key,
        voice_id,
        model_id,
        cache_dir,
        timeout=15,
        speech_speed_percent=95,
        voice_profile="classic",
    ):
        self.api_key = (api_key or "").strip()
        self.voice_id = (voice_id or "").strip()
        self.model_id = (model_id or "eleven_flash_v2_5").strip()
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.speech_speed_percent = _clamped_speed_percent(speech_speed_percent)
        profile = str(voice_profile or "classic").strip().casefold()
        self.voice_profile = profile if profile in VOICE_PROFILES else "classic"

    def _request(self, url, data=None):
        if not self.api_key:
            raise SpeechError("Brak klucza API ElevenLabs. Otwórz ustawienia dodatku.")
        headers = {"xi-api-key": self.api_key, "Accept": "application/json" if data is None else "audio/pcm"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_AUDIO_BYTES + 1)
                if len(body) > MAX_AUDIO_BYTES:
                    raise SpeechError("Odpowiedź audio z ElevenLabs jest zbyt duża.")
                return body
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read(16384)
            except Exception:
                body = b""
            raise SpeechError(_error_message(exc.code, body), exc.code, exc.code == 429 or exc.code >= 500)
        except (urllib.error.URLError, OSError, TimeoutError):
            raise SpeechError("Brak połączenia z ElevenLabs. Sprawdź internet w telewizorze.", retryable=True)

    def list_voices(self):
        body = self._request(API_BASE + "/voices")
        try:
            payload = json.loads(body.decode("utf-8"))
            voices = []
            for item in payload.get("voices", []):
                voice_id = str(item.get("voice_id", "")).strip()
                name = str(item.get("name", voice_id)).strip()
                category = str(item.get("category", "")).strip()
                if voice_id:
                    voices.append((name, voice_id, category))
            return sorted(voices, key=lambda value: value[0].casefold())
        except (ValueError, TypeError, AttributeError):
            raise SpeechError("ElevenLabs zwrócił nieprawidłową listę głosów.")

    def _speech_options(self):
        voice_settings = dict(VOICE_PROFILES[self.voice_profile])
        voice_settings["speed"] = self.speech_speed_percent / 100.0
        options = {
            "voice_settings": voice_settings,
            "apply_text_normalization": "auto",
        }
        if self.model_id in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
            options["language_code"] = "pl"
        return options

    def _cache_path(self, text, previous_text="", next_text=""):
        signature = {
            "voice_id": self.voice_id,
            "model_id": self.model_id,
            "output_format": "pcm_24000",
            "text": text,
            "previous_text": previous_text,
            "next_text": next_text,
            "voice_profile": self.voice_profile,
            "speech_speed_percent": self.speech_speed_percent,
            "options": self._speech_options(),
        }
        material = json.dumps(signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, digest + ".wav")

    def synthesize(
        self,
        text,
        previous_text="",
        next_text="",
        economy_mode=False,
        cancelled=None,
    ):
        text = compress_for_economy(text) if economy_mode else normalize_for_speech(text)
        if economy_mode:
            previous_text = ""
            next_text = ""
        else:
            previous_text = normalize_for_speech(previous_text)[-CONTEXT_LIMIT:]
            next_text = normalize_for_speech(next_text)[:CONTEXT_LIMIT]
        if not text:
            raise SpeechError("Brak tekstu do przeczytania.")
        if len(text) > MAX_TOTAL_TEXT_CHARS:
            raise SpeechError(
                "Pojedyncza kwestia napisów jest nienaturalnie długa i została pominięta dla ochrony limitu API."
            )
        if not self.voice_id:
            raise SpeechError("Brak identyfikatora głosu. Wybierz głos w menu dodatku.")
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
            path = self._cache_path(text, previous_text, next_text)
            if os.path.isfile(path) and os.path.getsize(path) > 44:
                frames = max(0, os.path.getsize(path) - 44) // 2
                try:
                    os.utime(path, None)
                except OSError:
                    pass
                return SpeechResult(path, frames / float(SAMPLE_RATE), True)
        else:
            raise SpeechError("Brak katalogu na pliki głosu.")

        url = "%s/text-to-speech/%s?output_format=pcm_24000" % (
            API_BASE,
            urllib.parse.quote(self.voice_id, safe=""),
        )
        chunks = _split_text_for_api(text)
        temporary = path + ".part"
        pcm_bytes = 0
        try:
            with wave.open(temporary, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                for index, chunk in enumerate(chunks):
                    if callable(cancelled) and cancelled():
                        raise SpeechCancelled()
                    request_data = {"text": chunk, "model_id": self.model_id}
                    request_data.update(self._speech_options())
                    chunk_previous = previous_text if index == 0 else chunks[index - 1][-CONTEXT_LIMIT:]
                    chunk_next = next_text if index == len(chunks) - 1 else chunks[index + 1][:CONTEXT_LIMIT]
                    if chunk_previous:
                        request_data["previous_text"] = chunk_previous
                    if chunk_next:
                        request_data["next_text"] = chunk_next
                    payload = json.dumps(
                        request_data,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    pcm = self._request(url, payload)
                    if callable(cancelled) and cancelled():
                        raise SpeechCancelled()
                    if not pcm or len(pcm) % 2:
                        raise SpeechError("ElevenLabs zwrócił uszkodzony dźwięk.")
                    output.writeframesraw(pcm)
                    pcm_bytes += len(pcm)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
        self.prune_cache(128)
        return SpeechResult(path, (pcm_bytes // 2) / float(SAMPLE_RATE), False)

    def prune_cache(self, keep):
        try:
            files = [
                os.path.join(self.cache_dir, name)
                for name in os.listdir(self.cache_dir)
                if name.endswith(".wav")
            ]
            files.sort(key=lambda path: os.path.getmtime(path), reverse=True)
            for path in files[keep:]:
                try:
                    os.remove(path)
                except OSError:
                    pass
        except OSError:
            pass
