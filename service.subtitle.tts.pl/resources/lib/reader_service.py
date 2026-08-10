"""Long-running Kodi service that narrates the currently visible subtitle cue."""

from __future__ import unicode_literals

import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from auto_subtitles import AutoSubtitleSearch, build_media_fingerprint
from cue_tracker import ActiveCueTracker
from family_filter import soften
from speech import ElevenLabsClient, SpeechError
from subtitle_source import SubtitleSource
from text_normalizer import compress_for_economy, normalize_for_speech


ADDON_ID = "service.subtitle.tts.pl"
TITLE = "Kodi Lektor PL"
AUTO_SUBTITLE_DELAY_SECONDS = 7.0


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


@dataclass(frozen=True)
class Result:
    job: Job
    audio: object = None
    error: object = None


class LatestWorker(threading.Thread):
    def __init__(self):
        super().__init__(name="KodiLektorTTS", daemon=True)
        self.jobs = queue.Queue(maxsize=1)
        self.results = queue.Queue()
        self.stopping = threading.Event()

    def submit(self, job):
        try:
            self.jobs.put_nowait(job)
            return
        except queue.Full:
            pass
        try:
            self.jobs.get_nowait()
        except queue.Empty:
            pass
        try:
            self.jobs.put_nowait(job)
        except queue.Full:
            pass

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
            client = ElevenLabsClient(job.api_key, job.voice_id, job.model_id, job.cache_dir)
            try:
                audio = client.synthesize(job.text, job.previous_text, job.next_text, job.economy_mode)
                self.results.put(Result(job, audio=audio))
            except SpeechError as exc:
                if exc.retryable and time.monotonic() - job.created < 7.0 and not self.stopping.wait(0.8):
                    try:
                        audio = client.synthesize(job.text, job.previous_text, job.next_text, job.economy_mode)
                        self.results.put(Result(job, audio=audio))
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
        self.next_audio_at = 0.0
        self.playback_started = 0.0
        self.no_source_notified = False
        self.no_key_notified = False
        self.audio_warning_shown = False
        self.last_errors = {}
        self.last_player_time = None
        self.last_player_clock = None

    def reload_settings(self):
        old_signature = self._tts_config_signature(self.config)
        new_config = {
            "enabled": self.settings.getBool("tts_enabled"),
            "auto_subtitles": self.settings.getBool("auto_subtitles"),
            "family_mode": self.settings.getBool("family_mode"),
            "economy_mode": self.settings.getBool("economy_mode"),
            "filter_level": self.settings.getString("filter_level") or "family",
            "offset": self.settings.getInt("subtitle_offset_ms") / 1000.0,
            "min_gap": self.settings.getInt("min_gap_ms") / 1000.0,
            "show_status": self.settings.getBool("show_status"),
            "api_key": self.settings.getString("api_key").strip(),
            "voice_id": self.settings.getString("voice_id").strip(),
            "model_id": self.settings.getString("model_id").strip() or "eleven_flash_v2_5",
        }
        self.config = new_config
        self.monitor.settings_changed = False
        if old_signature and old_signature != self._tts_config_signature(new_config):
            self.invalidate_audio(stop_sfx=True)

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
            )
        )

    def _prepare_spoken(self, text, limit):
        value = text or ""
        if self.config["family_mode"]:
            value = soften(value, self.config["filter_level"])
        if self.config.get("economy_mode"):
            value = compress_for_economy(value)
        else:
            value = normalize_for_speech(value)
        return value[:limit].strip()

    def invalidate_audio(self, stop_sfx=False, reset_cues=True):
        self.generation += 1
        self.last_visible_text = ""
        self.recent.clear()
        self.pending_audio = None
        self.next_audio_at = 0.0
        if reset_cues:
            self.cue_tracker.reset()
        if stop_sfx:
            try:
                xbmc.stopSFX()
            except Exception:
                pass

    def reset_playback(self, playing_file=""):
        self.invalidate_audio(stop_sfx=bool(self.playing_file))
        self.playing_file = playing_file
        self.source.reset(playing_file)
        self.playback_started = time.monotonic()
        self.no_source_notified = False
        self.no_key_notified = False
        self.audio_warning_shown = False
        self.last_player_time = None
        self.last_player_clock = None

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
        spoken = self._prepare_spoken(text, 500)
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
        self.worker.submit(
            Job(
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
            )
        )
        log("Przyjęto nową kwestię napisów")
        return True

    def report_error(self, error):
        message = getattr(error, "user_message", "Nie udało się przygotować głosu.")
        now = time.monotonic()
        if now - self.last_errors.get(message, -1000.0) >= 60:
            self.last_errors[message] = now
            notification(message, True, 8000)
        log(message, xbmc.LOGERROR)

    def process_results(self):
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
            if time.monotonic() - result.job.created <= 12.0:
                self.pending_audio = result
        if self.pending_audio and self.pending_audio.job.generation != self.generation:
            self.pending_audio = None
        if self.pending_audio and time.monotonic() >= self.next_audio_at:
            result = self.pending_audio
            self.pending_audio = None
            try:
                xbmc.playSFX(result.audio.path, False)
                self.next_audio_at = time.monotonic() + max(0.2, result.audio.duration) + self.config["min_gap"]
            except Exception as exc:
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
        if not self.config["enabled"]:
            return
        if not self.config["api_key"]:
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
        if self.cue_tracker.use_source(source_key):
            self.invalidate_audio(stop_sfx=True, reset_cues=False)
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
            self.worker.stop()
            self.worker.join(timeout=2.0)
            log("Zatrzymano")


def run():
    ReaderService().run()
