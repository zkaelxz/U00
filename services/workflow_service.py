"""UI-free workflow/pipeline-progress helpers (Streamlit retirement M0a).

`compute_workspace_stage_index` moved here unchanged from
tabs/workspace_tab.py (the Step 19 invariant) so it no longer depends on
the Streamlit tab; the tab imports it back. Never imports streamlit/fastapi.
"""
import os


def stage_statuses_from_index(stages, current_index):
    """Convenience for the common linear case: everything before
    current_index is done, current_index is current, everything after is
    not started. current_index of None means nothing has started yet."""
    if current_index is None:
        return ["not_started"] * len(stages)
    return [
        "done" if i < current_index else "current" if i == current_index else "not_started"
        for i in range(len(stages))
    ]


def compute_workspace_stage_index(drama, lines, ddir):
    """Maps a drama's real pipeline progress onto the 7 stage-tab indices
    the header's stepper uses (Source=0, Transcript=1, Diarize=2,
    Translate=3, Review=4, Dub=5, Export=6). The pipeline is linear, so one
    index is enough -- render_stepper_from_index() reads everything before
    it as done, it as current, everything after as not started.

    Review and Dub have no reliable automatic "done" signal of their own
    (review is manual QC with no completion flag; dubbing is optional), so
    once translation is complete this reports Review as current until
    either a dub track exists or the drama is marked exported, at which
    point it jumps straight to Export -- Dub only ever shows as done or
    not-started, never uniquely current.

    No lines yet doesn't by itself mean Transcript is current -- a
    brand-new drama with no audio/video (or, for novel narration, no
    saved novel text) hasn't finished Source either, so that case checks
    for real source content before advancing past it.

    Export/dub are checked before Diarize/Translate (Step 45): a drama
    that's genuinely marked exported or has a dub track has clearly moved
    well past those earlier stages, regardless of whether an earlier
    stage's own signal (e.g. a line's `speaker` field) ever got
    backfilled -- otherwise a drama with no persisted speaker data
    reports Diarize as current forever, no matter how far translation and
    export actually got.
    """
    content_mode = (drama or {}).get("content_mode") or "audio_drama"
    has_audio_pipeline = content_mode in ("audio_drama", "streamer_vod")
    if not lines:
        if has_audio_pipeline:
            _has_source = bool((drama or {}).get("audio_filename")
                                or (drama or {}).get("source_video_filename"))
        else:
            _has_source = bool(ddir and os.path.exists(
                os.path.join(ddir, "novel_narration_source.txt")))
        return 1 if _has_source else 0
    if (drama or {}).get("status") == "exported":
        return 6
    if ddir and os.path.exists(os.path.join(ddir, "dub_track.wav")):
        return 6
    _untranslated = any(not (ln.en or "").strip() for ln in lines)
    if has_audio_pipeline and not any(getattr(ln, "speaker", None) for ln in lines) and _untranslated:
        return 2
    if _untranslated:
        return 3
    return 4
