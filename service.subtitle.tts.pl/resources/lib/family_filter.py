"""Deterministic, family-friendly substitutions for spoken subtitles."""

from __future__ import unicode_literals

import re
import unicodedata


FLAGS = re.IGNORECASE | re.UNICODE


def _rules(items):
    return tuple((re.compile(pattern, FLAGS), replacement) for pattern, replacement in items)


# Longer phrases run before individual words so the spoken result remains natural.
STRONG_PHRASES = _rules(
    (
        (r"(?<!\w)co\s+do\s+cholery(?!\w)", "co u licha"),
        (r"(?<!\w)do\s+(?:jasnej\s+)?cholery(?!\w)", "do licha"),
        (r"(?<!\w)jasna\s+cholera(?!\w)", "ojej"),
        (r"(?<!\w)kurw+a\s+mać(?!\w)", "kurczę"),
        (r"(?<!\w)ja\s+pierdolę(?!\w)", "o rany"),
        (r"(?<!\w)mam\s+(?:to\s+)?w\s+dupie(?!\w)", "nie obchodzi mnie to"),
        (r"(?<!\w)do\s+dupy(?!\w)", "do bani"),
        (r"(?<!\w)gówno\s+prawda(?!\w)", "nieprawda"),
        (r"(?<!\w)(?:odpierdol|odpieprz)\s+się(?!\w)", "odczep się"),
        (r"(?<!\w)(?:odpierdolcie|odpieprzcie)\s+się(?!\w)", "odczepcie się"),
        (r"(?<!\w)(?:pierdol|pieprz)\s+się(?!\w)", "daj spokój"),
        (r"(?<!\w)(?:jebać|pieprzyć)\s+to(?!\w)", "dać temu spokój"),
        (r"(?<!\w)(?:s|wy)pierdalajcie(?!\w)", "odejdźcie"),
        (r"(?<!\w)(?:s|wy)pierdalaj(?!\w)", "idź stąd"),
        (r"(?<!\w)shut\s+(?:the\s+)?fuck\s+up(?!\w)", "bądź cicho"),
        (r"(?<!\w)go\s+fuck\s+yourself(?!\w)", "daj mi spokój"),
        (r"(?<!\w)fuck\s+off(?!\w)", "idź stąd"),
        (r"(?<!\w)(?:oh|holy)\s+shit(?!\w)", "ojej"),
        (r"(?<!\w)son\s+of\s+a\s+bitch(?!\w)", "drań"),
        (r"(?<!\w)piece\s+of\s+shit(?!\w)", "bubel"),
        (r"(?<!\w)what\s+the\s+(?:fuck|hell)(?!\w)", "co u licha"),
        (r"(?<!\w)piss\s+off(?!\w)", "idź stąd"),
        (r"(?<!\w)screw\s+you(?!\w)", "daj spokój"),
        (r"(?<!\w)bloody\s+hell(?!\w)", "ojej"),
        (r"(?<!\w)damn\s+it(?!\w)", "kurczę"),
    )
)

NATURAL_FORMS = _rules(
    (
        (r"(?<!\w)zajebiście(?!\w)", "świetnie"),
        (r"(?<!\w)zajebiści(?!\w)", "świetni"),
        (r"(?<!\w)zajebist(y|a|e|ego|ej|emu|ym|ą|ych|ymi)(?!\w)", r"świetn\1"),
        (r"(?<!\w)gównian(y|a|e|ego|ej|emu|ym|ą|i|ych|ymi)(?!\w)", r"słab\1"),
        (r"(?<!\w)chujow(y|a|e|ego|ej|emu|ym|ą|i|ych|ymi)(?!\w)", r"słab\1"),
        (r"(?<!\w)najeban(y|a|e|ego|ej|emu|ym|ą|i|ych|ymi)(?!\w)", r"pijan\1"),
        (r"(?<!\w)(?:po)?jeban(y|a|e|ego|ej|emu|ym|ą|i|ych|ymi)(?!\w)", r"okropn\1"),
        (r"(?<!\w)(?:jeban|pierdolon)(y|a|e|ego|ej|emu|ym|ą|i|ych|ymi)(?!\w)", r"okropn\1"),
        (r"(?<!\w)pierdoleni(?!\w)", "okropni"),
        (r"(?<!\w)przejebane(?!\w)", "kiepsko"),
    )
)

STRONG_WORDS = _rules(
    (
        (r"(?<!\w)(?:skurwysyn(?:a|owi|em|ie|y|ów|om|ami|ach)?|skurw(?:iel|iela|ielowi|ielem|ielu|iele|ieli|ielom|ielami|ielach)|kurewsk(?:i|a|ie|o|iego|iej|iemu|im|ich|imi|ą)|kurw(?:a|y|ie|ę|ą|o|ami|om|ach))(?!\w)", "kurczę"),
        (r"(?<!\w)(?:(?:nie)?(?:na|po|za|wy|do|od|u|prze|roz|w|z|przy|ob|pod|nad)jeb[a-ząćęłńóśźż]*|jeb(?:ać|ię|iesz|ie|iemy|iecie|ią|ał[a-ząćęłńóśźż]*|ali[a-ząćęłńóśźż]*|ały[a-ząćęłńóśźż]*|an[a-ząćęłńóśźż]*|ani[a-ząćęłńóśźż]*|anie[a-ząćęłńóśźż]*|nąć|nę|niesz|nie|niemy|niecie|ną|nął[a-ząćęłńóśźż]*|nięt[a-ząćęłńóśźż]*))(?!\w)", "bip"),
        (r"(?<!\w)(?:(?:nie)?(?:s|od|na|wy|za|prze|po|u|do|roz|w|przy|ob)?pierd[oa]l)[a-ząćęłńóśźż]*(?!\w)", "bip"),
        (r"(?<!\w)chuj(?:a|owi|em|u|e|ów|om|ami|ach|ow(?:y|a|e|i|ego|ej|emu|ym|ych|ymi|ą))?(?!\w)", "bip"),
        (r"(?<!\w)(?:pizd(?:a|y|ę|ą|o|ami|om|ach)|piździe)(?!\w)", "bip"),
        (r"(?<!\w)gówn(?:o|a|ie|em|u|ami|ach|ian(?:y|a|e|i|ego|ej|emu|ym|ą|ych|ymi))(?!\w)", "bzdura"),
        (r"(?<!\w)sukinsyn(?:a|owi|em|ie|y|ów|om|ami|ach)?(?!\w)", "drań"),
        (r"(?<!\w)cwel(?:a|owi|em|u|e|ów|om|ami|ach)?(?!\w)", "palant"),
        (r"(?<!\w)(?:motherfuck(?:er|ers|ing)?|fuck(?:ed|er|ers|ing|in'?|s)?|bullshit|shit(?:ty|ted|ting|s|head|heads|storm|storms|show|shows|face|faces|bag|bags)?)(?!\w)", "bip"),
        (r"(?<!\w)(?:asshole|arsehole|dickhead|douchebag|wanker)s?(?!\w)", "palant"),
        (r"(?<!\w)(?:bastard|slut|whore|cunt)s?(?!\w)|(?<!\w)bitch(?:es|y)?(?!\w)", "drań"),
        (r"(?<!\w)(?:k\*{2,}a|f\*{2,}k|s\*{2,}t)(?!\w)", "bip"),
    )
)

FAMILY_WORDS = _rules(
    (
        (r"(?<!\w)dup(a|y|ie|ę|ą|o|ami|om|ach)(?!\w)", r"pup\1"),
        (r"(?<!\w)dup(ek|ka|kowi|kiem|ku|ki|ków|kom|kami|kach)(?!\w)", r"głup\1"),
        (r"(?<!\w)cholera(?!\w)", "ojej"),
        (r"(?<!\w)idiot(?:a|y|o|ę|ą|ami|om|ach)?(?!\w)", "niemądry"),
        (r"(?<!\w)debil(?:a|owi|em|u|e|i|ów|om|ami|ach)?(?!\w)", "niemądry"),
    )
)

STRICT_WORDS = _rules(
    (
        (r"(?<!\w)szmat(?:a|y|o|ę|ą|ami|om|ach)(?!\w)", "maruda"),
        (r"(?<!\w)suk(?:a|i|ę|ą|o|ami|om|ach)(?!\w)", "złośnica"),
        (r"(?<!\w)kretyn(?:a|owi|em|ie|i|ów|om|ami|ach)?(?!\w)", "niemądry"),
        (r"(?<!\w)damn(?:ed)?(?!\w)", "kurczę"),
    )
)

RESIDUAL = re.compile(
    r"(?<!\w)(?:kurw[a-ząćęłńóśźż]*|(?:s|wy)?pierdal[a-ząćęłńóśźż]*|chuj[a-ząćęłńóśźż]*|pizd[a-ząćęłńóśźż]*|motherfuck[a-z]*|fuck(?:ed|er|ers|ing|in'?|s)?|bullshit|shit(?:ty|ted|ting|s)?)(?!\w)",
    FLAGS,
)


def _apply(text, rules):
    for pattern, replacement in rules:
        text = pattern.sub(replacement, text)
    return text


def soften(text, level="family"):
    """Return text safe to speak; ``level`` is strong, family or strict."""
    if not isinstance(text, str):
        text = str(text or "")
    result = unicodedata.normalize("NFKC", text)
    result = " ".join(result.split())
    result = _apply(result, STRONG_PHRASES)
    result = _apply(result, NATURAL_FORMS)
    result = _apply(result, STRONG_WORDS)
    if level in ("family", "strict"):
        result = _apply(result, FAMILY_WORDS)
    if level == "strict":
        result = _apply(result, STRICT_WORDS)
    result = " ".join(result.split())
    if RESIDUAL.search(result):
        return "Ojej, ale zamieszanie."
    return result

