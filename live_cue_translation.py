"""Translating Live cues, a chunk per request, with the context a stream needs.

Without help the prompt would carry no source language, no medium and no memory
of what was just said. This keeps the last few finished cues and passes them as
continuity context."""
import re
from collections import deque

import background_jobs

# Enough to resolve a pronoun or a running topic; more only slows a local model.
RECENT_CUES = 4
# A cue is a spoken sentence, but a run-on ASR segment shouldn't bloat every prompt.
_RECENT_CHARS = 200


def _as_data(text: str) -> str:
    """One line with no markup: a speaker controls this text, so it must not be
    able to start a new pair line or close the block the prompt wraps it in."""
    return re.sub(r"\s+", " ", text).replace("<", "\u2039").replace(">", "\u203a").strip()


def build_live_context(source_language: str, recent, line_ids,
                       reply_without_thinking: bool) -> dict:
    """The translate_batch context for one cue's id or a chunk's ids. A non-baihe
    genre keeps the prompt from calling every stream baihe; recent is
    [(source, translation)]."""
    return {
        "drama_meta": {"source_language": source_language, "content_mode": "streamer_vod",
                       "genre": "livestream"},
        "recent_context": list(recent),
        "recent_as_data": True,
        "line_ids": list(line_ids) if isinstance(line_ids, (list, tuple)) else [line_ids],
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
        in its place. A cancel raises JobCancelled."""
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
        self._remember(text, translated)
        return translated

    def translate_chunk(self, texts: list) -> dict:
        """{position: translation} for the cues one request carried. The engine
        matches its reply to the ids (request_translations_with_retry) and hands
        back one entry per id, "" where one was missing; a reply of any other
        length, or a failed request, gives {} and the caller asks per cue."""
        from translate_engines import TranslationCancelled
        ids = list(range(self._count + 1, self._count + 1 + len(texts)))
        self._count += len(texts)
        context = build_live_context(self.source_language, self._recent, ids,
                                     self.reply_without_thinking)
        try:
            replies = self.engine.translate_batch(list(texts), context)
        except TranslationCancelled:
            raise background_jobs.JobCancelled("live translate") from None
        except Exception:
            return {}
        if not isinstance(replies, list) or len(replies) != len(ids):
            return {}
        by_id = dict(zip(ids, replies))
        found = {}
        for pos, (line_id, text) in enumerate(zip(ids, texts)):
            translated = by_id[line_id]
            if isinstance(translated, str) and translated.strip():
                found[pos] = translated
                self._remember(text, translated)
        return found

    def _remember(self, text: str, translated: str) -> None:
        """Only a finished translation joins the context: a failure note must
        not reach the model as one."""
        if translated and translated.strip():
            self._recent.append((_as_data(text)[:_RECENT_CHARS], _as_data(translated)[:_RECENT_CHARS]))
