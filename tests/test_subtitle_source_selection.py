import os
import sys
import types
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "service.subtitle.tts.pl", "resources", "lib")
sys.path.insert(0, LIB)

if "xbmcvfs" not in sys.modules:
    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.exists = lambda _path: False
    xbmcvfs.listdir = lambda _path: ([], [])
    xbmcvfs.translatePath = lambda path: path
    sys.modules["xbmcvfs"] = xbmcvfs

import subtitle_source
from subtitle_parser import Cue, SubtitleTrack
from subtitle_source import SubtitleSource


class FakePlayer:
    def __init__(self, total_time=6000.0):
        self.total_time = total_time

    def getTotalTime(self):
        return self.total_time


def track_ending_at(seconds):
    return SubtitleTrack([Cue(0.0, float(seconds), "Tekst")], "test")


class SubtitleSelectionTests(unittest.TestCase):
    def _scan(self, playing_file, paths, tracks, mtimes, direct="", total_time=6000.0):
        source = SubtitleSource(FakePlayer(total_time))
        source.playing_file = playing_file
        source.started_at = 1000.0
        source._roots = lambda: ["test-root"]
        source._walk = lambda _root: list(paths)
        source._stream_candidate = lambda: direct
        source._load_track = lambda path: tracks[path]
        original_stat = subtitle_source._local_stat
        subtitle_source._local_stat = lambda path: (mtimes.get(path, 1001.0), 100)
        try:
            source._scan(30.0)
        finally:
            subtitle_source._local_stat = original_stat
        return source

    def test_old_unrelated_temp_is_rejected_for_matching_sidecar(self):
        old = "special://temp/Previous.Film.pl.srt"
        matching = "smb://server/Current.Film.pl.srt"
        tracks = {old: track_ending_at(5900), matching: track_ending_at(5900)}
        source = self._scan(
            "smb://server/Current.Film.mkv",
            [old, matching],
            tracks,
            {old: 100.0, matching: 100.0},
        )
        self.assertEqual(source.selected_path, matching)

    def test_matching_stem_beats_recent_unrelated_polish_temp(self):
        matching = "smb://server/The.Matrix.1999.srt"
        unrelated = "special://temp/Another.Movie.polish.srt"
        tracks = {matching: track_ending_at(5900), unrelated: track_ending_at(5900)}
        source = self._scan(
            "smb://server/The.Matrix.1999.mkv",
            [unrelated, matching],
            tracks,
            {matching: 900.0, unrelated: 1001.0},
        )
        self.assertEqual(source.selected_path, matching)

    def test_unrelated_local_sidecar_is_rejected_so_auto_search_can_run(self):
        unrelated = "smb://server/Another.Movie.pl.srt"
        source = self._scan(
            "smb://server/Current.Film.mkv",
            [unrelated],
            {unrelated: track_ending_at(5900)},
            {unrelated: 1001.0},
        )
        self.assertEqual(source.selected_path, "")

    def test_title_prefix_does_not_match_a_sequel(self):
        cases = (
            ("smb://server/The.Matrix.Reloaded.mkv", "smb://server/The.Matrix.pl.srt"),
            ("smb://server/Avatar.2.mkv", "smb://server/Avatar.pl.srt"),
        )
        for playing, candidate in cases:
            with self.subTest(playing=playing):
                source = self._scan(
                    playing,
                    [candidate],
                    {candidate: track_ending_at(5900)},
                    {candidate: 1001.0},
                )
                self.assertEqual(source.selected_path, "")

    def test_shorter_sidecar_can_omit_recognized_release_suffix(self):
        candidate = "smb://server/Movie.Title.pl.srt"
        source = self._scan(
            "smb://server/Movie.Title.2024.1080p.WEB-DL.mkv",
            [candidate],
            {candidate: track_ending_at(5900)},
            {candidate: 900.0},
        )
        self.assertEqual(source.selected_path, candidate)

    def test_temp_file_from_previous_playback_is_rejected_even_when_recent(self):
        previous = "special://temp/Previous.Film.pl.srt"
        source = self._scan(
            "plugin://video.example/play/42",
            [previous],
            {previous: track_ending_at(5900)},
            {previous: 950.0},
        )
        self.assertEqual(source.selected_path, "")

    def test_temp_file_created_for_current_playback_is_accepted(self):
        downloaded = "special://temp/downloaded.pl.srt"
        source = self._scan(
            "plugin://video.example/play/42",
            [downloaded],
            {downloaded: track_ending_at(5900)},
            {downloaded: 1001.0},
        )
        self.assertEqual(source.selected_path, downloaded)

    def test_matching_sidecar_is_never_discarded_by_other_penalties(self):
        matching = "smb://server/Movie.forced.sdh.srt"
        source = self._scan(
            "smb://server/Movie.mkv",
            [matching],
            {matching: track_ending_at(10)},
            {matching: 100.0},
        )
        self.assertEqual(source.selected_path, matching)

    def test_duration_match_beats_grossly_wrong_duration(self):
        matching_duration = "special://temp/candidate-a.pl.srt"
        wrong_duration = "special://temp/candidate-b.pl.srt"
        tracks = {
            matching_duration: track_ending_at(5900),
            wrong_duration: track_ending_at(2400),
        }
        source = self._scan(
            "plugin://video.example/play/42",
            [wrong_duration, matching_duration],
            tracks,
            {matching_duration: 1001.0, wrong_duration: 1001.0},
        )
        self.assertEqual(source.selected_path, matching_duration)

    def test_polish_full_subtitles_beat_forced_sdh_variant(self):
        full = "smb://server/Movie.pl.srt"
        limited = "smb://server/Movie.pl.forced.sdh.srt"
        tracks = {full: track_ending_at(5900), limited: track_ending_at(5900)}
        source = self._scan(
            "smb://server/Movie.mkv",
            [limited, full],
            tracks,
            {full: 900.0, limited: 1001.0},
        )
        self.assertEqual(source.selected_path, full)

    def test_direct_current_subtitle_always_wins(self):
        direct = "special://temp/Unrelated.forced.sdh.srt"
        matching = "smb://server/Movie.pl.srt"
        tracks = {direct: track_ending_at(1200), matching: track_ending_at(5900)}
        source = self._scan(
            "smb://server/Movie.mkv",
            [matching],
            tracks,
            {direct: 100.0, matching: 1001.0},
            direct=direct,
        )
        self.assertEqual(source.selected_path, direct)


if __name__ == "__main__":
    unittest.main()
