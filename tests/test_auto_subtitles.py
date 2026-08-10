import json
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
sys.path.insert(0, LIB)

from auto_subtitles import (
    ACTIVATE_SUBTITLE_SEARCH,
    AutoSubtitleSearch,
    JsonRpcError,
    apply_setting_changes,
    build_media_fingerprint,
    build_polish_auto_download_changes,
    configure_polish_auto_download,
    get_default_subtitle_service,
    is_text_subtitle_path,
    list_subtitle_modules,
    text_subtitle_file_available,
)


class JsonRpcStub:
    def __init__(self, settings=None, addons=None):
        self.settings = dict(settings or {})
        self.addons = list(addons or [])
        self.calls = []

    def __call__(self, payload):
        request = json.loads(payload)
        self.calls.append(request)
        method = request["method"]
        params = request.get("params", {})
        if method == "Settings.GetSettingValue":
            result = {"value": self.settings.get(params["setting"])}
        elif method == "Settings.SetSettingValue":
            self.settings[params["setting"]] = params["value"]
            result = True
        elif method == "Addons.GetAddons":
            result = {"addons": self.addons}
        else:
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request["id"],
                    "error": {"code": -32601, "message": "unknown method"},
                }
            )
        return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class SettingsTests(unittest.TestCase):
    def test_reads_movie_and_tv_default_services(self):
        rpc = JsonRpcStub(
            {
                "subtitles.movie": "service.subtitles.movie",
                "subtitles.tv": "service.subtitles.tv",
            }
        )
        self.assertEqual(
            get_default_subtitle_service(rpc, "film"),
            "service.subtitles.movie",
        )
        self.assertEqual(
            get_default_subtitle_service(rpc, "episode"),
            "service.subtitles.tv",
        )

    def test_building_changes_is_pure_and_apply_is_explicit(self):
        rpc = JsonRpcStub({"subtitles.languages": ["English"]})
        changes = build_polish_auto_download_changes(["English", "polish"])
        self.assertEqual(rpc.calls, [])
        self.assertEqual(changes["subtitles.languages"], ["Polish", "English"])
        self.assertTrue(changes["subtitles.downloadfirst"])

        apply_setting_changes(rpc, changes)
        self.assertEqual(rpc.settings["subtitles.languages"], ["Polish", "English"])
        self.assertIs(rpc.settings["subtitles.downloadfirst"], True)
        self.assertEqual(
            [call["method"] for call in rpc.calls],
            ["Settings.SetSettingValue", "Settings.SetSettingValue"],
        )

    def test_explicit_configure_preserves_other_languages(self):
        rpc = JsonRpcStub({"subtitles.languages": ["German", "Polish"]})
        applied = configure_polish_auto_download(rpc)
        self.assertEqual(applied["subtitles.languages"], ["Polish", "German"])
        self.assertEqual(rpc.settings["subtitles.languages"], ["Polish", "German"])
        self.assertTrue(rpc.settings["subtitles.downloadfirst"])

    def test_lists_only_normalized_subtitle_module_details(self):
        rpc = JsonRpcStub(
            addons=[
                {
                    "addonid": "service.subtitles.zeta",
                    "name": "Zeta",
                    "version": "2.0.0",
                    "enabled": False,
                },
                {
                    "addonid": "service.subtitles.alpha",
                    "name": "Alpha",
                    "version": "1.0.0",
                    "enabled": True,
                },
            ]
        )
        modules = list_subtitle_modules(rpc)
        self.assertEqual([item["name"] for item in modules], ["Alpha", "Zeta"])
        self.assertEqual(rpc.calls[0]["method"], "Addons.GetAddons")
        self.assertEqual(rpc.calls[0]["params"]["type"], "xbmc.subtitle.module")
        self.assertEqual(rpc.calls[0]["params"]["enabled"], "all")
        self.assertTrue(rpc.calls[0]["params"]["installed"])

    def test_jsonrpc_errors_are_explicit(self):
        def rejected(_payload):
            return {"error": {"code": -1, "message": "rejected"}}

        with self.assertRaises(JsonRpcError):
            get_default_subtitle_service(rejected, "movie")


class PureHelperTests(unittest.TestCase):
    def test_text_subtitle_detection_supports_urls_and_existence_probe(self):
        self.assertTrue(is_text_subtitle_path("special://temp/Movie.PL.SRT?token=1"))
        self.assertFalse(is_text_subtitle_path("Movie.mkv"))
        self.assertTrue(text_subtitle_file_available("Movie.vtt", lambda _path: True))
        self.assertFalse(text_subtitle_file_available("Movie.vtt", lambda _path: False))
        self.assertFalse(
            text_subtitle_file_available(
                "Movie.ass",
                lambda _path: (_ for _ in ()).throw(OSError("missing")),
            )
        )

    def test_fingerprint_is_stable_and_distinguishes_episodes(self):
        first = build_media_fingerprint(
            "smb://server/show/episode.mkv",
            "tv",
            {"tvdb": 123},
            season=1,
            episode=2,
        )
        again = build_media_fingerprint(
            "smb://server/show/episode.mkv",
            "episode",
            {"tvdb": 123},
            season=1,
            episode=2,
        )
        other = build_media_fingerprint(
            "smb://server/show/episode.mkv",
            "tv",
            {"tvdb": 123},
            season=1,
            episode=3,
        )
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 64)
        self.assertEqual(build_media_fingerprint(), "")


class AutoSubtitleSearchTests(unittest.TestCase):
    def _rpc(self, movie="service.subtitles.example", tv="service.subtitles.tv"):
        return JsonRpcStub({"subtitles.movie": movie, "subtitles.tv": tv})

    def test_repeated_avchange_opens_only_once_per_fingerprint(self):
        builtins = []
        clock = Clock()
        search = AutoSubtitleSearch(
            self._rpc(),
            builtins.append,
            clock=clock,
            cooldown_seconds=10,
        )
        first = search.consider("film-1", "movie", False)
        second = search.consider("film-1", "movie", False)
        clock.value += 100
        third = search.consider("film-1", "movie", False)

        self.assertEqual(first.status, "opened")
        self.assertEqual(second.status, "already_attempted")
        self.assertEqual(third.status, "already_attempted")
        self.assertEqual(builtins, [ACTIVATE_SUBTITLE_SEARCH])

    def test_existing_text_file_prevents_search_but_does_not_poison_item(self):
        builtins = []
        search = AutoSubtitleSearch(self._rpc(), builtins.append, cooldown_seconds=0)
        available = search.consider("film-2", "movie", True)
        missing_later = search.consider("film-2", "movie", False)
        self.assertEqual(available.status, "text_available")
        self.assertEqual(missing_later.status, "opened")
        self.assertEqual(builtins, [ACTIVATE_SUBTITLE_SEARCH])

    def test_cooldown_delays_new_item_without_marking_it_attempted(self):
        builtins = []
        clock = Clock()
        search = AutoSubtitleSearch(
            self._rpc(),
            builtins.append,
            clock=clock,
            cooldown_seconds=30,
        )
        self.assertEqual(search.consider("film-a", "movie", False).status, "opened")
        clock.value += 5
        waiting = search.consider("film-b", "movie", False)
        self.assertEqual(waiting.status, "cooldown")
        self.assertAlmostEqual(waiting.retry_after, 25.0)
        self.assertNotIn("film-b", search.attempted_fingerprints)

        clock.value += 25
        self.assertEqual(search.consider("film-b", "movie", False).status, "opened")
        self.assertEqual(builtins, [ACTIVATE_SUBTITLE_SEARCH] * 2)

    def test_missing_default_service_is_reported_once_and_never_opens(self):
        builtins = []
        notices = []
        search = AutoSubtitleSearch(self._rpc(movie=""), builtins.append, notices.append)
        first = search.consider("film-3", "movie", False)
        second = search.consider("film-3", "movie", False)
        self.assertEqual(first.status, "no_service")
        self.assertEqual(second.status, "already_attempted")
        self.assertEqual(builtins, [])
        self.assertEqual(len(notices), 1)

    def test_builtin_failure_cannot_create_retry_loop(self):
        calls = []

        def broken_builtin(command):
            calls.append(command)
            raise RuntimeError("Kodi stopped")

        search = AutoSubtitleSearch(self._rpc(), broken_builtin, cooldown_seconds=0)
        first = search.consider("film-4", "movie", False)
        second = search.consider("film-4", "movie", False)
        self.assertEqual(first.status, "builtin_error")
        self.assertEqual(second.status, "already_attempted")
        self.assertEqual(calls, [ACTIVATE_SUBTITLE_SEARCH])


if __name__ == "__main__":
    unittest.main()
