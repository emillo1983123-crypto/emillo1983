import os
import queue
import sys
import types
import unittest
from collections import deque


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)


if "xbmc" not in sys.modules:
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
    xbmc.Monitor = type("Monitor", (), {})
    xbmc.Player = type("Player", (), {})
    sys.modules["xbmc"] = xbmc

if "xbmcaddon" not in sys.modules:
    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = object
    sys.modules["xbmcaddon"] = xbmcaddon

if "xbmcgui" not in sys.modules:
    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.NOTIFICATION_ERROR = "error"
    xbmcgui.NOTIFICATION_INFO = "info"
    xbmcgui.Dialog = object
    sys.modules["xbmcgui"] = xbmcgui

if "xbmcvfs" not in sys.modules:
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda value: value
    xbmcvfs.exists = lambda value: False
    xbmcvfs.listdir = lambda value: ([], [])
    sys.modules["xbmcvfs"] = xbmcvfs


import reader_service  # noqa: E402
import subtitle_source  # noqa: E402
from cue_tracker import ActiveCueTracker  # noqa: E402
from reader_service import ReaderService  # noqa: E402
from subtitle_parser import Cue, SubtitleTrack  # noqa: E402
from subtitle_source import SubtitleSource  # noqa: E402
from usage import estimate_film_usage, parse_subscription_usage  # noqa: E402
from usage_worker import UsageResult  # noqa: E402


class SelectedTrackApiTests(unittest.TestCase):
    def test_public_selected_track_can_scan_and_reject_stale_source_key(self):
        track = SubtitleTrack([Cue(0, 2, "Tekst")], "movie.srt")
        source = SubtitleSource.__new__(SubtitleSource)
        source.selected_path = "movie.srt"
        source._track_at = lambda seconds: track
        original_stat = subtitle_source._local_stat
        subtitle_source._local_stat = lambda path: (12.0, 345)
        try:
            self.assertIs(
                source.selected_track(1.0, ("movie.srt", 12.0, 345)),
                track,
            )
            self.assertIsNone(
                source.selected_track(1.0, ("old.srt", 1.0, 1)),
            )
        finally:
            subtitle_source._local_stat = original_stat


class ReaderUsageRuntimeTests(unittest.TestCase):
    @staticmethod
    def _service():
        service = ReaderService.__new__(ReaderService)
        service.generation = 4
        service.usage_generation = 2
        service.config = {
            "provider_id": "elevenlabs",
            "api_key": "secret",
            "model_id": "eleven_flash_v2_5",
            "family_mode": False,
            "economy_mode": False,
            "filter_level": "family",
            "show_status": True,
        }
        service.usage_request_identity = None
        service.usage_status_identity = None
        service.usage_source_key = None
        service.usage_permission_warned = False
        return service

    def test_api_model_and_text_policy_change_usage_signature_but_speed_does_not(self):
        base = {
            "enabled": True,
            "provider_id": "elevenlabs",
            "api_key": "key-a",
            "model_id": "eleven_flash_v2_5",
            "family_mode": False,
            "economy_mode": False,
            "filter_level": "family",
            "speech_speed_percent": 95,
        }
        signature = ReaderService._usage_config_signature(base)
        for changed in (
            dict(base, provider_id="kwpj"),
            dict(base, api_key="key-b"),
            dict(base, model_id="eleven_multilingual_v2"),
            dict(base, family_mode=True),
            dict(base, economy_mode=True),
        ):
            self.assertNotEqual(
                signature,
                ReaderService._usage_config_signature(changed),
            )
        self.assertEqual(
            signature,
            ReaderService._usage_config_signature(
                dict(base, speech_speed_percent=85)
            ),
        )

    def test_full_track_is_queued_once_with_the_runtime_prepare_callback(self):
        service = self._service()
        source_key = ("movie.srt", 1.0, 100)
        track = SubtitleTrack(
            [
                Cue(0, 1, " Ala , ma kota . "),
                Cue(2, 3, "Druga kwestia."),
            ],
            "movie.srt",
        )
        service.source = types.SimpleNamespace(
            selected_track=lambda source_key=None: track,
        )
        jobs = []

        class Worker:
            results = queue.Queue()

            @staticmethod
            def submit(job):
                jobs.append(job)
                return True

        service.usage_worker = Worker()

        self.assertTrue(service._maybe_submit_usage(source_key))
        self.assertFalse(service._maybe_submit_usage(source_key))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].cues, tuple(track.cues))
        self.assertEqual(jobs[0].prepare_text(" Ala , ma kota . "), "Ala, ma kota.")
        self.assertNotIn("secret", repr(jobs[0]))

    def test_hidden_status_does_not_start_an_account_request(self):
        service = self._service()
        service.config["show_status"] = False
        service.source = types.SimpleNamespace(
            selected_track=lambda source_key=None: SubtitleTrack(
                [Cue(0, 1, "Tekst filmu.")], "movie.srt"
            )
        )
        submitted = []
        service.usage_worker = types.SimpleNamespace(
            submit=lambda job: submitted.append(job) or True,
            results=queue.Queue(),
        )

        self.assertFalse(service._maybe_submit_usage(("movie.srt", 1.0, 100)))
        self.assertEqual(submitted, [])

    def test_status_contains_film_remaining_and_remaining_after_once(self):
        service = self._service()
        source_key = ("movie.srt", 1.0, 100)
        service.usage_request_identity = (service.usage_generation, source_key)
        service.usage_worker = types.SimpleNamespace(results=queue.Queue())
        estimate = estimate_film_usage(
            ["x" * 1_200],
            lambda text: text,
            "eleven_flash_v2_5",
        )
        subscription = parse_subscription_usage(
            {"credit_count": 250, "credit_limit": 1_000}
        )
        result = UsageResult(
            service.usage_generation,
            source_key,
            estimate=estimate,
            subscription=subscription,
        )
        service.usage_worker.results.put(result)
        service.usage_worker.results.put(result)
        messages = []
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.process_usage_results()
        finally:
            reader_service.notification = original

        self.assertEqual(len(messages), 1)
        self.assertIn("Film: ok. 600 kred.", messages[0][0])
        self.assertIn("pozostało 750", messages[0][0])
        self.assertIn("po filmie ok. 150", messages[0][0])

    def test_character_quota_is_not_mislabeled_as_credits(self):
        service = self._service()
        source_key = ("movie.srt", 1.0, 100)
        service.usage_request_identity = (service.usage_generation, source_key)
        service.usage_worker = types.SimpleNamespace(results=queue.Queue())
        service.usage_worker.results.put(
            UsageResult(
                service.usage_generation,
                source_key,
                estimate=estimate_film_usage(
                    ["x" * 200], lambda text: text, "eleven_flash_v2_5"
                ),
                subscription=parse_subscription_usage(
                    {"character_count": 100, "character_limit": 1_000}
                ),
            )
        )
        messages = []
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.process_usage_results()
        finally:
            reader_service.notification = original

        self.assertEqual(len(messages), 1)
        self.assertIn("200 znaków (ok. 100 kred.)", messages[0][0])
        self.assertIn("limit API: zostało 900", messages[0][0])
        self.assertIn("po filmie ok. 700", messages[0][0])

    def test_permission_status_is_informational_and_does_not_touch_audio(self):
        service = self._service()
        source_key = ("movie.srt", 1.0, 100)
        service.usage_request_identity = (service.usage_generation, source_key)
        service.usage_worker = types.SimpleNamespace(results=queue.Queue())
        service.usage_worker.results.put(
            UsageResult(
                service.usage_generation,
                source_key,
                error_kind="missing_user_read",
                user_message=(
                    "Limit ElevenLabs: klucz nie ma uprawnienia "
                    "User: Read (user_read). Lektor nadal działa."
                ),
            )
        )
        service.pending_audio = object()
        messages = []
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.process_usage_results()
        finally:
            reader_service.notification = original

        self.assertEqual(len(messages), 1)
        self.assertFalse(messages[0][1])
        self.assertIn("User: Read (user_read)", messages[0][0])
        self.assertIsNotNone(service.pending_audio)

    def test_hidden_status_suppresses_success_and_permission_notifications(self):
        service = self._service()
        service.config["show_status"] = False
        source_key = ("movie.srt", 1.0, 100)
        service.usage_request_identity = (service.usage_generation, source_key)
        service.usage_worker = types.SimpleNamespace(results=queue.Queue())
        service.usage_worker.results.put(
            UsageResult(
                service.usage_generation,
                source_key,
                estimate=estimate_film_usage(
                    ["x" * 200], lambda text: text, "eleven_flash_v2_5"
                ),
                subscription=parse_subscription_usage(
                    {"character_count": 100, "character_limit": 1_000}
                ),
            )
        )
        messages = []
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.process_usage_results()
            service.usage_status_identity = None
            service.usage_worker.results.put(
                UsageResult(
                    service.usage_generation,
                    source_key,
                    error_kind="missing_user_read",
                    user_message="Brak User Read. Lektor nadal działa.",
                )
            )
            service.process_usage_results()
        finally:
            reader_service.notification = original

        self.assertEqual(messages, [])

    def test_missing_user_read_is_reported_only_once_until_key_changes(self):
        service = self._service()
        messages = []
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            for index in (1, 2):
                source_key = ("movie-%s.srt" % index, float(index), 100)
                service.usage_request_identity = (service.usage_generation, source_key)
                service.usage_status_identity = None
                service.usage_worker = types.SimpleNamespace(results=queue.Queue())
                service.usage_worker.results.put(
                    UsageResult(
                        service.usage_generation,
                        source_key,
                        error_kind="missing_user_read",
                        user_message="Brak User Read. Lektor nadal działa.",
                    )
                )
                service.process_usage_results()
        finally:
            reader_service.notification = original

        self.assertEqual(len(messages), 1)

    def test_seek_does_not_refetch_or_show_a_second_status(self):
        service = self._service()
        source_key = ("movie.srt", 1.0, 100)
        track = SubtitleTrack([Cue(0, 2, "Tekst filmu.")], "movie.srt")
        service.source = types.SimpleNamespace(
            selected_track=lambda source_key=None: track,
        )
        invalidated = []
        submitted = []
        service.worker = types.SimpleNamespace(invalidate=lambda value: None)
        service.usage_worker = types.SimpleNamespace(
            invalidate=invalidated.append,
            submit=lambda job: submitted.append(job) or True,
            results=queue.Queue(),
        )
        service.last_visible_text = "tekst"
        service.recent = {}
        service.pending_audio = None
        service.pending_audio_queue = deque()
        service.next_audio_at = 0.0
        service.cue_tracker = ActiveCueTracker()
        service.cue_tracker.use_source(source_key)
        service.usage_source_key = source_key
        self.assertTrue(service._maybe_submit_usage(source_key))
        estimate = estimate_film_usage(
            track.cues,
            service._prepare_spoken,
            service.config["model_id"],
        )
        result = UsageResult(
            service.usage_generation,
            source_key,
            estimate=estimate,
            subscription=parse_subscription_usage(
                {"character_count": 100, "character_limit": 1_000}
            ),
        )
        service.usage_worker.results.put(result)
        messages = []
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.process_usage_results()
            old_usage_generation = service.usage_generation
            old_identity = service.usage_request_identity
            service.invalidate_audio(stop_sfx=True)
            self.assertTrue(service.cue_tracker.use_source(source_key))
            self.assertFalse(service._use_usage_source(source_key))
            self.assertFalse(service._maybe_submit_usage(source_key))
            # Even a late duplicate cannot display a second notification.
            service.usage_worker.results.put(result)
            service.process_usage_results()
        finally:
            reader_service.notification = original

        service.invalidate_audio()
        self.assertEqual(service.generation, 6)
        self.assertEqual(service.usage_generation, old_usage_generation)
        self.assertEqual(service.usage_request_identity, old_identity)
        self.assertEqual(invalidated, [])
        self.assertEqual(len(submitted), 1)
        self.assertEqual(len(messages), 1)

    def test_new_source_cancels_old_result_and_refetches_once(self):
        service = self._service()
        source_a = ("a.srt", 1.0, 100)
        source_b = ("b.srt", 2.0, 200)
        tracks = {
            source_a: SubtitleTrack([Cue(0, 1, "Stary.")], "a.srt"),
            source_b: SubtitleTrack([Cue(0, 1, "Nowy film.")], "b.srt"),
        }
        invalidated = []
        submitted = []
        results = queue.Queue()
        service.source = types.SimpleNamespace(
            selected_track=lambda source_key=None: tracks[source_key],
        )
        service.usage_worker = types.SimpleNamespace(
            invalidate=invalidated.append,
            submit=lambda job: submitted.append(job) or True,
            results=results,
        )
        service.usage_source_key = source_a

        self.assertTrue(service._maybe_submit_usage(source_a))
        stale_generation = service.usage_generation
        self.assertTrue(service._use_usage_source(source_b))
        self.assertTrue(service._maybe_submit_usage(source_b))
        current_generation = service.usage_generation
        subscription = parse_subscription_usage(
            {"character_count": 100, "character_limit": 1_000}
        )
        results.put(
            UsageResult(
                stale_generation,
                source_a,
                estimate=estimate_film_usage(
                    tracks[source_a].cues, service._prepare_spoken, "eleven_flash_v2_5"
                ),
                subscription=subscription,
            )
        )
        results.put(
            UsageResult(
                current_generation,
                source_b,
                estimate=estimate_film_usage(
                    tracks[source_b].cues, service._prepare_spoken, "eleven_flash_v2_5"
                ),
                subscription=subscription,
            )
        )
        messages = []
        original = reader_service.notification
        reader_service.notification = lambda *args, **kwargs: messages.append(args)
        try:
            service.process_usage_results()
        finally:
            reader_service.notification = original

        self.assertEqual(invalidated, [current_generation])
        self.assertEqual([job.source_key for job in submitted], [source_a, source_b])
        self.assertEqual(len(messages), 1)
        self.assertEqual(service.usage_status_identity, (current_generation, source_b))

    def test_new_movie_reset_cancels_usage_and_allows_new_request(self):
        service = self._service()
        source_key = ("new.srt", 2.0, 200)
        track = SubtitleTrack([Cue(0, 1, "Nowy film.")], "new.srt")
        invalidated = []
        submitted = []
        reset_calls = []
        service.worker = types.SimpleNamespace(invalidate=lambda value: None)
        service.usage_worker = types.SimpleNamespace(
            invalidate=invalidated.append,
            submit=lambda job: submitted.append(job) or True,
            results=queue.Queue(),
        )
        service.source = types.SimpleNamespace(
            reset=reset_calls.append,
            selected_track=lambda source_key=None: track,
        )
        service.playing_file = "old.mkv"
        service.usage_source_key = ("old.srt", 1.0, 100)
        service.last_visible_text = "tekst"
        service.recent = {}
        service.pending_audio = None
        service.pending_audio_queue = deque()
        service.next_audio_at = 0.0
        service.cue_tracker = ActiveCueTracker()
        service.subtitles_hidden_by_us = False
        service.subtitles_were_visible = False
        old_usage_generation = service.usage_generation

        service.reset_playback("new.mkv")
        self.assertTrue(service._maybe_submit_usage(source_key))

        self.assertEqual(service.usage_generation, old_usage_generation + 1)
        self.assertEqual(invalidated, [old_usage_generation + 1])
        self.assertEqual(reset_calls, ["new.mkv"])
        self.assertIsNone(service.usage_source_key)
        self.assertEqual(len(submitted), 1)


if __name__ == "__main__":
    unittest.main()
