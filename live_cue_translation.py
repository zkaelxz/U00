"""Translating Live cues one at a time, with the context a stream needs.

Live sends each cue alone, so without help the prompt would carry no source
language, no medium and no memory of what was just said. This keeps the last
few finished cues and passes them as continuity context."""
from collections import deque

import background_jobs

# Enough to resolve a pronoun or a running topic; more only slows a local model.
RECENT_CUES = 4
# A cue is a spoken sentence, but a run-on ASR segment shouldn't bloat every prompt.
_RECENT_CHARS = 200


def build_live_context(source_language: str, recent, line_id: int,
                       reply_without_thinking: bool) -> dict:
    """The translate_batch context for one cue. A non-baihe genre keeps the
    prompt from calling every stream baihe; recent is [(source, translation)]."""
    return {
        "drama_meta": {"source_language": source_language, "content_mode": "streamer_vod",
                       "genre": "livestream"},
        "recent_context": list(recent),
        "line_ids": [line_id],
        "reply_without_thinking": reply_without_thinking,
    }


class CueTranslator:
    def __init__(self, engine, source_language: str, reply_without_thinking: bool = True):
        self.engine = engine
        self.source_language = source_language
        self.reply_without_thinking = reply_without_thinking
        self._recent = deque(maxlen=RECENT_CUES)
        self._count = 0

    def translate(self, text: str) -> str:
        """The translation, or a "[translation failed: ...]" note the feed shows
        in its place. A cancel raises JobCancelled. Only a finished translation
        joins the context: a failure note must not reach the model as one."""
        from translate_engines import TranslationCancelled, redact_secrets
        self._count += 1
        context = build_live_context(self.source_language, self._recent, self._count,
                                     self.reply_without_thinking)
        try:
            translated = self.engine.translate_batch([text], context)[0]
        except TranslationCancelled:
            raise background_jobs.JobCancelled("live translate") from None
        except Exception as exc:
            return f"[translation failed: {redact_secrets(str(exc))}]"
        if translated and translated.strip():
            self._recent.append((text[:_RECENT_CHARS], translated[:_RECENT_CHARS]))
        return translated
