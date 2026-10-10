"""Live cues shown in two steps: the transcript as soon as Whisper returns,
then the translation filled into the same cue.

A cue's id is its position in the session's list, which only ever grows, so a
client can match an update to the line it already shows by id and never by
position in a reply. Cues are never trimmed or edited once done, so a
client only re-reads cues still pending. Each cue carries its own translation state so one slow
or failed line never hides the others."""
import background_jobs

PENDING, DONE, FAILED, CANCELLED = "pending", "done", "failed", "cancelled"
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


def translate_cues(cues: list, translator, checkpoint, on_stage=None, on_cues=None) -> None:
    """Translates the cues in order, calling on_cues(cues) after each so the
    transcript is already visible while the rest wait. A cancel leaves the
    unfinished cues marked cancelled, published, and is re-raised."""
    if on_cues:
        on_cues(cues)
    try:
        for cue in cues:
            checkpoint()
            if on_stage:
                on_stage("translating")
            _finish(cue, translator.translate(cue["text"]))
            if on_cues:
                on_cues(cues)
    except background_jobs.JobCancelled:
        for cue in cues:
            if cue["translation"] == PENDING:
                cue["translation"] = CANCELLED
        if on_cues:
            on_cues(cues)
        raise
