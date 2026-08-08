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

from family_filter import soften
from speech import ElevenLabsClient, SpeechError
from subtitle_source import SubtitleSource


ADDON_ID = "service.subtitle.tts.pl"
TITLE = "Kodi Lektor PL"


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
                audio = client.synthesize(job.text)
                self.results.put(Result(job, audio=audio))
            except SpeechError as exc:
                if exc.retryable and time.monotonic() - job.created < 7.0 and not self.stopping.wait(0.8):
                    try:
                        audio = client.synthesize(job.text)
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

    def onAVStarted(self):
        self.changed = True

    def onAVChange(self):
        self.changed = True

    def onPlayBackStopped(self):
        self.changed = True

    def onPlayBackEnded(self):
        self.changed = True


class ReaderService:
    def __init__(self):
        self.addon = xbmcaddon.Addon(ADDON_ID)
        self.settings = self.addon.getSettings()
        self.monitor = ServiceMonitor()
        self.player = ServicePlayer()
        self.source = SubtitleSource(self.player, lambda value: log(value, xbmc.LOGDEBUG))
        self.worker = LatestWorker()
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
        self.config = {
            "enabled": self.settings.getBool("tts_enabled"),
            "family_mode": self.settings.getBool("family_mode"),
            "filter_level": self.settings.getString("filter_level") or "family",
            "offset": self.settings.getInt("subtitle_offset_ms") / 1000.0,
            "min_gap": self.settings.getInt("min_gap_ms") / 1000.0,
            "show_status": self.settings.getBool("show_status"),
            "api_key": self.settings.getString("api_key").strip(),
            "voice_id": self.settings.getString("voice_id").strip(),
            "model_id": self.settings.getString("model_id").strip() or "eleven_flash_v2_5",
        }
        self.monitor.settings_changed = False

    def reset_playback(self, playing_file=""):
        self.playing_file = playing_file
        self.source.reset(playing_file)
        self.last_visible_text = ""
        self.recent.clear()
        self.pending_audio = None
        self.next_audio_at = 0.0
        self.playback_started = time.monotonic()
        self.no_source_notified = False
        self.no_key_notified = False
        self.audio_warning_shown = False
        self.last_player_time = None
        self.last_player_clock = None

    def warn_audio_settings(self):
        if self.audio_warning_shown:
            return
        passthrough = setting_value("audiooutput.passthrough")
        gui_sounds = setting_value("audiooutput.guisoundmode")
        if passthrough is True:
            notification("Wyłącz passthrough w Ustawienia → System → Dźwięk, aby usłyszeć lektora.", True, 9000)
            self.audio_warning_shown = True
        elif gui_sounds in (0, False):
            notification("Ustaw „Odtwarzaj dźwięki GUI” na „Zawsze”, aby usłyszeć lektora.", True, 9000)
            self.audio_warning_shown = True

    def submit_text(self, text):
        spoken = soften(text, self.config["filter_level"]) if self.config["family_mode"] else " ".join(text.split())
        spoken = spoken[:500].strip()
        if not spoken:
            return
        now = time.monotonic()
        for value, timestamp in list(self.recent.items()):
            if now - timestamp > 20:
                del self.recent[value]
        if now - self.recent.get(spoken, -100.0) < 2.5:
            return
        self.recent[spoken] = now
        profile = xbmcvfs.translatePath(self.addon.getAddonInfo("profile"))
        cache_dir = os.path.join(profile, "cache")
        self.worker.submit(
            Job(spoken, now, self.config["api_key"], self.config["voice_id"], self.config["model_id"], cache_dir)
        )
        log("Przyjęto nową kwestię napisów")

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
            if result.error:
                self.report_error(result.error)
                continue
            if time.monotonic() - result.job.created <= 12.0:
                self.pending_audio = result
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
            self.reset_playback(current_file)
            if self.config["show_status"]:
                notification("Lektor aktywny — szukam napisów.", False, 3000)
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
                self.last_visible_text = ""
                self.recent.clear()
                self.pending_audio = None
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
        text = self.source.text_at(max(0.0, seconds))
        if text != self.last_visible_text:
            self.last_visible_text = text
            if text:
                self.submit_text(text)
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
