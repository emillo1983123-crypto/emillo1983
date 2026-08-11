"""Find the text subtitle file selected by Kodi and return its current cue."""

from __future__ import unicode_literals

import math
import os
import re
import time
import urllib.parse

import xbmcvfs

from subtitle_parser import parse_subtitle


EXTENSIONS = (".srt", ".vtt", ".ass", ".ssa", ".sub")
LANGUAGE_HINTS = (".pl.", ".pol.", " polish", " polski", "pl_pl")
TEMP_START_SLOP_SECONDS = 2.0
RELEASE_MARKER_RE = re.compile(
    r"^(?:19\d{2}|20\d{2}|\d{3,4}p|4k|uhd|web|webdl|bluray|brrip|dvdrip|hdtv|"
    r"x26[45]|h26[45]|hevc|av1|s\d{1,2}e\d{1,3})$"
)


def _join(parent, child):
    if parent.endswith(("/", "\\")):
        return parent + child
    separator = "/" if "://" in parent or parent.startswith("special:") else os.sep
    return parent + separator + child


def _read_file(path):
    handle = xbmcvfs.File(path, "rb")
    try:
        if hasattr(handle, "readBytes"):
            return handle.readBytes()
        return handle.read()
    finally:
        handle.close()


def _local_stat(path):
    try:
        translated = xbmcvfs.translatePath(path)
        stat = os.stat(translated)
        return stat.st_mtime, stat.st_size
    except OSError:
        return 0.0, 0


def _normal_stem(path):
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    stem = os.path.splitext(name)[0].casefold()
    stem = re.sub(r"(?:[._ -](?:pl|pol|polish|forced|sdh|cc))+$", "", stem)
    return re.sub(r"[^a-z0-9ąćęłńóśźż]+", " ", stem).strip()


def _stem_matches(playing_stem, candidate_stem):
    if not playing_stem or not candidate_stem:
        return False
    if playing_stem == candidate_stem:
        return True
    shorter, longer = sorted((playing_stem, candidate_stem), key=len)
    if len(shorter) < 6 or not longer.startswith(shorter + " "):
        return False
    suffix = longer[len(shorter) + 1 :].split()
    # A shorter basename may omit technical release tags, but it must not
    # match a sequel or another title merely because it is a prefix.
    return bool(suffix) and bool(RELEASE_MARKER_RE.match(suffix[0]))


def _filename_tokens(path):
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    stem = os.path.splitext(name)[0].casefold()
    return set(value for value in re.split(r"[^a-z0-9]+", stem) if value)


def _duration_score(track, total_time):
    if not track or not getattr(track, "cues", None) or total_time <= 0:
        return 0.0
    ends = []
    for cue in track.cues:
        try:
            value = float(cue.end)
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            ends.append(value)
    if not ends:
        return 0.0
    last_end = max(ends)
    difference = abs(last_end - total_time)
    ratio = last_end / total_time
    if difference <= max(90.0, total_time * 0.04):
        return 100.0
    if difference <= max(240.0, total_time * 0.12):
        return 55.0
    if ratio < 0.45 or ratio > 1.55:
        return -180.0
    if difference > total_time * 0.30:
        return -90.0
    return 0.0


class SubtitleSource:
    def __init__(self, player, logger=None):
        self.player = player
        self.logger = logger or (lambda message: None)
        self.playing_file = ""
        self.stream_name = ""
        self.started_at = time.time()
        self.last_scan = 0.0
        self.selected_path = ""
        self.track_cache = {}
        self.candidates = []

    def reset(self, playing_file=""):
        self.playing_file = playing_file or ""
        self.stream_name = ""
        self.started_at = time.time()
        self.last_scan = 0.0
        self.selected_path = ""
        self.track_cache.clear()
        self.candidates = []

    def _roots(self):
        roots = ["special://temp/"]
        playing = self.playing_file
        if playing and not playing.casefold().startswith(("http://", "https://", "plugin://", "pvr://")):
            directory = playing.rsplit("/", 1)[0] if "/" in playing else os.path.dirname(playing)
            if directory:
                roots.insert(0, directory)
        return roots

    def _walk(self, root, depth=0, budget=None):
        if budget is None:
            budget = [500]
        if depth > 3 or budget[0] <= 0:
            return []
        found = []
        try:
            directories, files = xbmcvfs.listdir(root)
        except Exception:
            return found
        for name in files:
            if budget[0] <= 0:
                break
            budget[0] -= 1
            if os.path.splitext(name.casefold())[1] in EXTENSIONS:
                found.append(_join(root, name))
        for name in directories:
            if budget[0] <= 0:
                break
            folded = name.casefold()
            if folded in ("archive_cache", "packages", "thumbnails", "cache"):
                continue
            found.extend(self._walk(_join(root, name), depth + 1, budget))
        return found

    def _stream_candidate(self):
        try:
            self.stream_name = self.player.getSubtitles() or ""
        except Exception:
            self.stream_name = ""
        value = self.stream_name.strip()
        if value.casefold().endswith(EXTENSIONS) and xbmcvfs.exists(value):
            return value
        return ""

    def _load_track(self, path):
        signature = _local_stat(path)
        cached = self.track_cache.get(path)
        if cached and cached[0] == signature:
            return cached[1]
        try:
            track = parse_subtitle(_read_file(path), path)
        except Exception as exc:
            self.logger("Nie można odczytać napisów %s: %s" % (path, exc))
            return None
        self.track_cache[path] = (signature, track)
        return track

    def _player_total_time(self):
        try:
            value = float(self.player.getTotalTime())
        except (AttributeError, TypeError, ValueError, RuntimeError):
            return 0.0
        return value if math.isfinite(value) and value > 0 else 0.0

    def _score(self, path, seconds, direct="", total_time=0.0):
        track = self._load_track(path)
        if not track or not track.cues:
            return -1, track
        score = 10.0
        folded = (" " + path.casefold() + " ")
        playing_stem = _normal_stem(self.playing_file)
        candidate_stem = _normal_stem(path)
        matching_stem = _stem_matches(playing_stem, candidate_stem)
        direct_match = bool(direct) and path.casefold() == direct.casefold()
        modified, size = _local_stat(path)
        is_temp = path.casefold().startswith("special://temp")
        fresh_temp = bool(modified) and modified >= self.started_at - TEMP_START_SLOP_SECONDS

        # Never borrow an unrelated subtitle from another film in the same
        # directory. A non-matching temp file is eligible only when it was
        # created for the current playback session by Kodi's subtitle service.
        if not direct_match and not matching_stem and not (is_temp and fresh_temp):
            return -1, track

        if matching_stem:
            score += 300
        tokens = _filename_tokens(path)
        if tokens.intersection(("pl", "pol", "polish", "polski", "pl_pl")) or any(
            hint in folded for hint in LANGUAGE_HINTS
        ):
            score += 65
        special = tokens.intersection(("forced", "sdh", "cc", "hi", "hearingimpaired"))
        if "forced" in special:
            score -= 110
        if special.intersection(("sdh", "cc", "hi", "hearingimpaired")):
            score -= 45
        if not special:
            score += 25
        if self.stream_name and self.stream_name.casefold() in folded:
            score += 80
        if track.at(seconds):
            score += 120
        score += _duration_score(track, total_time)
        age = max(0.0, time.time() - modified) if modified else 86400.0
        score += max(0.0, 50.0 - age / 120.0)
        if is_temp and fresh_temp:
            score += 120
        if path.casefold().endswith((".srt", ".vtt")):
            score += 5
        # A filename-matching sidecar remains usable even when its duration or
        # accessibility tags make it a weak candidate.
        if matching_stem:
            score = max(score, 1.0)
        if direct_match:
            score += 10000
        return score, track

    def _scan(self, seconds):
        direct = self._stream_candidate()
        total_time = self._player_total_time()
        paths = [direct] if direct else []
        for root in self._roots():
            paths.extend(self._walk(root))
        unique = []
        seen = set()
        for path in paths:
            key = path.casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        self.candidates = unique
        best_score = -1.0
        best_path = ""
        for path in unique:
            score, track = self._score(path, seconds, direct, total_time)
            if score > best_score:
                best_score = score
                best_path = path
        if best_path != self.selected_path:
            self.selected_path = best_path
            if best_path:
                self.logger("Wybrano napisy: %s" % best_path)
        self.last_scan = time.monotonic()

    def _track_at(self, seconds):
        now = time.monotonic()
        if not self.selected_path or now - self.last_scan >= 4.0:
            self._scan(seconds)
        track = self._load_track(self.selected_path) if self.selected_path else None
        if track and track.at(seconds):
            return track
        # A new subtitle file can appear several seconds after playback starts.
        if now - self.last_scan >= 1.0:
            self._scan(seconds)
            track = self._load_track(self.selected_path) if self.selected_path else None
        return track

    def cues_at(self, seconds):
        """Return a stable source key and the currently active cue contexts."""
        track = self._track_at(seconds)
        if not track or not self.selected_path:
            return None, []
        modified, size = _local_stat(self.selected_path)
        source_key = (self.selected_path, modified, size)
        return source_key, track.active(seconds)

    def selected_track(self, seconds=None, source_key=None):
        """Return the complete currently selected text track.

        ``seconds`` lets callers such as the executable add-on perform the
        normal scan before asking for a track.  The optional ``source_key``
        protects long-running background work from accidentally estimating a
        subtitle file that Kodi replaced between two polling iterations.
        """

        if seconds is not None:
            track = self._track_at(seconds)
        elif self.selected_path:
            track = self._load_track(self.selected_path)
        else:
            track = None
        if not track or not self.selected_path:
            return None
        if source_key is not None:
            modified, size = _local_stat(self.selected_path)
            if source_key != (self.selected_path, modified, size):
                return None
        return track

    def text_at(self, seconds):
        track = self._track_at(seconds)
        return track.at(seconds) if track else ""
