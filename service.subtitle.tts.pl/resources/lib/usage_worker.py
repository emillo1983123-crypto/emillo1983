"""Non-blocking ElevenLabs quota lookup for the playback service.

Only this worker performs the network request.  The Kodi polling thread gives
it an immutable snapshot of the selected subtitle track and later consumes a
small result object.  API keys are deliberately excluded from dataclass
representations and from every error shown or logged by this module.
"""

from __future__ import unicode_literals

import json
import queue
import socket
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from provider_factory import ProviderConfigurationError, usage_fetcher_for
from usage import (
    SubscriptionUsage,
    UsageDataError,
    estimate_film_usage,
    parse_subscription_usage,
)


SUBSCRIPTION_API_URL = "https://api.elevenlabs.io/v1/user/subscription"
MAX_RESPONSE_BYTES = 128 * 1024


class UsageCancelled(Exception):
    """Internal signal used when playback/generation has changed."""


class UsageFetchError(Exception):
    """A safe, already-localized account lookup failure."""

    def __init__(self, kind, user_message):
        super().__init__(user_message)
        self.kind = str(kind or "unknown")
        self.user_message = str(user_message or "Nie udało się pobrać limitu ElevenLabs.")


@dataclass(frozen=True)
class UsageJob:
    generation: int
    source_key: object
    api_key: str = field(repr=False)
    model_id: str = ""
    cues: tuple = ()
    prepare_text: object = field(default=None, repr=False, compare=False)
    provider_id: str = ""


@dataclass(frozen=True)
class UsageResult:
    generation: int
    source_key: object
    estimate: object = None
    subscription: object = None
    error_kind: str = ""
    user_message: str = ""


def _safe_error_payload(error):
    try:
        body = error.read(MAX_RESPONSE_BYTES + 1)
    except Exception:
        return {}
    if not body or len(body) > MAX_RESPONSE_BYTES:
        return {}
    try:
        text = body if isinstance(body, str) else body.decode("utf-8", "replace")
        payload = json.loads(text)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_markers(payload):
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        values = (
            detail.get("status"),
            detail.get("code"),
            detail.get("type"),
            detail.get("message"),
        )
    else:
        values = (detail, payload.get("status") if isinstance(payload, dict) else None)
    return " ".join(str(value or "") for value in values).casefold()


def _http_error(http_error):
    status = int(getattr(http_error, "code", 0) or 0)
    markers = _error_markers(_safe_error_payload(http_error))
    if (
        "user_read" in markers
        or "user read" in markers
        or "missing_permissions" in markers
        or "missing permission" in markers
    ):
        return UsageFetchError(
            "missing_user_read",
            "Limit ElevenLabs: klucz nie ma uprawnienia User: Read (user_read). Lektor nadal działa.",
        )
    if status in (401, 403):
        return UsageFetchError(
            "account_access_denied",
            "Limit ElevenLabs jest niedostępny dla tego klucza. Lektor nadal działa.",
        )
    if status == 429:
        return UsageFetchError(
            "rate_limited",
            "ElevenLabs chwilowo ograniczył odczyt limitu. Lektor nadal działa.",
        )
    return UsageFetchError(
        "http_error",
        "Nie udało się pobrać limitu ElevenLabs. Lektor nadal działa.",
    )


def fetch_subscription(api_key, cancelled=None, timeout=8.0):
    """Fetch and parse ``GET /v1/user/subscription`` without exposing a key."""

    cancelled = cancelled if callable(cancelled) else (lambda: False)
    if cancelled():
        raise UsageCancelled()
    key = str(api_key or "").strip()
    if not key:
        raise UsageFetchError(
            "missing_api_key",
            "Nie można pokazać limitu: brak klucza API ElevenLabs.",
        )
    request = urllib.request.Request(
        SUBSCRIPTION_API_URL,
        headers={"xi-api-key": key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc)
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError):
        raise UsageFetchError(
            "network_error",
            "Nie udało się pobrać limitu ElevenLabs. Lektor nadal działa.",
        )
    if cancelled():
        raise UsageCancelled()
    if len(body) > MAX_RESPONSE_BYTES:
        raise UsageFetchError(
            "invalid_response",
            "ElevenLabs zwrócił nieprawidłowy stan konta. Lektor nadal działa.",
        )
    try:
        return parse_subscription_usage(body)
    except UsageDataError:
        raise UsageFetchError(
            "invalid_response",
            "ElevenLabs zwrócił nieprawidłowy stan konta. Lektor nadal działa.",
        )


class UsageWorker(threading.Thread):
    """Estimate one track and fetch account quota away from Kodi's main loop."""

    def __init__(self, fetcher=None, estimator=None):
        super().__init__(name="KodiLektorUsage", daemon=True)
        self.fetcher = fetcher or fetch_subscription
        self.estimator = estimator or estimate_film_usage
        self.jobs = queue.Queue(maxsize=2)
        self.results = queue.Queue()
        self.stopping = threading.Event()
        self.latest_generation = 0
        self._attempts = set()
        self._lock = threading.Lock()

    @staticmethod
    def _identity(job):
        try:
            hash(job.source_key)
            source_identity = job.source_key
        except TypeError:
            source_identity = repr(job.source_key)
        return int(job.generation), source_identity

    def submit(self, job):
        identity = self._identity(job)
        with self._lock:
            if identity in self._attempts or self.stopping.is_set():
                return False
            self._attempts.add(identity)
            self.latest_generation = int(job.generation)
        try:
            self.jobs.put_nowait(job)
            return True
        except queue.Full:
            # The identity intentionally stays recorded: repeatedly polling
            # Kodi must not turn a full queue into an HTTP retry storm.
            return False

    def invalidate(self, generation):
        generation = int(generation)
        with self._lock:
            self.latest_generation = generation
            self._attempts = {
                identity for identity in self._attempts if identity[0] == generation
            }
        while True:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                break

    def stop(self):
        self.stopping.set()
        try:
            self.jobs.put_nowait(None)
        except queue.Full:
            pass

    def _cancelled(self, job):
        return self.stopping.is_set() or int(job.generation) != self.latest_generation

    def run(self):
        while not self.stopping.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None or self._cancelled(job):
                continue
            try:
                estimate = self.estimator(
                    job.cues,
                    job.prepare_text,
                    job.model_id,
                )
                if self._cancelled(job):
                    continue
                fetcher = usage_fetcher_for(job.provider_id, self.fetcher)
                subscription = fetcher(
                    job.api_key,
                    lambda: self._cancelled(job),
                )
                if not isinstance(subscription, SubscriptionUsage):
                    subscription = parse_subscription_usage(subscription)
                if self._cancelled(job):
                    continue
                self.results.put(
                    UsageResult(
                        job.generation,
                        job.source_key,
                        estimate=estimate,
                        subscription=subscription,
                    )
                )
            except UsageCancelled:
                continue
            except ProviderConfigurationError as exc:
                if not self._cancelled(job):
                    self.results.put(
                        UsageResult(
                            job.generation,
                            job.source_key,
                            error_kind="unknown_provider",
                            user_message=exc.user_message,
                        )
                    )
            except UsageFetchError as exc:
                if not self._cancelled(job):
                    self.results.put(
                        UsageResult(
                            job.generation,
                            job.source_key,
                            error_kind=exc.kind,
                            user_message=exc.user_message,
                        )
                    )
            except (UsageDataError, TypeError, ValueError):
                if not self._cancelled(job):
                    self.results.put(
                        UsageResult(
                            job.generation,
                            job.source_key,
                            error_kind="invalid_response",
                            user_message=(
                                "Nie udało się obliczyć limitu ElevenLabs. "
                                "Lektor nadal działa."
                            ),
                        )
                    )
            except Exception:
                if not self._cancelled(job):
                    self.results.put(
                        UsageResult(
                            job.generation,
                            job.source_key,
                            error_kind="unexpected_error",
                            user_message=(
                                "Nie udało się pobrać limitu ElevenLabs. "
                                "Lektor nadal działa."
                            ),
                        )
                    )
