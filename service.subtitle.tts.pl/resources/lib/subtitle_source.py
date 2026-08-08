"""Find the text subtitle file selected by Kodi and return its current cue."""

from __future__ import unicode_literals

import os
import re
import time
import urllib.parse

import xbmcvfs

from subtitle_parser import parse_subtitle


EXTENSIONS = (".srt", ".vtt", ".ass", ".ssa", ".sub")
LANGUAGE_HINTS = (".pl.", ".pol.", " polish", " polski", "pl_pl")


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

    def _score(self, path, seconds):
        track = self._load_track(path)
        if not track or not track.cues:
            return -1, track
        score = 0.0
        folded = (" " + path.casefold() + " ")
        playing_stem = _normal_stem(self.playing_file)
        candidate_stem = _normal_stem(path)
        if playing_stem and candidate_stem and (playing_stem in candidate_stem or candidate_stem in playing_stem):
            score += 180
        if any(hint in folded for hint in LANGUAGE_HINTS):
            score += 35
        if self.stream_name and self.stream_name.casefold() in folded:
            score += 80
        if track.at(seconds):
            score += 120
        modified, size = _local_stat(path)
        age = max(0.0, time.time() - modified) if modified else 86400.0
        score += max(0.0, 50.0 - age / 120.0)
        if path.startswith("special://temp") and modified >= self.started_at - 120:
            score += 100
        if path.casefold().endswith((".srt", ".vtt")):
            score += 5
        return score, track

    def _scan(self, seconds):
        direct = self._stream_candidate()
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
            score, track = self._score(path, seconds)
            if direct and path == direct:
                score += 500
            if score > best_score:
                best_score = score
                best_path = path
        if best_path and best_path != self.selected_path:
            self.selected_path = best_path
            self.logger("Wybrano napisy: %s" % best_path)
        self.last_scan = time.monotonic()

    def text_at(self, seconds):
        now = time.monotonic()
        if not self.selected_path or now - self.last_scan >= 4.0:
            self._scan(seconds)
        track = self._load_track(self.selected_path) if self.selected_path else None
        if track:
            text = track.at(seconds)
            if text:
                return text
        # A new subtitle file can appear several seconds after playback starts.
        if now - self.last_scan >= 1.0:
            self._scan(seconds)
            track = self._load_track(self.selected_path) if self.selected_path else None
            if track:
                return track.at(seconds)
        return ""

