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


# Stage keys of the React stage bar (five stages) and the 7-tab index each
# one covers (docs/specs/ux-workspace-shell-and-review.md, "Stage status").
STAGE_KEYS = ("source", "translate", "review", "dub", "export")
_STAGE_FOR_INDEX = {0: "source", 1: "source", 2: "source", 3: "translate",
                    4: "review", 5: "dub", 6: "export"}


def get_drama_progress(drama_id: int) -> dict:
    """The drama's pipeline progress for the React stage bar: the 7-tab
    `stage_index` from compute_workspace_stage_index, the five-stage key it
    maps to, whole-drama counts, booleans, and one state per stage
    ("done", "current", "pending", "optional" or "blocked"). NotFoundError
    for an unknown drama. Reads only; never creates the drama folder, and
    returns no path or file name."""
    import core as core_module
    import db
    from services.service_errors import NotFoundError
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    lines = core_module.lines_from_rows(db.load_lines(drama_id))
    ddir = os.path.join(db.DRAMAS_DIR, str(drama_id))
    index = compute_workspace_stage_index(drama, lines, ddir)
    line_count = len(lines)
    # Same definitions as the Review stage's counts (review_lines_service).
    untranslated = sum(1 for ln in lines if (ln.zh or "").strip() and not (ln.en or "").strip())
    flagged = sum(1 for ln in lines if ln.flag)
    has_audio = bool(drama.get("audio_filename") or drama.get("source_video_filename"))
    has_dub_track = os.path.isfile(os.path.join(ddir, "dub_track.wav"))
    exported = drama.get("status") == "exported"
    current = _STAGE_FOR_INDEX[index]
    no_lines = line_count == 0

    def state(key):
        if key == "source":
            return "current" if current == "source" else "done"
        if key == "dub":
            if has_dub_track:
                return "done"
            return "blocked" if no_lines else "optional"
        if no_lines:
            return "blocked"
        if key == current and not (key == "export" and exported):
            return "current"
        if key == "translate":
            return "done" if index > 3 and untranslated == 0 else "pending"
        if key == "review":
            return "done" if index >= 6 else "pending"
        return "done" if exported else "pending"  # export

    return {"drama_id": drama_id, "stage_index": index, "stage": current,
            "line_count": line_count, "untranslated_count": untranslated,
            "flagged_count": flagged, "has_audio": has_audio,
            "has_dub_track": has_dub_track, "exported": exported,
            "stages": [{"key": k, "state": state(k)} for k in STAGE_KEYS]}
