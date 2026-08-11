"""Safe, opt-in helpers for Kodi's official subtitle search window.

This module deliberately has no dependency on ``xbmc``.  Kodi-facing functions
(``xbmc.executeJSONRPC`` and ``xbmc.executebuiltin``) have to be passed in by the
caller.  Importing the module never reads or changes Kodi settings.
"""

from __future__ import unicode_literals

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass


ACTIVATE_SUBTITLE_SEARCH = "ActivateWindow(subtitlesearch)"
ACTIVATE_ADDON_BROWSER = "ActivateWindow(addonbrowser)"
OPENSUBTITLES_COM_ADDON_ID = "service.subtitles.opensubtitles-com"
DEFAULT_SERVICE_SETTINGS = {
    "movie": "subtitles.movie",
    "tv": "subtitles.tv",
}
TEXT_SUBTITLE_EXTENSIONS = (".srt", ".vtt", ".ass", ".ssa", ".sub")


class JsonRpcError(RuntimeError):
    """Raised when Kodi rejects or cannot decode a JSON-RPC request."""


@dataclass(frozen=True)
class AutoSubtitleResult:
    """Outcome returned by :class:`AutoSubtitleSearch` for every decision."""

    status: str
    message: str
    fingerprint: str = ""
    service: str = ""
    retry_after: float = 0.0

    @property
    def opened(self):
        return self.status == "opened"


def _jsonrpc_response(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise JsonRpcError("Kodi zwrócił niepoprawny JSON: %s" % exc)
    if not isinstance(value, dict):
        raise JsonRpcError("Kodi zwrócił nieobsługiwaną odpowiedź JSON-RPC")
    if value.get("error"):
        raise JsonRpcError("Błąd Kodi JSON-RPC: %s" % value["error"])
    if "result" not in value:
        raise JsonRpcError("W odpowiedzi Kodi JSON-RPC brakuje pola result")
    return value["result"]


def jsonrpc_call(jsonrpc, method, params=None):
    """Call an injected ``xbmc.executeJSONRPC``-compatible function."""

    if not callable(jsonrpc):
        raise TypeError("jsonrpc musi być funkcją")
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
    }
    if params is not None:
        request["params"] = params
    try:
        raw = jsonrpc(json.dumps(request, ensure_ascii=False))
    except Exception as exc:
        raise JsonRpcError("Nie udało się wywołać Kodi JSON-RPC: %s" % exc)
    return _jsonrpc_response(raw)


def get_setting_value(jsonrpc, setting, default=None):
    """Read one Kodi setting without changing global configuration."""

    result = jsonrpc_call(
        jsonrpc,
        "Settings.GetSettingValue",
        {"setting": setting},
    )
    if not isinstance(result, dict):
        raise JsonRpcError("Niepoprawna odpowiedź Settings.GetSettingValue")
    return result.get("value", default)


def set_setting_value(jsonrpc, setting, value):
    """Explicitly change one Kodi setting and validate the response."""

    result = jsonrpc_call(
        jsonrpc,
        "Settings.SetSettingValue",
        {"setting": setting, "value": value},
    )
    if result is not True:
        raise JsonRpcError("Kodi nie potwierdził zmiany ustawienia %s" % setting)
    return True


def apply_setting_changes(jsonrpc, changes):
    """Apply a caller-approved mapping of Kodi setting changes."""

    if not isinstance(changes, dict):
        raise TypeError("changes musi być słownikiem")
    for setting, value in changes.items():
        set_setting_value(jsonrpc, setting, value)
    return dict(changes)


def normalize_media_kind(media_kind):
    value = str(media_kind or "").strip().casefold()
    if value in ("movie", "movies", "film"):
        return "movie"
    if value in ("tv", "episode", "episodes", "tvshow", "series", "serial"):
        return "tv"
    raise ValueError("Nieznany typ materiału: %s" % (media_kind or "pusty"))


def default_service_setting(media_kind):
    """Return the Kodi setting ID used for a movie or TV episode."""

    return DEFAULT_SERVICE_SETTINGS[normalize_media_kind(media_kind)]


def get_default_subtitle_service(jsonrpc, media_kind):
    """Read the configured default subtitle service for the media kind."""

    value = get_setting_value(jsonrpc, default_service_setting(media_kind), "")
    service = str(value or "").strip()
    if service.casefold() in ("none", "null"):
        return ""
    return service


def read_subtitle_configuration(jsonrpc, media_kind):
    """Read relevant subtitle settings; this function is strictly read-only."""

    service_setting = default_service_setting(media_kind)
    return {
        "service_setting": service_setting,
        "service": str(get_setting_value(jsonrpc, service_setting, "") or "").strip(),
        "languages": get_setting_value(jsonrpc, "subtitles.languages", []),
        "downloadfirst": bool(
            get_setting_value(jsonrpc, "subtitles.downloadfirst", False)
        ),
    }


def ensure_polish_language(current_languages=None):
    """Return a new language list with Polish first, preserving other values."""

    if current_languages is None:
        values = []
    elif isinstance(current_languages, str):
        text = current_languages.strip()
        separator = "|" if "|" in text else "," if "," in text else None
        values = text.split(separator) if separator else ([text] if text else [])
    else:
        try:
            values = list(current_languages)
        except TypeError:
            values = []

    result = ["Polish"]
    seen = {"polish"}
    for value in values:
        language = str(value or "").strip()
        folded = language.casefold()
        if language and folded not in seen:
            result.append(language)
            seen.add(folded)
    return result


def build_polish_auto_download_changes(current_languages=None):
    """Build, but do not apply, safe Polish auto-download setting changes."""

    return {
        "subtitles.languages": ensure_polish_language(current_languages),
        "subtitles.downloadfirst": True,
    }


def build_subtitle_provider_changes(addon_id, current_languages=None):
    """Build the explicit Kodi changes for one preferred subtitle provider."""

    addon_id = str(addon_id or "").strip()
    if not addon_id:
        raise ValueError("Brak identyfikatora dostawcy napisów")
    changes = build_polish_auto_download_changes(current_languages)
    changes["subtitles.movie"] = addon_id
    changes["subtitles.tv"] = addon_id
    return changes


def configure_polish_auto_download(jsonrpc):
    """Explicitly enable Polish and Kodi's ``subtitles.downloadfirst`` option.

    This is the only convenience helper in this module which mutates Kodi's
    global subtitle settings, and it does so only when called directly.
    """

    current = get_setting_value(jsonrpc, "subtitles.languages", [])
    changes = build_polish_auto_download_changes(current)
    return apply_setting_changes(jsonrpc, changes)


def list_subtitle_modules(jsonrpc, enabled="all"):
    """Return installed subtitle-provider add-ons without changing their state."""

    if enabled not in ("all", True, False):
        raise ValueError("enabled musi mieć wartość 'all', True albo False")
    result = jsonrpc_call(
        jsonrpc,
        "Addons.GetAddons",
        {
            "type": "xbmc.subtitle.module",
            "enabled": enabled,
            "installed": True,
            "properties": ["name", "version", "enabled"],
        },
    )
    if not isinstance(result, dict):
        raise JsonRpcError("Niepoprawna odpowiedź Addons.GetAddons")

    modules = []
    for addon in result.get("addons", []) or []:
        if not isinstance(addon, dict):
            continue
        addon_id = str(addon.get("addonid") or addon.get("id") or "").strip()
        if not addon_id:
            continue
        modules.append(
            {
                "id": addon_id,
                "name": str(addon.get("name") or addon_id),
                "version": str(addon.get("version") or ""),
                "enabled": bool(addon.get("enabled", False)),
            }
        )
    return sorted(modules, key=lambda item: (item["name"].casefold(), item["id"]))


def find_subtitle_module(modules, addon_id=OPENSUBTITLES_COM_ADDON_ID):
    """Return an installed provider matching ``addon_id`` exactly."""

    wanted = str(addon_id or "").strip().casefold()
    if not wanted:
        return None
    for module in modules or ():
        if not isinstance(module, dict):
            continue
        if str(module.get("id") or "").strip().casefold() == wanted:
            return module
    return None


def is_text_subtitle_path(path):
    """Recognise text subtitle formats supported by the reader."""

    clean = str(path or "").split("?", 1)[0].split("#", 1)[0].casefold()
    return clean.endswith(TEXT_SUBTITLE_EXTENSIONS)


def text_subtitle_file_available(path, exists=None):
    """Check a text subtitle path using an optional injected filesystem probe."""

    if not is_text_subtitle_path(path):
        return False
    if exists is None:
        return True
    try:
        return bool(exists(path))
    except Exception:
        return False


def build_media_fingerprint(
    playing_file="",
    media_kind="",
    unique_ids=None,
    season=None,
    episode=None,
):
    """Create a stable, opaque fingerprint for one playable media item."""

    ids = {}
    for key, value in sorted((unique_ids or {}).items()):
        if value not in (None, ""):
            ids[str(key)] = str(value)
    identity = {
        "file": str(playing_file or "").strip().replace("\\", "/"),
        "ids": ids,
        "season": season,
        "episode": episode,
    }
    if media_kind:
        try:
            identity["kind"] = normalize_media_kind(media_kind)
        except ValueError:
            identity["kind"] = str(media_kind).strip().casefold()
    if not identity["file"] and not ids and season is None and episode is None:
        return ""
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class AutoSubtitleSearch:
    """Open Kodi subtitle search a bounded number of times per media item.

    ``consider`` is safe to call from repeated AVChange notifications.  A
    fingerprint is remembered before the builtin is executed, so even a Kodi
    error cannot create a rapid retry loop.  A second attempt is allowed only
    after ``retry_interval_seconds`` and no more than ``max_attempts`` search
    windows are opened for one fingerprint.
    """

    def __init__(
        self,
        jsonrpc,
        execute_builtin,
        notify=None,
        clock=None,
        cooldown_seconds=30.0,
        retry_interval_seconds=45.0,
        max_attempts=2,
        history_size=256,
    ):
        if not callable(jsonrpc):
            raise TypeError("jsonrpc musi być funkcją")
        if not callable(execute_builtin):
            raise TypeError("execute_builtin musi być funkcją")
        self._jsonrpc = jsonrpc
        self._execute_builtin = execute_builtin
        self._notify = notify
        self._clock = clock or time.monotonic
        self._cooldown = max(0.0, float(cooldown_seconds))
        self._retry_interval = max(0.0, float(retry_interval_seconds))
        self._max_attempts = max(1, int(max_attempts))
        self._history_size = max(1, int(history_size))
        self._attempted = OrderedDict()
        self._last_opened_at = None

    @property
    def attempted_fingerprints(self):
        return tuple(self._attempted.keys())

    def _remember(self, fingerprint, status, attempts=0, last_attempt_at=None):
        self._attempted.pop(fingerprint, None)
        self._attempted[fingerprint] = {
            "status": status,
            "attempts": max(0, int(attempts)),
            "last_attempt_at": last_attempt_at,
        }
        while len(self._attempted) > self._history_size:
            self._attempted.popitem(last=False)

    def _announce(self, result):
        if callable(self._notify):
            try:
                self._notify(result.message)
            except Exception:
                pass
        return result

    def consider(self, fingerprint, media_kind, text_subtitle_available):
        """Evaluate one item and, when eligible, open official subtitle search."""

        fingerprint = str(fingerprint or "").strip()
        if text_subtitle_available:
            previous = self._attempted.get(fingerprint)
            if previous and previous.get("attempts", 0):
                self._remember(
                    fingerprint,
                    "text_available",
                    previous["attempts"],
                    previous.get("last_attempt_at"),
                )
            return AutoSubtitleResult(
                "text_available",
                "Tekstowy plik napisów jest już dostępny.",
                fingerprint,
            )
        if not fingerprint:
            return AutoSubtitleResult(
                "invalid_fingerprint",
                "Nie można uruchomić wyszukiwania bez identyfikatora materiału.",
            )
        previous = self._attempted.get(fingerprint)
        now = float(self._clock())
        if previous:
            if previous["status"] == "text_available":
                return AutoSubtitleResult(
                    "already_found",
                    "Napisy dla tego materiału zostały już znalezione.",
                    fingerprint,
                )
            if previous["status"] != "opened":
                return AutoSubtitleResult(
                    "already_attempted",
                    "Wyszukiwanie napisów dla tego materiału zostało zakończone.",
                    fingerprint,
                )
            if previous["attempts"] >= self._max_attempts:
                return AutoSubtitleResult(
                    "max_attempts",
                    "Kodi nie znalazło napisów po %s próbach. Możesz wyszukać je ręcznie."
                    % self._max_attempts,
                    fingerprint,
                )
            last_attempt_at = previous.get("last_attempt_at")
            if last_attempt_at is not None:
                remaining = self._retry_interval - (now - last_attempt_at)
                if remaining > 0:
                    return AutoSubtitleResult(
                        "retry_wait",
                        "Ponowne wyszukiwanie napisów będzie dostępne za %.1f s."
                        % remaining,
                        fingerprint,
                        retry_after=remaining,
                    )

        try:
            service = get_default_subtitle_service(self._jsonrpc, media_kind)
        except (JsonRpcError, ValueError) as exc:
            attempts = previous["attempts"] if previous else 0
            self._remember(fingerprint, "configuration_error", attempts)
            return self._announce(
                AutoSubtitleResult(
                    "configuration_error",
                    "Nie udało się odczytać ustawień napisów Kodi: %s" % exc,
                    fingerprint,
                )
            )

        if not service:
            attempts = previous["attempts"] if previous else 0
            self._remember(fingerprint, "no_service", attempts)
            return self._announce(
                AutoSubtitleResult(
                    "no_service",
                    "Kodi nie ma ustawionej domyślnej usługi napisów dla tego typu materiału.",
                    fingerprint,
                )
            )

        if self._last_opened_at is not None:
            remaining = self._cooldown - (now - self._last_opened_at)
            if remaining > 0:
                return AutoSubtitleResult(
                    "cooldown",
                    "Wyszukiwanie napisów będzie dostępne za %.1f s." % remaining,
                    fingerprint,
                    service,
                    remaining,
                )

        # Remember before invoking Kodi.  This prevents an AVChange/error loop.
        attempts = (previous["attempts"] if previous else 0) + 1
        self._remember(fingerprint, "attempting", attempts, now)
        try:
            self._execute_builtin(ACTIVATE_SUBTITLE_SEARCH)
        except Exception as exc:
            self._remember(fingerprint, "builtin_error", attempts, now)
            return self._announce(
                AutoSubtitleResult(
                    "builtin_error",
                    "Kodi nie otworzył wyszukiwania napisów: %s" % exc,
                    fingerprint,
                    service,
                )
            )

        self._remember(fingerprint, "opened", attempts, now)
        self._last_opened_at = now
        action = "Ponowiono" if attempts > 1 else "Otwarto"
        return self._announce(
            AutoSubtitleResult(
                "opened",
                "%s oficjalne okno wyszukiwania napisów Kodi (%s), próba %s/%s."
                % (action, service, attempts, self._max_attempts),
                fingerprint,
                service,
            )
        )
