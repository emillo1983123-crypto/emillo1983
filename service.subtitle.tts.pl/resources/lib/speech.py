"""ElevenLabs HTTP client producing Kodi-compatible WAV files."""

from __future__ import unicode_literals

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from dataclasses import dataclass


API_BASE = "https://api.elevenlabs.io/v1"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
SAMPLE_RATE = 24000


class SpeechError(Exception):
    def __init__(self, user_message, status=0, retryable=False):
        super().__init__(user_message)
        self.user_message = user_message
        self.status = status
        self.retryable = retryable


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
    def __init__(self, api_key, voice_id, model_id, cache_dir, timeout=15):
        self.api_key = (api_key or "").strip()
        self.voice_id = (voice_id or "").strip()
        self.model_id = (model_id or "eleven_flash_v2_5").strip()
        self.cache_dir = cache_dir
        self.timeout = timeout

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

    def _cache_path(self, text):
        digest = hashlib.sha256(
            (self.voice_id + "\0" + self.model_id + "\0pcm_24000\0" + text).encode("utf-8")
        ).hexdigest()
        return os.path.join(self.cache_dir, digest + ".wav")

    def synthesize(self, text):
        text = " ".join((text or "").split())[:500]
        if not text:
            raise SpeechError("Brak tekstu do przeczytania.")
        if not self.voice_id:
            raise SpeechError("Brak identyfikatora głosu. Wybierz głos w menu dodatku.")
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
            path = self._cache_path(text)
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
        payload = json.dumps(
            {"text": text, "model_id": self.model_id},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        pcm = self._request(url, payload)
        if not pcm or len(pcm) % 2:
            raise SpeechError("ElevenLabs zwrócił uszkodzony dźwięk.")
        temporary = path + ".part"
        try:
            with wave.open(temporary, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(SAMPLE_RATE)
                output.writeframes(pcm)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass
        self.prune_cache(128)
        return SpeechResult(path, (len(pcm) // 2) / float(SAMPLE_RATE), False)

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

