"""Prepare subtitle text for natural speech without reading decorations."""

from __future__ import unicode_literals

import re
import unicodedata


FLAGS = re.IGNORECASE | re.UNICODE
SPACE_RE = re.compile(r"\s+")
ELLIPSIS_RE = re.compile(r"(?:\.\s*){2,}")
MIXED_EMPHASIS_RE = re.compile(r"[!?](?:\s*[!?])+")
REPEATED_PAUSE_RE = re.compile(r"([,;:])(?:\s*\1)+")
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"\s+([,.;:!?…])")
DIALOG_MARKER_RE = re.compile(r"(?<!\S)[\-–—]{1,2}(?=\s)")
DECORATION_RE = re.compile(r"[\*#_=~^|<>]+")
QUOTE_RE = re.compile(r"[\"“”„‟«»‹›]")
BRACKET_RE = re.compile(r"[\[\]{}()]")
MUSIC_RE = re.compile(r"[♪♫♬♩]+")
ARROW_AND_BULLET_RE = re.compile(r"[•◦▪▫►▶◆◇★☆→←↑↓↔]+")
FILLER_RE = re.compile(r"(?<!\w)(?:y{2,}|e{3,}|uh+|um+|h+m{2,})(?!\w)", FLAGS)
STUTTER_RE = re.compile(r"\b([^\W\d_]{1,3})-\1([^\W\d_]*)\b", FLAGS)
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
REPEAT_SEPARATOR_RE = re.compile(r"[\s,;:–—-]+", re.UNICODE)
LEADING_PAUSE_RE = re.compile(r"^[\s,;:…–—-]+")
PAUSE_BEFORE_END_RE = re.compile(r"[,;:]+(?=[.!?…])")


PROTECTED_REPEAT_WORDS = frozenset(
    (
        "nie",
        "nigdy",
        "nikt",
        "nic",
        "żaden",
        "żadna",
        "żadne",
        "bez",
        "not",
        "no",
        "never",
        "nobody",
        "nothing",
    )
)
PROTECTED_LABELS = frozenset(("NIE", "TAK", "UWAGA", "WAŻNE", "STOP", "POMOC"))


SDH_TERMS = (
    r"muzyk(?:a|i|ę|ą)",
    r"music",
    r"śmiech|śmieje\s+się|chichot",
    r"laugh(?:s|ing|ter)?|giggles?",
    r"westchnienie|wzdycha",
    r"sighs?|sighing",
    r"płacz|płacze|szloch",
    r"cries|crying|sobs?|sobbing",
    r"oklaski|brawa|applause",
    r"krzyk|krzyczy|screams?|shouts?|yells?",
    r"szept|szeptem|szepcze|whispers?|whispering",
    r"pukanie|knocks?|knocking",
    r"dzwonek|dzwoni|rings?|ringing",
    r"drzwi|door",
    r"telefon|phone",
    r"huk|hałas|odgłos(?:y)?|noise|sound",
)
SDH_TERM_RE = "(?:%s)" % "|".join(SDH_TERMS)
INLINE_SQUARE_SDH_RE = re.compile(
    r"\[\s*(?=[^\]\r\n]{1,80}\])(?=[^\]\r\n]*\b%s\b)[^\]\r\n]*\]" % SDH_TERM_RE,
    FLAGS,
)
INLINE_ROUND_SDH_RE = re.compile(
    r"\(\s*(?=[^)\r\n]{1,80}\))(?=[^)\r\n]*\b%s\b)[^)\r\n]*\)" % SDH_TERM_RE,
    FLAGS,
)


def _without_controls(value):
    characters = []
    for character in value:
        if character.isspace():
            characters.append(" ")
            continue
        category = unicodedata.category(character)
        if category in ("Cc", "Cf", "Cs"):
            continue
        characters.append(character)
    return "".join(characters)


def _collapse_emphasis(match):
    return "?" if "?" in match.group(0) else "!"


def _without_speaker_label(value):
    match = re.match(r"^([^:\r\n]{2,32}):\s+(.+)$", value)
    if not match:
        return value
    label = match.group(1).strip()
    letters = [character for character in label if character.isalpha()]
    if not letters or not all(character.isupper() for character in letters):
        return value
    if label.upper() in PROTECTED_LABELS:
        return value
    return match.group(2).strip()


def _at_sentence_start(value, position):
    prefix = value[:position].rstrip()
    return not prefix or prefix[-1] in ".!?…"


def _repeat_is_protected(value, matches):
    for match in matches:
        token = match.group(0)
        if any(character.isdigit() for character in token):
            return True
        if token.casefold() in PROTECTED_REPEAT_WORDS:
            return True
        first_letter = next((character for character in token if character.isalpha()), "")
        if first_letter.isupper() and not _at_sentence_start(value, match.start()):
            return True
    return False


def _without_direct_repetitions(value):
    """Remove adjacent duplicate words or phrases of at most four words."""
    result = value
    while True:
        matches = list(WORD_RE.finditer(result))
        removed = False
        for width in range(min(4, len(matches) // 2), 0, -1):
            for index in range(0, len(matches) - (2 * width) + 1):
                first = matches[index : index + width]
                second = matches[index + width : index + (2 * width)]
                if [item.group(0).casefold() for item in first] != [
                    item.group(0).casefold() for item in second
                ]:
                    continue
                separator = result[first[-1].end() : second[0].start()]
                if not REPEAT_SEPARATOR_RE.fullmatch(separator):
                    continue
                if _repeat_is_protected(result, first + second):
                    continue
                result = result[: first[-1].end()] + result[second[-1].end() :]
                removed = True
                break
            if removed:
                break
        if not removed:
            return result


def normalize_for_speech(value):
    """Return idempotent, speech-ready text while preserving natural pauses."""
    if not isinstance(value, str):
        value = str(value or "")
    result = unicodedata.normalize("NFKC", value)
    result = _without_controls(result)
    result = INLINE_SQUARE_SDH_RE.sub(" ", result)
    result = INLINE_ROUND_SDH_RE.sub(" ", result)
    result = MUSIC_RE.sub(" ", result)
    result = ARROW_AND_BULLET_RE.sub(" ", result)
    result = DIALOG_MARKER_RE.sub(" ", result)
    result = DECORATION_RE.sub(" ", result)
    result = QUOTE_RE.sub("", result)
    result = BRACKET_RE.sub(" ", result)
    result = ELLIPSIS_RE.sub("… ", result)
    result = MIXED_EMPHASIS_RE.sub(_collapse_emphasis, result)
    result = REPEATED_PAUSE_RE.sub(r"\1", result)
    result = SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", result)
    result = SPACE_RE.sub(" ", result).strip()
    if not any(character.isalnum() for character in result):
        return ""
    return result


def compress_for_economy(value):
    """Shorten speech locally while retaining semantic subtitle content."""
    result = normalize_for_speech(value)
    if not result:
        return ""
    result = _without_speaker_label(result)
    result = FILLER_RE.sub(" ", result)
    result = STUTTER_RE.sub(r"\1\2", result)
    result = normalize_for_speech(result)
    result = PAUSE_BEFORE_END_RE.sub("", result)
    result = LEADING_PAUSE_RE.sub("", result)
    result = _without_direct_repetitions(result)
    result = PAUSE_BEFORE_END_RE.sub("", result)
    result = LEADING_PAUSE_RE.sub("", result)
    return normalize_for_speech(result)
