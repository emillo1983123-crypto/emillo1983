import os
import queue
import sys
import time
import types
import unittest
from collections import deque


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
sys.path.insert(0, LIB)


xbmc = types.ModuleType("xbmc")
xbmc.LOGDEBUG = 0
xbmc.LOGINFO = 1
xbmc.LOGERROR = 4
xbmc.log = lambda *args, **kwargs: None
xbmc.playSFX = lambda *args, **kwargs: None
xbmc.stopSFX = lambda: None
xbmc.executeJSONRPC = lambda value: "{}"
xbmc.executebuiltin = lambda value: None
xbmc.getInfoLabel = lambda value: ""
xbmc.getCondVisibility = lambda value: False


class Monitor:
    pass


class Player:
    pass


xbmc.Monitor = Monitor
xbmc.Player = Player
sys.modules["xbmc"] = xbmc

xbmcaddon = types.ModuleType("xbmcaddon")
xbmcaddon.Addon = object
sys.modules["xbmcaddon"] = xbmcaddon

xbmcgui = types.ModuleType("xbmcgui")
xbmcgui.NOTIFICATION_ERROR = "error"
xbmcgui.NOTIFICATION_INFO = "info"
xbmcgui.Dialog = object
sys.modules["xbmcgui"] = xbmcgui

xbmcvfs = types.ModuleType("xbmcvfs")
xbmcvfs.translatePath = lambda value: value
sys.modules["xbmcvfs"] = xbmcvfs

import reader_service
import subtitle_source
from auto_subtitles import AutoSubtitleSearch
from cue_tracker import ActiveCueTracker
from reader_service import Job, LatestWorker, ReaderService, Result
from subtitle_parser import parse_subtitle
from subtitle_source import SubtitleSource


class SubtitleSourceCueTests(unittest.TestCase):
    def test_cues_at_exposes_source_signature_and_contexts(self):
        track = parse_subtitle(
            "1\n00:00:01,000 --> 00:00:03,000\nPierwsza\n\n"
            "2\n00:00:02,000 --> 00:00:04,000\nDruga\n",
            "movie.srt",
        )
        source = SubtitleSource.__new__(SubtitleSource)
        source.selected_path = "movie.srt"
        source._track_at = lambda seconds: track
        original_stat = subtitle_source._local_stat
        subtitle_source._local_stat = lambda path: (12.5, 321)
        try:
            source_key, contexts = source.cues_at(2.5)
        finally:
            subtitle_source._local_stat = original_stat
        self.assertEqual(source_key, ("movie.srt", 12.5, 321))
        self.assertEqual([context.index for context in contexts], [0, 1])


class ReaderGenerationTests(unittest.TestCase):
    def _service_for_results(self):
        service = ReaderService.__new__(ReaderService)
        service.worker = types.SimpleNamespace(results=queue.Queue())
        service.generation = 2
        service.pending_audio = None
        service.pending_audio_queue = deque()
        service.next_audio_at = float("inf")
        service.config = {"min_gap": 0.0}
        return service

    @staticmethod
    def _job(generation):
        return Job("Tekst", time.monotonic(), "key", "voice", "model", "cache", generation)

    def test_stale_success_and_error_are_ignored_before_reporting(self):
        service = self._service_for_results()
        reported = []
        service.report_error = reported.append
        stale = self._job(1)
        current = self._job(2)
        service.worker.results.put(Result(stale, audio=types.SimpleNamespace(path="old.wav", duration=1.0)))
        service.worker.results.put(Result(stale, error=RuntimeError("old error")))
        service.worker.results.put(Result(current, audio=types.SimpleNamespace(path="new.wav", duration=1.0)))
        service.process_results()
        self.assertEqual(reported, [])
        self.assertIs(service.pending_audio.job, current)

    def test_invalidation_increments_generation_clears_state_and_stops_sfx(self):
        service = ReaderService.__new__(ReaderService)
        service.generation = 4
        service.last_visible_text = "stary"
        service.recent = {"stary": 1.0}
        service.pending_audio = object()
        service.pending_audio_queue = deque((object(),))
        service.next_audio_at = 20.0
        service.cue_tracker = ActiveCueTracker()
        service.cue_tracker.use_source(("movie.srt", 1.0, 10))
        stopped = []
        original = reader_service.xbmc.stopSFX
        reader_service.xbmc.stopSFX = lambda: stopped.append(True)
        try:
            service.invalidate_audio(stop_sfx=True)
        finally:
            reader_service.xbmc.stopSFX = original
        self.assertEqual(service.generation, 5)
        self.assertEqual(service.last_visible_text, "")
        self.assertEqual(service.recent, {})
        self.assertIsNone(service.pending_audio)
        self.assertEqual(list(service.pending_audio_queue), [])
        self.assertEqual(service.next_audio_at, 0.0)
        self.assertIsNone(service.cue_tracker.source_key)
        self.assertEqual(stopped, [True])


class ReaderAudioQueueTests(unittest.TestCase):
    @staticmethod
    def _service(generation=1):
        service = ReaderService.__new__(ReaderService)
        service.worker = types.SimpleNamespace(results=queue.Queue())
        service.generation = generation
        service.pending_audio = None
        service.pending_audio_queue = deque()
        service.next_audio_at = float("inf")
        service.config = {"min_gap": 0.0}
        service.report_error = lambda error: None
        return service

    @staticmethod
    def _result(label, created, generation=1, duration=0.1):
        job = Job(label, created, "key", "voice", "model", "cache", generation)
        audio = types.SimpleNamespace(path=label + ".wav", duration=duration)
        return Result(job, audio=audio)

    def test_ready_buffer_keeps_complete_lines_in_fifo_order(self):
        service = self._service()
        now = time.monotonic()
        for label in ("A", "B", "C"):
            service.worker.results.put(self._result(label, now))

        service.process_results()

        self.assertEqual(service.pending_audio.job.text, "A")
        self.assertEqual(
            [item.job.text for item in service.pending_audio_queue],
            ["B", "C"],
        )

    def test_worker_uses_small_fifo_and_reports_overflow(self):
        worker = LatestWorker()
        jobs = [
            Job(label, time.monotonic(), "key", "voice", "model", "cache", 1)
            for label in ("A", "B", "C", "D")
        ]
        self.assertEqual([worker.submit(job) for job in jobs], [True, True, True, False])
        self.assertEqual(
            [worker.jobs.get_nowait().text for _index in range(3)],
            ["A", "B", "C"],
        )

    def test_two_ready_lines_play_in_fifo_order(self):
        service = self._service()
        now = time.monotonic()
        service.worker.results.put(self._result("A", now))
        service.worker.results.put(self._result("B", now))
        service.next_audio_at = 0.0
        service.cue_tracker = ActiveCueTracker()
        service.source = types.SimpleNamespace(selected_path="")
        service.config.update({"enabled": True, "hide_visible_subtitles": False})
        service.subtitle_manual_override = False
        played = []
        original_play = reader_service.xbmc.playSFX
        reader_service.xbmc.playSFX = lambda path, loop: played.append(path)
        try:
            service.process_results()
            service.next_audio_at = 0.0
            service.process_results()
        finally:
            reader_service.xbmc.playSFX = original_play

        self.assertEqual(played, ["A.wav", "B.wav"])
        self.assertIsNone(service.pending_audio)
        self.assertEqual(list(service.pending_audio_queue), [])

    def test_stale_result_is_rejected_on_arrival(self):
        service = self._service()
        service.worker.results.put(
            self._result(
                "stale",
                time.monotonic() - reader_service.MAX_RESULT_AGE_SECONDS - 1.0,
            )
        )
        service.process_results()
        self.assertIsNone(service.pending_audio)

    def test_result_that_ages_in_buffer_is_skipped_for_newer_line(self):
        service = self._service()
        service.worker.results.put(self._result("old", 90.0))
        service.worker.results.put(self._result("new", 99.0))
        original_clock = reader_service.time.monotonic
        try:
            reader_service.time.monotonic = lambda: 100.0
            service.process_results()
            self.assertEqual(service.pending_audio.job.text, "old")

            reader_service.time.monotonic = lambda: 111.0
            service.process_results()
        finally:
            reader_service.time.monotonic = original_clock

        self.assertEqual(service.pending_audio.job.text, "new")
        self.assertEqual(list(service.pending_audio_queue), [])


class ReaderEconomyTests(unittest.TestCase):
    @staticmethod
    def _service(economy_mode):
        jobs = []
        service = ReaderService.__new__(ReaderService)
        service.config = {
            "family_mode": False,
            "economy_mode": economy_mode,
            "filter_level": "family",
            "api_key": "key",
            "voice_id": "voice",
            "model_id": "eleven_flash_v2_5",
            "speech_speed_percent": 90,
            "voice_profile": "warm",
        }
        service.recent = {}
        service.generation = 3
        service.worker = types.SimpleNamespace(submit=jobs.append)
        service.addon = types.SimpleNamespace(getAddonInfo=lambda key: "profile")
        return service, jobs

    def test_economy_setting_compresses_text_and_zeros_context(self):
        service, jobs = self._service(True)
        accepted = service.submit_text(
            "JAN: Yyy, n-nie wiem wiem.",
            "Poprzednia kwestia.",
            "Następna kwestia.",
            (("cue", 1),),
        )
        self.assertTrue(accepted)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].text, "nie wiem.")
        self.assertEqual(jobs[0].previous_text, "")
        self.assertEqual(jobs[0].next_text, "")
        self.assertTrue(jobs[0].economy_mode)
        self.assertEqual(jobs[0].speech_speed_percent, 90)
        self.assertEqual(jobs[0].voice_profile, "warm")

    def test_economy_off_keeps_natural_context(self):
        service, jobs = self._service(False)
        service.submit_text(
            "Bieżąca kwestia.",
            "Poprzednia , kwestia .",
            "Następna ?",
            (("cue", 1),),
        )
        self.assertEqual(jobs[0].text, "Bieżąca kwestia.")
        self.assertEqual(jobs[0].previous_text, "Poprzednia, kwestia.")
        self.assertEqual(jobs[0].next_text, "Następna?")
        self.assertFalse(jobs[0].economy_mode)

    def test_main_subtitle_text_is_not_silently_cut_at_500_characters(self):
        service, jobs = self._service(False)
        text = ("Pełna długa kwestia. " * 35).strip()
        self.assertGreater(len(text), 500)

        service.submit_text(text, cue_ids=(("cue", 1),))

        self.assertEqual(jobs[0].text, text)

    def test_profile_and_speed_are_part_of_tts_generation_signature(self):
        base = {
            "enabled": True,
            "family_mode": True,
            "economy_mode": True,
            "filter_level": "family",
            "api_key": "key",
            "voice_id": "voice",
            "model_id": "model",
            "speech_speed_percent": 95,
            "voice_profile": "classic",
        }
        speed = dict(base, speech_speed_percent=90)
        profile = dict(base, voice_profile="natural")
        self.assertNotEqual(
            ReaderService._tts_config_signature(base),
            ReaderService._tts_config_signature(speed),
        )
        self.assertNotEqual(
            ReaderService._tts_config_signature(base),
            ReaderService._tts_config_signature(profile),
        )


class FakeVideoTag:
    def __init__(self, media_type="movie", unique_ids=None, season=-1, episode=-1):
        self.media_type = media_type
        self.unique_ids = dict(unique_ids or {})
        self.season = season
        self.episode = episode

    def getMediaType(self):
        return self.media_type

    def getUniqueIDs(self):
        return self.unique_ids

    def getUniqueID(self, key):
        return self.unique_ids.get(key, "")

    def getSeason(self):
        return self.season

    def getEpisode(self):
        return self.episode


class FakePlayer:
    def __init__(self, playing_file, tag=None):
        self.playing_file = playing_file
        self.tag = tag or FakeVideoTag()
        self.changed = False
        self.av_changed = False
        self.seconds = 20.0
        self.subtitle_enabled = True
        self.subtitle_visibility_calls = []
        self.events = []

    def isPlayingVideo(self):
        return True

    def getPlayingFile(self):
        return self.playing_file

    def getVideoInfoTag(self):
        return self.tag

    def getTime(self):
        return self.seconds

    def showSubtitles(self, visible):
        self.subtitle_enabled = bool(visible)
        self.subtitle_visibility_calls.append(visible)
        self.events.append(("show", visible))


class FakeSource:
    def __init__(self, selected_path=""):
        self.selected_path = selected_path
        self.source_key = (selected_path, 1.0, 100) if selected_path else None
        self.contexts = []
        self.last_scan = 99.0
        self.reset_calls = []

    def reset(self, playing_file=""):
        self.reset_calls.append(playing_file)
        self.selected_path = ""
        self.source_key = None
        self.contexts = []
        self.last_scan = 0.0

    def cues_at(self, _seconds):
        return self.source_key, self.contexts


class ReaderAutoSubtitleTests(unittest.TestCase):
    @staticmethod
    def _jsonrpc(payload):
        import json

        request = json.loads(payload)
        setting = request.get("params", {}).get("setting")
        value = "service.subtitles.example" if setting in ("subtitles.movie", "subtitles.tv") else None
        return json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": {"value": value}})

    def _service(self, playing_file="smb://server/movie.mkv", selected_path=""):
        service = ReaderService.__new__(ReaderService)
        service.player = FakePlayer(playing_file)
        service.source = FakeSource(selected_path)
        service.cue_tracker = ActiveCueTracker()
        service.generation = 3
        service.config = {
            "enabled": True,
            "auto_subtitles": True,
            "hide_visible_subtitles": True,
            "api_key": "secret",
            "show_status": False,
            "offset": 0.0,
            "min_gap": 0.0,
        }
        service.playing_file = playing_file
        service.last_visible_text = ""
        service.recent = {}
        service.pending_audio = None
        service.next_audio_at = 0.0
        service.playback_started = time.monotonic() - 8.0
        service.no_source_notified = False
        service.no_key_notified = False
        service.audio_warning_shown = False
        service.last_errors = {}
        service.last_player_time = None
        service.last_player_clock = None
        service.warn_audio_settings = lambda: None
        service.submit_text = lambda *args, **kwargs: True
        service.auto_builtin_calls = []
        service.auto_subtitles = AutoSubtitleSearch(
            self._jsonrpc,
            service.auto_builtin_calls.append,
            cooldown_seconds=0,
        )
        return service

    @staticmethod
    def _poll_without_notifications(service):
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: None
        try:
            service.poll_video()
        finally:
            reader_service.notification = original

    def test_search_opens_once_for_repeated_poll_and_same_file_avchange(self):
        service = self._service()
        started = service.playback_started
        generation = service.generation
        self._poll_without_notifications(service)
        service.player.av_changed = True
        service.source.last_scan = 123.0
        self._poll_without_notifications(service)

        self.assertEqual(service.auto_builtin_calls, ["ActivateWindow(subtitlesearch)"])
        self.assertEqual(service.playback_started, started)
        self.assertEqual(service.generation, generation)
        self.assertEqual(service.source.reset_calls, [])
        self.assertEqual(service.source.last_scan, 0.0)

    def test_text_source_blocks_search(self):
        service = self._service(selected_path="special://temp/movie.pl.srt")
        self._poll_without_notifications(service)
        self.assertEqual(service.auto_builtin_calls, [])

    def test_pvr_and_live_playback_block_search(self):
        pvr = self._service("pvr://channels/tv/1")
        self._poll_without_notifications(pvr)
        self.assertEqual(pvr.auto_builtin_calls, [])

        live = self._service("https://example.invalid/live.m3u8")
        original = reader_service.xbmc.getCondVisibility
        reader_service.xbmc.getCondVisibility = lambda name: name == "Player.IsLive"
        try:
            self._poll_without_notifications(live)
        finally:
            reader_service.xbmc.getCondVisibility = original
        self.assertEqual(live.auto_builtin_calls, [])

    def test_disabled_tts_missing_key_and_disabled_option_block_search(self):
        for changed in (
            {"enabled": False},
            {"api_key": ""},
            {"auto_subtitles": False},
        ):
            service = self._service()
            service.config.update(changed)
            self._poll_without_notifications(service)
            self.assertEqual(service.auto_builtin_calls, [])

    def test_new_file_performs_full_reset_and_restarts_grace_period(self):
        service = self._service("smb://server/old.mkv")
        service.player.playing_file = "smb://server/new.mkv"
        old_started = service.playback_started
        old_generation = service.generation
        self._poll_without_notifications(service)

        self.assertEqual(service.playing_file, "smb://server/new.mkv")
        self.assertEqual(service.source.reset_calls, ["smb://server/new.mkv"])
        self.assertEqual(service.generation, old_generation + 1)
        self.assertGreater(service.playback_started, old_started)
        self.assertEqual(service.auto_builtin_calls, [])

    def test_seek_invalidation_preserves_the_new_generation(self):
        service = self._service()
        service.playback_started = time.monotonic()
        service.last_player_time = 1.0
        service.last_player_clock = time.monotonic() - 0.1
        service.player.seconds = 30.0
        old_generation = service.generation
        self._poll_without_notifications(service)
        self.assertEqual(service.generation, old_generation + 1)

    def test_episode_identity_uses_video_tag_ids_and_numbers(self):
        service = self._service()
        service.player.tag = FakeVideoTag("episode", {"tvdb": "123"}, 2, 4)
        first, media_kind = service._video_identity(service.playing_file)
        second, _ = service._video_identity(service.playing_file)
        service.player.tag.episode = 5
        other, _ = service._video_identity(service.playing_file)
        self.assertEqual(media_kind, "tv")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)


class ReaderSubtitleVisibilityTests(unittest.TestCase):
    @staticmethod
    def _poll(service):
        original = reader_service.notification
        original_visibility = reader_service.xbmc.getCondVisibility
        reader_service.notification = lambda *args, **kwargs: None
        reader_service.xbmc.getCondVisibility = (
            lambda name: service.player.subtitle_enabled
            if name == "VideoPlayer.SubtitlesEnabled"
            else False
        )
        try:
            service.poll_video()
        finally:
            reader_service.notification = original
            reader_service.xbmc.getCondVisibility = original_visibility

    @staticmethod
    def _process(service, error=None, fail_playback=False):
        job = Job(
            "Czytany tekst.",
            time.monotonic(),
            "key",
            "voice",
            "model",
            "cache",
            service.generation,
        )
        audio = None if error else types.SimpleNamespace(path="voice.wav", duration=1.0)
        service.worker.results.put(Result(job, audio=audio, error=error))
        original_play = reader_service.xbmc.playSFX
        original_visibility = reader_service.xbmc.getCondVisibility
        original_notification = reader_service.notification

        def play(path, loop):
            service.player.events.append(("play", path))
            if fail_playback:
                raise RuntimeError("playback failed")

        reader_service.xbmc.playSFX = play
        reader_service.xbmc.getCondVisibility = (
            lambda name: service.player.subtitle_enabled
            if name == "VideoPlayer.SubtitlesEnabled"
            else False
        )
        reader_service.notification = lambda *args, **kwargs: None
        try:
            service.process_results()
        finally:
            reader_service.xbmc.playSFX = original_play
            reader_service.xbmc.getCondVisibility = original_visibility
            reader_service.notification = original_notification

    @staticmethod
    def _service(hide_visible=True):
        service = ReaderAutoSubtitleTests()._service(
            selected_path="special://temp/movie.pl.srt"
        )
        service.config["hide_visible_subtitles"] = hide_visible
        track = parse_subtitle(
            "1\n00:00:19,000 --> 00:00:21,000\nCzytany tekst.\n",
            "movie.pl.srt",
        )
        service.source.contexts = track.active(20.0)
        service.spoken = []
        service.submit_text = lambda *args, **kwargs: service.spoken.append(args) or True
        service.worker = types.SimpleNamespace(results=queue.Queue())
        service.subtitles_hidden_by_us = False
        service.subtitles_were_visible = False
        service.subtitle_manual_override = False
        service.subtitle_audio_source = None
        service.hidden_subtitle_source = None
        service.subtitle_hide_requested_at = None
        return service

    def test_hides_only_immediately_before_first_successful_audio(self):
        service = self._service(True)
        selected_path = service.source.selected_path

        self._poll(service)
        self.assertEqual(service.player.subtitle_visibility_calls, [])

        self._process(service)
        self._process(service)

        self.assertEqual(service.player.subtitle_visibility_calls, [False])
        self.assertEqual(
            service.player.events[:2],
            [("show", False), ("play", "voice.wav")],
        )
        self.assertTrue(service.config["enabled"])
        self.assertEqual(service.source.selected_path, selected_path)
        self.assertEqual(len(service.spoken), 1)
        self.assertEqual(service.spoken[0][0], "Czytany tekst.")
        self.assertTrue(service.subtitles_hidden_by_us)
        self.assertTrue(service.subtitles_were_visible)

    def test_setting_false_leaves_visible_subtitles_untouched(self):
        service = self._service(False)
        self._poll(service)
        self._process(service)
        self.assertEqual(service.player.subtitle_visibility_calls, [])
        self.assertEqual(service.source.selected_path, "special://temp/movie.pl.srt")
        self.assertEqual(len(service.spoken), 1)

    def test_source_change_hides_again(self):
        service = self._service(True)
        self._poll(service)
        self._process(service)

        service.player.subtitle_enabled = True
        service.source.selected_path = "special://temp/movie.other.pl.srt"
        service.source.source_key = (
            "special://temp/movie.other.pl.srt",
            2.0,
            120,
        )
        self._poll(service)
        self._process(service)

        self.assertEqual(service.player.subtitle_visibility_calls, [False, False])
        self.assertFalse(service.subtitle_manual_override)

    def test_manual_show_override_survives_seek_and_same_file_avchange(self):
        service = self._service(True)
        self._poll(service)
        self._process(service)

        service.player.subtitle_enabled = True
        service.subtitle_hide_requested_at = (
            time.monotonic() - reader_service.SUBTITLE_HIDE_CONFIRM_SECONDS - 0.1
        )
        self._poll(service)
        self.assertTrue(service.subtitle_manual_override)

        service.last_player_time = 1.0
        service.last_player_clock = time.monotonic() - 0.1
        service.player.seconds = 30.0
        self._poll(service)
        service.player.av_changed = True
        self._poll(service)
        self._process(service)

        self.assertTrue(service.subtitle_manual_override)
        self.assertEqual(service.player.subtitle_visibility_calls, [False])

    def test_visibility_propagation_delay_is_not_mistaken_for_manual_override(self):
        service = self._service(True)
        self._poll(service)
        self._process(service)

        # Simulate Kodi reporting the old visibility condition for a few
        # frames after showSubtitles(False).
        service.player.subtitle_enabled = True
        service.subtitle_hide_requested_at = time.monotonic()
        self._poll(service)
        self.assertFalse(service.subtitle_manual_override)

        service.subtitle_hide_requested_at = (
            time.monotonic() - reader_service.SUBTITLE_HIDE_CONFIRM_SECONDS - 0.1
        )
        self._poll(service)
        self.assertTrue(service.subtitle_manual_override)

    def test_new_playback_resets_manual_override_and_can_hide_again(self):
        service = self._service(True)
        self._poll(service)
        self._process(service)
        service.player.subtitle_enabled = True
        service.subtitle_hide_requested_at = (
            time.monotonic() - reader_service.SUBTITLE_HIDE_CONFIRM_SECONDS - 0.1
        )
        self._poll(service)
        self.assertTrue(service.subtitle_manual_override)

        service.reset_playback(service.player.playing_file)
        service.source.selected_path = "special://temp/movie.pl.srt"
        service.source.source_key = ("special://temp/movie.pl.srt", 1.0, 100)
        track = parse_subtitle(
            "1\n00:00:19,000 --> 00:00:21,000\nNowy film.\n",
            "movie.pl.srt",
        )
        service.source.contexts = track.active(20.0)

        self._poll(service)
        self._process(service)

        self.assertEqual(service.player.subtitle_visibility_calls, [False, False])
        self.assertFalse(service.subtitle_manual_override)

    def test_disabling_option_or_tts_restores_only_our_hidden_overlay(self):
        for changed in ({"hide_visible_subtitles": False}, {"enabled": False}):
            service = self._service(True)
            self._poll(service)
            self._process(service)
            service.config.update(changed)

            self._poll(service)

            self.assertEqual(service.player.subtitle_visibility_calls, [False, True])
            self.assertFalse(service.subtitles_hidden_by_us)

        already_hidden = self._service(True)
        already_hidden.player.subtitle_enabled = False
        self._poll(already_hidden)
        self._process(already_hidden)
        already_hidden.config["enabled"] = False
        self._poll(already_hidden)
        self.assertEqual(already_hidden.player.subtitle_visibility_calls, [])

    def test_every_final_error_restores_hidden_subtitles(self):
        fatal = self._service(True)
        self._poll(fatal)
        self._process(fatal)
        self._process(
            fatal,
            error=types.SimpleNamespace(user_message="fatal", retryable=False),
        )
        self.assertEqual(fatal.player.subtitle_visibility_calls, [False, True])

        retryable = self._service(True)
        self._poll(retryable)
        self._process(retryable)
        self._process(
            retryable,
            error=types.SimpleNamespace(user_message="retry", retryable=True),
        )
        self.assertEqual(retryable.player.subtitle_visibility_calls, [False, True])

    def test_failed_playback_rolls_back_hide(self):
        service = self._service(True)
        self._poll(service)

        self._process(service, fail_playback=True)

        self.assertEqual(service.player.subtitle_visibility_calls, [False, True])
        self.assertEqual(
            service.player.events,
            [("show", False), ("play", "voice.wav"), ("show", True)],
        )
        self.assertFalse(service.subtitles_hidden_by_us)


class AudioWarningTests(unittest.TestCase):
    def test_gui_sounds_value_two_is_always_and_does_not_warn(self):
        service = ReaderService.__new__(ReaderService)
        service.audio_warning_shown = False
        messages = []
        original_setting = reader_service.setting_value
        original_notification = reader_service.notification
        reader_service.setting_value = lambda key: False if key == "audiooutput.passthrough" else 2
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.warn_audio_settings()
        finally:
            reader_service.setting_value = original_setting
            reader_service.notification = original_notification
        self.assertEqual(messages, [])
        self.assertFalse(service.audio_warning_shown)

    def test_gui_sounds_value_one_warns_to_select_always(self):
        service = ReaderService.__new__(ReaderService)
        service.audio_warning_shown = False
        messages = []
        original_setting = reader_service.setting_value
        original_notification = reader_service.notification
        reader_service.setting_value = lambda key: False if key == "audiooutput.passthrough" else 1
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.warn_audio_settings()
        finally:
            reader_service.setting_value = original_setting
            reader_service.notification = original_notification
        self.assertEqual(len(messages), 1)
        self.assertTrue(service.audio_warning_shown)


if __name__ == "__main__":
    unittest.main()
