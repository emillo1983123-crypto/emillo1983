"""Long-running Kodi service that narrates the currently visible subtitle cue."""

from __future__ import unicode_literals

import json
import os
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from auto_subtitles import AutoSubtitleSearch, build_media_fingerprint
from cue_tracker import ActiveCueTracker
from family_filter import soften
from speech import ElevenLabsClient, SpeechCancelled, SpeechError
from subtitle_source import SubtitleSource
from text_normalizer import compress_for_economy, normalize_for_speech


ADDON_ID = "service.subtitle.tts.pl"
TITLE = "Kodi Lektor PL"
AUTO_SUBTITLE_DELAY_SECONDS = 7.0
SUBTITLE_HIDE_CONFIRM_SECONDS = 0.75
MAX_RESULT_AGE_SECONDS = 20.0
MAX_JOB_QUEUE_SIZE = 3
MAX_READY_AUDIO_SIZE = 3
VOICE_PROFILE_KEYS = ("classic", "warm", "natural", "dynamic")


def log(message, level=xbmc.LOGINFO):
    xbmc.log("[%s] %s" % (TITLE, message), level)


def notification(message, error=False, duration=6000):
    icon = xbmcgui.NOTIFICATION_ERROR if error else xbmcgui.NOTIFICATION_INFO
    xbmcgui.Dialog().notification(TITLE, message, icon, duration)


def setting_value(setting_id):
    query = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "Settings.GetSettingValue",
        "params": {"setting": setting_id},
    }
    try:
        response = json.loads(xbmc.executeJSONRPC(json.dumps(query)))
        return response.get("result", {}).get("value")
    except Exception:
        return None


def _safe_new_bool(settings, setting_id, default):
    """Read a newly introduced boolean without breaking a hot add-on update."""

    try:
        value = settings.getBool(setting_id)
    except Exception:
        return bool(default)
    return value if isinstance(value, bool) else bool(default)


def _safe_new_int(settings, setting_id, default, minimum=None, maximum=None):
    """Read a newly introduced integer and reject missing or malformed values."""

    try:
        value = settings.getInt(setting_id)
    except Exception:
        return int(default)
    if isinstance(value, bool) or not isinstance(value, int):
        return int(default)
    if minimum is not None and value < minimum:
        return int(default)
    if maximum is not None and value > maximum:
        return int(default)
    return value


def _safe_new_profile(settings, setting_id, default="classic"):
    """Read a known profile key while tolerating stale Kodi setting definitions."""

    try:
        value = settings.getString(setting_id)
    except Exception:
        return default
    if not isinstance(value, str):
        return default
    value = value.strip().casefold()
    return value if value in VOICE_PROFILE_KEYS else default


@dataclass(frozen=True)
class Job:
    text: str
    created: float
    api_key: str
    voice_id: str
    model_id: str
    cache_dir: str
    generation: int = 0
    cue_ids: tuple = ()
    previous_text: str = ""
    next_text: str = ""
    economy_mode: bool = False
    speech_speed_percent: int = 95
    voice_profile: str = "classic"


@dataclass(frozen=True)
class Result:
    job: Job
    audio: object = None
    error: object = None


class LatestWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="KodiLektorTTS", daemon=True)
        self.jobs = queue.Queue(maxsize=MAX_JOB_QUEUE_SIZE)
        self.results = queue.Queue()
        self.stopping = threading.Event()
        self.latest_generation = 0

    def submit(self, job):
        self.latest_generation = job.generation
        try:
            self.jobs.put_nowait(job)
            return True
        except queue.Full:
            return False

    def invalidate(self, generation):
        self.latest_generation = generation
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

    def run(self):
        while not self.stopping.is_set():
            try:
                job = self.jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            if job is None:
                continue
            if job.generation != self.latest_generation:
                continue
            client = ElevenLabsClient(
                job.api_key,
                job.voice_id,
                job.model_id,
                job.cache_dir,
                speech_speed_percent=job.speech_speed_percent,
                voice_profile=job.voice_profile,
            )
            cancelled = lambda: (
                self.stopping.is_set() or job.generation != self.latest_generation
            )
            try:
                audio = client.synthesize(
                    job.text,
                    job.previous_text,
                    job.next_text,
                    job.economy_mode,
                    cancelled=cancelled,
                )
                self.results.put(Result(job, audio=audio))
            except SpeechCancelled:
                continue
            except SpeechError as exc:
                if exc.retryable and time.monotonic() - job.created < 7.0 and not self.stopping.wait(0.8):
                    try:
                        audio = client.synthesize(
                            job.text,
                            job.previous_text,
                            job.next_text,
                            job.economy_mode,
                            cancelled=cancelled,
                        )
                        self.results.put(Result(job, audio=audio))
                        continue
                    except SpeechCancelled:
                        continue
                    except SpeechError as retry_error:
                        exc = retry_error
                self.results.put(Result(job, error=exc))
            except Exception as exc:
                log("Nieoczekiwany błąd syntezy: %s" % exc, xbmc.LOGERROR)
                self.results.put(Result(job, error=SpeechError("Nie udało się przygotować głosu.")))


class ServiceMonitor(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.settings_changed = True

    def onSettingsChanged(self):
        self.settings_changed = True


class ServicePlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.changed = True
        self.av_changed = False

    def onAVStarted(self):
        self.changed = True
        self.av_changed = False

    def onAVChange(self):
        self.av_changed = True

    def onPlayBackStopped(self):
        self.changed = True
        self.av_changed = False

    def onPlayBackEnded(self):
        self.changed = True
        self.av_changed = False


class ReaderService:
    def __init__(self):
        self.addon = xbmcaddon.Addon(ADDON_ID)
        self.settings = self.addon.getSettings()
        self.monitor = ServiceMonitor()
        self.player = ServicePlayer()
        self.source = SubtitleSource(self.player, lambda value: log(value, xbmc.LOGDEBUG))
        self.auto_subtitles = AutoSubtitleSearch(
            xbmc.executeJSONRPC,
            xbmc.executebuiltin,
            notify=self._auto_subtitle_notification,
            cooldown_seconds=30.0,
        )
        self.worker = LatestWorker()
        self.cue_tracker = ActiveCueTracker()
        self.generation = 0
        self.config = {}
        self.playing_file = ""
        self.last_visible_text = ""
        self.recent = {}
        self.pending_audio = None
        self.pending_audio_queue = deque()
        self.next_audio_at = 0.0
        self.playback_started = 0.0
        self.no_source_notified = False
        self.no_key_notified = False
        self.audio_warning_shown = False
        self.last_errors = {}
        self.last_player_time = None
        self.last_player_clock = None
        self.subtitles_hidden_by_us = False
        self.subtitles_were_visible = False
        self.subtitle_manual_override = False
        self.subtitle_audio_source = None
        self.hidden_subtitle_source = None
        self.subtitle_hide_requested_at = None

    def reload_settings(self):
        old_signature = self._tts_config_signature(self.config)
        old_config = dict(self.config)
        new_config = {
            "enabled": self.settings.getBool("tts_enabled"),
            "auto_subtitles": self.settings.getBool("auto_subtitles"),
            "hide_visible_subtitles": _safe_new_bool(
                self.settings, "hide_visible_subtitles", True
            ),
            "family_mode": self.settings.getBool("family_mode"),
            "economy_mode": self.settings.getBool("economy_mode"),
            "filter_level": self.settings.getString("filter_level") or "family",
            "offset": self.settings.getInt("subtitle_offset_ms") / 1000.0,
            "min_gap": self.settings.getInt("min_gap_ms") / 1000.0,
            "show_status": self.settings.getBool("show_status"),
            "api_key": self.settings.getString("api_key").strip(),
            "voice_id": self.settings.getString("voice_id").strip(),
            "model_id": self.settings.getString("model_id").strip() or "eleven_flash_v2_5",
            "speech_speed_percent": _safe_new_int(
                self.settings,
                "speech_speed_percent",
                95,
                minimum=70,
                maximum=120,
            ),
            "voice_profile": _safe_new_profile(
                self.settings, "voice_profile", "classic"
            ),
        }
        self.config = new_config
        self.monitor.settings_changed = False
        if old_signature and old_signature != self._tts_config_signature(new_config):
            self.invalidate_audio(stop_sfx=True)
        if old_config and (
            not new_config["enabled"]
            or not new_config["hide_visible_subtitles"]
            or not new_config["api_key"]
        ):
            self._restore_visible_subtitles()

    @staticmethod
    def _tts_config_signature(config):
        if not config:
            return ()
        return tuple(
            config.get(key)
            for key in (
                "enabled",
                "family_mode",
                "economy_mode",
                "filter_level",
                "api_key",
                "voice_id",
                "model_id",
                "speech_speed_percent",
                "voice_profile",
            )
        )

    def _prepare_spoken(self, text, limit=None):
        value = text or ""
        if self.config["family_mode"]:
            value = soften(value, self.config["filter_level"])
        if self.config.get("economy_mode"):
            value = compress_for_economy(value)
        else:
            value = normalize_for_speech(value)
        value = value.strip()
        return value[:limit].strip() if limit else value

    def invalidate_audio(self, stop_sfx=False, reset_cues=True):
        self.generation += 1
        invalidate_worker = getattr(getattr(self, "worker", None), "invalidate", None)
        if callable(invalidate_worker):
            invalidate_worker(self.generation)
        self.last_visible_text = ""
        self.recent.clear()
        self.pending_audio = None
        pending_queue = getattr(self, "pending_audio_queue", None)
        if pending_queue is None:
            self.pending_audio_queue = deque()
        else:
            pending_queue.clear()
        self.next_audio_at = 0.0
        if reset_cues:
            self.cue_tracker.reset()
        if stop_sfx:
            try:
                xbmc.stopSFX()
            except Exception:
                pass

    def reset_playback(self, playing_file=""):
        self._restore_visible_subtitles()
        self.invalidate_audio(stop_sfx=bool(self.playing_file))
        self.playing_file = playing_file
        self.source.reset(playing_file)
        self.playback_started = time.monotonic()
        self.no_source_notified = False
        self.no_key_notified = False
        self.audio_warning_shown = False
        self.last_player_time = None
        self.last_player_clock = None
        self.subtitles_hidden_by_us = False
        self.subtitles_were_visible = False
        self.subtitle_manual_override = False
        self.subtitle_audio_source = None
        self.hidden_subtitle_source = None
        self.subtitle_hide_requested_at = None

    @staticmethod
    def _subtitle_overlay_visible():
        try:
            return bool(xbmc.getCondVisibility("VideoPlayer.SubtitlesEnabled"))
        except Exception:
            return None

    def _restore_visible_subtitles(self):
        """Restore the overlay only when this service previously hid it."""

        if not (
            getattr(self, "subtitles_hidden_by_us", False)
            and getattr(self, "subtitles_were_visible", False)
        ):
            return False
        try:
            self.player.showSubtitles(True)
        except Exception as exc:
            log("Nie mozna przywrocic napisow ekranowych: %s" % exc, xbmc.LOGERROR)
            return False
        self.subtitles_hidden_by_us = False
        self.subtitles_were_visible = False
        self.hidden_subtitle_source = None
        self.subtitle_hide_requested_at = None
        return True

    def _observe_subtitle_manual_override(self, source_key):
        """Remember a user's decision to show subtitles for this playback."""

        if (
            not source_key
            or getattr(self, "subtitle_manual_override", False)
            or not self.config.get("hide_visible_subtitles")
            or source_key != getattr(self, "subtitle_audio_source", None)
        ):
            return False
        requested_at = getattr(self, "subtitle_hide_requested_at", None)
        if (
            getattr(self, "subtitles_hidden_by_us", False)
            and requested_at is not None
            and time.monotonic() - requested_at < SUBTITLE_HIDE_CONFIRM_SECONDS
        ):
            # Kodi may need a few frames before its visibility condition
            # reflects showSubtitles(False). Do not mistake that propagation
            # delay for a user's manual override.
            return False
        if self._subtitle_overlay_visible() is not True:
            return False
        self.subtitle_manual_override = True
        self.subtitles_hidden_by_us = False
        self.subtitles_were_visible = False
        self.hidden_subtitle_source = None
        self.subtitle_hide_requested_at = None
        return True

    def _hide_visible_subtitles_for_audio(self, source_key):
        """Hide the overlay immediately before starting synthesized audio."""

        if (
            not self.config.get("enabled")
            or not self.config.get("hide_visible_subtitles")
            or getattr(self, "subtitle_manual_override", False)
            or not source_key
            or not self.source.selected_path
            or self._subtitle_overlay_visible() is not True
        ):
            return False
        try:
            self.player.showSubtitles(False)
        except Exception as exc:
            log("Nie mozna ukryc napisow ekranowych: %s" % exc, xbmc.LOGERROR)
            return False
        self.subtitles_hidden_by_us = True
        self.subtitles_were_visible = True
        self.hidden_subtitle_source = source_key
        self.subtitle_hide_requested_at = time.monotonic()
        return True

    def _auto_subtitle_notification(self, message):
        """Show only final auto-search outcomes; the helper suppresses repeats."""

        notification(message, False, 6000)

    @staticmethod
    def _tag_value(tag, method_name, *args):
        method = getattr(tag, method_name, None)
        if not callable(method):
            return None
        try:
            return method(*args)
        except Exception:
            return None

    def _video_identity(self, current_file):
        """Build a defensive movie/episode identity from Kodi's VideoInfoTag."""

        try:
            tag = self.player.getVideoInfoTag()
        except Exception:
            tag = None

        media_type = ""
        unique_ids = {}
        season = None
        episode = None
        if tag is not None:
            media_type = str(self._tag_value(tag, "getMediaType") or "").strip().casefold()
            all_ids = self._tag_value(tag, "getUniqueIDs")
            if isinstance(all_ids, dict):
                for key, value in all_ids.items():
                    if value not in (None, ""):
                        unique_ids[str(key)] = str(value)
            for key in ("imdb", "tmdb", "tvdb"):
                if key not in unique_ids:
                    value = self._tag_value(tag, "getUniqueID", key)
                    if value not in (None, ""):
                        unique_ids[key] = str(value)
            season = self._tag_value(tag, "getSeason")
            episode = self._tag_value(tag, "getEpisode")

        if not isinstance(season, int) or season < 0:
            season = None
        if not isinstance(episode, int) or episode < 0:
            episode = None
        tv_types = {"episode", "tv", "tvshow", "series", "season"}
        media_kind = "tv" if media_type in tv_types or season is not None or episode is not None else "movie"
        fingerprint = build_media_fingerprint(
            current_file,
            media_kind,
            unique_ids,
            season,
            episode,
        )
        return fingerprint, media_kind

    def _is_live_playback(self, current_file):
        value = str(current_file or "").strip().casefold()
        if value.startswith("pvr://"):
            return True
        condition = getattr(xbmc, "getCondVisibility", None)
        if callable(condition):
            for name in ("Player.IsLive", "Pvr.IsPlayingTV", "Pvr.IsPlayingRadio"):
                try:
                    if condition(name):
                        return True
                except Exception:
                    pass
        try:
            tag = self.player.getVideoInfoTag()
        except Exception:
            tag = None
        media_type = str(self._tag_value(tag, "getMediaType") or "").strip().casefold() if tag else ""
        return media_type in ("channel", "live", "livetv", "pvr")

    def _maybe_open_subtitle_search(self, current_file, now=None):
        """Open official search once after the text-source grace period."""

        if not self.config.get("enabled") or not self.config.get("api_key"):
            return None
        if not self.config.get("auto_subtitles"):
            return None
        if self.source.selected_path:
            return None
        now = time.monotonic() if now is None else float(now)
        if now - self.playback_started < AUTO_SUBTITLE_DELAY_SECONDS:
            return None
        if self._is_live_playback(current_file):
            return None
        fingerprint, media_kind = self._video_identity(current_file)
        if not fingerprint:
            return None
        result = self.auto_subtitles.consider(fingerprint, media_kind, False)
        if result.status == "opened":
            log("Otwarto wyszukiwanie napisow dla %s" % media_kind)
        elif result.status in ("configuration_error", "no_service", "builtin_error"):
            log(result.message, xbmc.LOGERROR)
        return result

    def _force_source_rescan(self):
        """Ask SubtitleSource to scan again without resetting playback state."""

        self.source.last_scan = 0.0

    def warn_audio_settings(self):
        if self.audio_warning_shown:
            return
        passthrough = setting_value("audiooutput.passthrough")
        gui_sounds = setting_value("audiooutput.guisoundmode")
        if passthrough is True:
            notification("Wyłącz passthrough w Ustawienia → System → Dźwięk, aby usłyszeć lektora.", True, 9000)
            self.audio_warning_shown = True
        elif gui_sounds is not None and gui_sounds != 2:
            notification("Ustaw „Odtwarzaj dźwięki GUI” na „Zawsze”, aby usłyszeć lektora.", True, 9000)
            self.audio_warning_shown = True

    def submit_text(self, text, previous_text="", next_text="", cue_ids=()):
        spoken = self._prepare_spoken(text)
        if not spoken:
            return False
        economy_mode = bool(self.config.get("economy_mode"))
        previous_spoken = "" if economy_mode else self._prepare_spoken(previous_text, 250)
        next_spoken = "" if economy_mode else self._prepare_spoken(next_text, 250)
        now = time.monotonic()
        if not cue_ids:
            for value, timestamp in list(self.recent.items()):
                if now - timestamp > 20:
                    del self.recent[value]
            if now - self.recent.get(spoken, -100.0) < 2.5:
                return False
            self.recent[spoken] = now
        profile = xbmcvfs.translatePath(self.addon.getAddonInfo("profile"))
        cache_dir = os.path.join(profile, "cache")
        job = Job(
                spoken,
                now,
                self.config["api_key"],
                self.config["voice_id"],
                self.config["model_id"],
                cache_dir,
                self.generation,
                tuple(cue_ids),
                previous_spoken,
                next_spoken,
                economy_mode,
                self.config.get("speech_speed_percent", 95),
                self.config.get("voice_profile", "classic"),
            )
        if self.worker.submit(job) is False:
            if cue_ids:
                self.cue_tracker.seen.difference_update(cue_ids)
            log("Kolejka lektora jest chwilowo pełna", xbmc.LOGDEBUG)
            return False
        log("Przyjęto nową kwestię napisów")
        return True

    def report_error(self, error):
        self._restore_visible_subtitles()
        message = getattr(error, "user_message", "Nie udało się przygotować głosu.")
        now = time.monotonic()
        if now - self.last_errors.get(message, -1000.0) >= 60:
            self.last_errors[message] = now
            notification(message, True, 8000)
        log(message, xbmc.LOGERROR)

    def process_results(self):
        now = time.monotonic()
        while True:
            try:
                result = self.worker.results.get_nowait()
            except queue.Empty:
                break
            if result.job.generation != self.generation:
                continue
            if result.error:
                self.report_error(result.error)
                continue
            if now - result.job.created <= MAX_RESULT_AGE_SECONDS:
                if self.pending_audio is None:
                    self.pending_audio = result
                elif len(self.pending_audio_queue) < MAX_READY_AUDIO_SIZE - 1:
                    self.pending_audio_queue.append(result)
                else:
                    log("Bufor gotowego głosu jest pełny — pomijam spóźnioną kwestię", xbmc.LOGDEBUG)

        while self.pending_audio and (
            self.pending_audio.job.generation != self.generation
            or now - self.pending_audio.job.created > MAX_RESULT_AGE_SECONDS
        ):
            self.pending_audio = self.pending_audio_queue.popleft() if self.pending_audio_queue else None

        if self.pending_audio and now >= self.next_audio_at:
            result = self.pending_audio
            self.pending_audio = self.pending_audio_queue.popleft() if self.pending_audio_queue else None
            source_key = getattr(getattr(self, "cue_tracker", None), "source_key", None)
            hidden_for_attempt = self._hide_visible_subtitles_for_audio(source_key)
            try:
                xbmc.playSFX(result.audio.path, False)
                self.subtitle_audio_source = source_key
                if getattr(self, "subtitles_hidden_by_us", False):
                    self.hidden_subtitle_source = source_key
                self.next_audio_at = time.monotonic() + max(0.2, result.audio.duration) + self.config["min_gap"]
            except Exception as exc:
                if hidden_for_attempt:
                    self._restore_visible_subtitles()
                log("Nie można odtworzyć WAV: %s" % exc, xbmc.LOGERROR)
                notification("Kodi nie odtworzył próbki WAV. Sprawdź ustawienia dźwięku.", True)

    def poll_video(self):
        try:
            is_video = self.player.isPlayingVideo()
        except Exception:
            is_video = False
        if not is_video:
            if self.playing_file:
                self.reset_playback("")
            return
        try:
            current_file = self.player.getPlayingFile()
        except Exception:
            current_file = ""
        if self.player.changed or current_file != self.playing_file:
            self.player.changed = False
            self.player.av_changed = False
            self.reset_playback(current_file)
            if self.config["show_status"]:
                notification("Lektor aktywny — szukam napisów.", False, 3000)
        elif getattr(self.player, "av_changed", False):
            # A downloaded subtitle can emit AVChange for the same media file.
            # A full reset here would restart the grace period and search loop.
            self.player.av_changed = False
            self._force_source_rescan()
        if not self.config.get("hide_visible_subtitles"):
            self._restore_visible_subtitles()
        if not self.config["enabled"]:
            self._restore_visible_subtitles()
            return
        if not self.config["api_key"]:
            self._restore_visible_subtitles()
            if not self.no_key_notified:
                self.no_key_notified = True
                notification("Wpisz klucz API ElevenLabs w ustawieniach Kodi Lektor PL.", True, 8000)
            return
        self.warn_audio_settings()
        try:
            player_seconds = self.player.getTime()
        except Exception:
            return
        clock = time.monotonic()
        if self.last_player_time is not None and self.last_player_clock is not None:
            expected = self.last_player_time + (clock - self.last_player_clock)
            if abs(player_seconds - expected) > 1.5:
                self.invalidate_audio(stop_sfx=True)
        self.last_player_time = player_seconds
        self.last_player_clock = clock
        # Kodi shifts display by Player.SubtitleDelay. To address the same cue,
        # move the lookup in the opposite direction.
        delay = 0.0
        try:
            label = xbmc.getInfoLabel("Player.SubtitleDelay") or ""
            match = re.search(r"[-+]?\d+(?:[.,]\d+)?", label)
            if match:
                delay = float(match.group(0).replace(",", "."))
        except (TypeError, ValueError):
            delay = 0.0
        seconds = player_seconds - delay + self.config["offset"]
        source_key, contexts = self.source.cues_at(max(0.0, seconds))
        source_changed = self.cue_tracker.use_source(source_key)
        if source_changed:
            self.invalidate_audio(stop_sfx=True, reset_cues=False)
        self._observe_subtitle_manual_override(source_key)
        batch = self.cue_tracker.observe(contexts)
        if batch:
            self.last_visible_text = batch.text
            self.submit_text(
                batch.text,
                batch.previous_text,
                batch.next_text,
                batch.cue_ids,
            )
        self._maybe_open_subtitle_search(current_file, clock)
        if (
            not self.source.selected_path
            and not self.no_source_notified
            and time.monotonic() - self.playback_started > 15.0
        ):
            self.no_source_notified = True
            notification("Nie znaleziono tekstowego pliku napisów. Wybierz napisy SRT/VTT/ASS.", True, 8000)

    def run(self):
        self.reload_settings()
        self.worker.start()
        log("Uruchomiono wersję %s" % self.addon.getAddonInfo("version"))
        if self.config["show_status"]:
            notification("Uruchomiony — Termux nie jest potrzebny.", False, 4000)
        try:
            while not self.monitor.abortRequested():
                if self.monitor.settings_changed:
                    self.reload_settings()
                self.poll_video()
                self.process_results()
                if self.monitor.waitForAbort(0.15):
                    break
        finally:
            self._restore_visible_subtitles()
            self.worker.stop()
            self.worker.join(timeout=2.0)
            log("Zatrzymano")


def run():
    ReaderService().run()
