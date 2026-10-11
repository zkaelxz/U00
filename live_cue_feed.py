"""Live cues shown in two steps: the transcript as soon as Whisper returns,
then the translation filled into the same cue.

A cue's id is its position in the session's list, which only ever grows, so a
client can match an update to the line it already shows by id and never by
position in a reply. Cues are never trimmed or edited once done, so a
client only re-reads cues still pending. Each cue carries its own translation state so one slow
or failed line never hides the others."""
import contextlib

import background_jobs

PENDING, DONE, FAILED, CANCELLED = "pending", "done", "failed", "cancelled"
# Never sent for translation: the translation queue was full (live_pipeline).
SKIPPED = "skipped"
# The note CueTranslator puts in place of a translation it could not get.
FAILED_PREFIX = "[translation failed"


def pending_cues(segments, offset: float, first_id: int) -> list:
    """One untranslated cue per non-blank segment, ids from first_id on."""
    cues = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if text:
            cues.append({"id": first_id + len(cues), "start": offset + seg["start"],
                         "end": offset + seg["end"], "text": text, "translated": "",
                         "translation": PENDING})
    return cues


def snapshot(settled: list, chunk: list) -> list:
    """Copies for the job result: the feed reads it while the next update is
    being written, so it must never see a cue change under it."""
    return [dict(c) for c in settled + chunk]


def _finish(cue: dict, translated: str) -> None:
    cue["translated"] = translated
    cue["translation"] = FAILED if translated.startswith(FAILED_PREFIX) else DONE


def translate_cues(cues: list, translator, checkpoint, on_stage=None, on_cues=None,
                   lock=None) -> None:
    """Translates the chunk's cues in one id-keyed request, then one request per
    cue only for those the reply left out or that failed, calling on_cues(cues)
    after each change so the transcript is already visible while the rest wait.
    A cancel leaves the unfinished cues marked cancelled, published, and is
    re-raised. lock: held while cues change and are published, for a reader on
    another thread."""
    guard = lock if lock is not None else contextlib.nullcontext()

    def publish():
        if on_cues:
            on_cues(cues)
    with guard:
        publish()
    try:
        if len(cues) > 1:
            if on_stage:
                on_stage("translating")
            found = translator.translate_chunk([c["text"] for c in cues])
            if found:
                with guard:
                    for pos, translated in found.items():
                        _finish(cues[pos], translated)
                    publish()
        for cue in cues:
            if cue["translation"] != PENDING:
                continue
            checkpoint()
            if on_stage:
                on_stage("translating")
            translated = translator.translate(cue["text"])
            with guard:
                _finish(cue, translated)
                publish()
    except background_jobs.JobCancelled:
        with guard:
            for cue in cues:
                if cue["translation"] == PENDING:
                    cue["translation"] = CANCELLED
            publish()
        raise
