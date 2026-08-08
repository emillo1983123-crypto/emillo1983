"""Parsers for text subtitle formats used by Kodi."""

from __future__ import unicode_literals

import bisect
import html
import os
import re
from dataclasses import dataclass
from typing import List


TAG_RE = re.compile(r"<[^>]+>|\{\\[^}]*\}")
SPACE_RE = re.compile(r"\s+")
SOUND_ONLY_RE = re.compile(r"^\s*(?:\[[^]]+\]|\([^)]*\)|[♪♫\s]+)\s*$")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


class SubtitleTrack:
    def __init__(self, cues, source=""):
        self.cues = sorted(cues, key=lambda cue: (cue.start, cue.end))
        self.starts = [cue.start for cue in self.cues]
        self.source = source

    def at(self, seconds):
        if not self.cues:
            return ""
        index = bisect.bisect_right(self.starts, seconds) - 1
        if index < 0:
            return ""
        texts = []
        for cue in self.cues[max(0, index - 12) : index + 1]:
            if cue.start <= seconds <= cue.end and cue.text not in texts:
                texts.append(cue.text)
        return " ".join(texts)


def clean_text(value):
    value = value.replace("\\N", " ").replace("\\n", " ").replace("|", " ")
    value = html.unescape(TAG_RE.sub("", value))
    value = SPACE_RE.sub(" ", value).strip(" \t\r\n-")
    if not value or SOUND_ONLY_RE.match(value):
        return ""
    return value


def decode_subtitle(data):
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "iso-8859-2", "cp1252"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", "replace")


def parse_clock(value):
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None
    return None


def parse_srt_or_vtt(text):
    cues = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    for block in blocks:
        lines = [line.strip("\ufeff") for line in block.split("\n") if line.strip()]
        timing_index = -1
        for index, line in enumerate(lines[:3]):
            if "-->" in line:
                timing_index = index
                break
        if timing_index < 0:
            continue
        timing = lines[timing_index].split("-->", 1)
        start = parse_clock(timing[0].split()[0])
        end = parse_clock(timing[1].strip().split()[0])
        spoken = clean_text(" ".join(lines[timing_index + 1 :]))
        if start is not None and end is not None and end >= start and spoken:
            cues.append(Cue(start, end, spoken))
    return cues


def parse_ass(text):
    cues = []
    in_events = False
    fields = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if line.startswith("["):
            in_events = line.casefold() == "[events]"
            continue
        if not in_events:
            continue
        if line.casefold().startswith("format:"):
            fields = [item.strip().casefold() for item in line.split(":", 1)[1].split(",")]
            continue
        if not line.casefold().startswith("dialogue:"):
            continue
        body = line.split(":", 1)[1].lstrip()
        values = body.split(",", max(0, len(fields) - 1))
        if len(values) < len(fields):
            continue
        record = dict(zip(fields, values))
        start = parse_clock(record.get("start", ""))
        end = parse_clock(record.get("end", ""))
        spoken = clean_text(record.get("text", ""))
        if start is not None and end is not None and end >= start and spoken:
            cues.append(Cue(start, end, spoken))
    return cues


def parse_microdvd(text, fps=25.0):
    cues = []
    pattern = re.compile(r"^\{(\d+)\}\{(\d+)\}(.*)$")
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        start_frame, end_frame, value = match.groups()
        if start_frame == "1" and end_frame == "1":
            try:
                fps = float(value.replace(",", "."))
                continue
            except ValueError:
                pass
        spoken = clean_text(value)
        if spoken and fps > 0:
            cues.append(Cue(int(start_frame) / fps, int(end_frame) / fps, spoken))
    return cues


def parse_subtitle(data, filename=""):
    text = decode_subtitle(data)
    suffix = os.path.splitext(filename.casefold())[1]
    if suffix in (".ass", ".ssa") or "[events]" in text.casefold():
        cues = parse_ass(text)
    elif suffix == ".sub" and re.search(r"^\{\d+\}\{\d+\}", text, re.MULTILINE):
        cues = parse_microdvd(text)
    else:
        cues = parse_srt_or_vtt(text)
    return SubtitleTrack(cues, filename)

