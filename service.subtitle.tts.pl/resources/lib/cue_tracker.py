"""Track subtitle cue identities independently from their visible text."""

from __future__ import unicode_literals

from dataclasses import dataclass


@dataclass(frozen=True)
class CueBatch:
    cue_ids: tuple
    text: str
    previous_text: str = ""
    next_text: str = ""


class ActiveCueTracker:
    """Return each cue once while allowing deliberate resets after seeks."""

    def __init__(self):
        self.source_key = None
        self.seen = set()

    def reset(self, source_key=None):
        self.source_key = source_key
        self.seen.clear()

    def use_source(self, source_key):
        if source_key == self.source_key:
            return False
        self.reset(source_key)
        return True

    def _identity(self, context):
        cue = context.cue
        return (
            self.source_key,
            context.index,
            round(cue.start, 3),
            round(cue.end, 3),
            cue.text,
        )

    def observe(self, contexts):
        fresh = []
        cue_ids = []
        for context in contexts:
            cue_id = self._identity(context)
            if cue_id in self.seen:
                continue
            self.seen.add(cue_id)
            cue_ids.append(cue_id)
            fresh.append(context)
        if not fresh:
            return None

        texts = []
        for context in fresh:
            text = context.cue.text
            if text and text not in texts:
                texts.append(text)
        if not texts:
            return None
        return CueBatch(
            tuple(cue_ids),
            " … ".join(texts),
            fresh[0].previous_text,
            fresh[-1].next_text,
        )
