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
# A blank reply is shown as a failure: as "done" the line would read as
# translated while showing nothing, and the captions would show no text.
EMPTY_REPLY_NOTE = f"{FAILED_PREFIX}: the engine sent back no translation]"


def pending_cues(segments, offset: float, first_id: int) -> list:
    """One untranslated cue per non-blank segment, ids from first_id on."""
    cues = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if text:
            # Whisper dates a first word a hair inside the lead-in silence, which the
            # timeline shift would turn into a negative time.
            cues.append({"id": first_id + len(cues), "start": max(0.0, offset + seg["start"]),
                         "end": max(0.0, offset + seg["end"]), "text": text, "translated": "",
                         "translation": PENDING})
    return cues


def snapshot(settled: list, chunk: list) -> list:
    """Copies for the job result: the feed reads it while the next update is
    being written, so it must never see a cue change under it."""
    return [dict(c) for c in settled + chunk]


def _finish(cue: dict, translated: str) -> None:
    translated = translated if isinstance(translated, str) and translated.strip() else EMPTY_REPLY_NOTE
    cue["translated"] = translated
    cue["translation"] = FAILED if translated.startswith(FAILED_PREFIX) else DONE


# What the earlier lines of one sentence show: its translation is on its last line.
CONTINUED = "…"
# At most this many lines are sent as one sentence: a run-on transcript with a
# stray full stop must not pile a whole chunk onto its last line.
MAX_SENTENCE_LINES = 3


def sentence_groups(cues: list) -> list:
    """[[position, ...], ...]: each run of lines that ends a sentence, so a
    sentence Whisper split over lines is translated whole. Korean and Japanese
    put the verb last, and a translator handed the first half of a sentence on
    its own gave it the whole sentence's English or nothing. Lines with no
    sentence end after them in reach stay one to a group."""
    import segment_splitting
    groups, run = [], []
    for pos, cue in enumerate(cues):
        run.append(pos)
        if segment_splitting._SENTENCE_END_RE.search(cue["text"].rstrip()[-3:]):
            groups.append(run)
            run = []
        elif len(run) == MAX_SENTENCE_LINES:
            groups.extend([p] for p in run)
            run = []
    groups.extend([p] for p in run)
    return groups


def _joined(cues: list, group: list) -> str:
    from live.live_translate import _join_text
    text = cues[group[0]]["text"]
    for pos in group[1:]:
        text = _join_text(text, cues[pos]["text"])
    return text


def translate_cues(cues: list, translator, checkpoint, on_stage=None, on_cues=None,
                   lock=None, on_sentence_error=None) -> None:
    """Translates the chunk's sentences (sentence_groups) in one id-keyed request,
    then one request per sentence only for those the reply left out or that
    failed, calling on_cues(cues) after each change so the transcript is already
    visible while the rest wait. A sentence's translation goes on its last line
    and the lines before it show CONTINUED. A cancel leaves the unfinished cues
    marked cancelled, published, and is re-raised. lock: held while cues change
    and are published, for a reader on another thread."""
    guard = lock if lock is not None else contextlib.nullcontext()
    groups = sentence_groups(cues)
    texts = [_joined(cues, g) for g in groups]

    def publish():
        if on_cues:
            on_cues(cues)

    def finish(group, translated):
        _finish(cues[group[-1]], translated)
        for pos in group[:-1]:
            if cues[group[-1]]["translation"] == DONE:
                cues[pos]["translated"], cues[pos]["translation"] = CONTINUED, DONE
            else:
                _finish(cues[pos], cues[group[-1]]["translated"])
    with guard:
        publish()
    try:
        if len(groups) > 1:
            if on_stage:
                on_stage("translating")
            found = translator.translate_chunk(texts)
            if found:
                with guard:
                    for unit, translated in found.items():
                        finish(groups[unit], translated)
                    publish()
        for unit, group in enumerate(groups):
            if cues[group[-1]]["translation"] != PENDING:
                continue
            checkpoint()
            if on_stage:
                on_stage("translating")
            try:
                translated = translator.translate(texts[unit])
            except background_jobs.JobCancelled:
                raise
            except Exception as exc:
                if on_sentence_error is None:
                    raise
                on_sentence_error(group, exc)
                continue
            with guard:
                finish(group, translated)
                publish()
    except background_jobs.JobCancelled:
        with guard:
            for cue in cues:
                if cue["translation"] == PENDING:
                    cue["translation"] = CANCELLED
            publish()
        raise
