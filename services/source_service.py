"""
services/source_service.py -- Source-stage config for one drama, shared
by the FastAPI /api/source routes and the Streamlit Source tab
(`tabs/workspace_tab.py`'s `with tab_source:` block, lines ~1602-1958).

Migration Slice 19: the minimal prerequisite this migration needs before
a future "transcribe-and-align" action slice can be built (a prior
migration-architect scoping pass found that action's real home is inside
`tab_translate`, depending on state `tab_source` sets up -- see
docs/migration-review.md's own Transcript-stage scoping notes). This
slice covers config only: source_language, chinese_script, content_mode,
transcript_mode, and read-only audio/video/transcript-source presence.

Deliberately NOT here, by design (not an oversight):
  - Audio/video upload or yt-dlp download -- a materially different risk
    tier (multipart upload, an ffmpeg subprocess) than every config write
    in this migration so far. Today's Streamlit code saves an uploaded
    file and starts the transcribe job in the same click (`run_prep`,
    workspace_tab.py:3154-3234) -- folding upload into the future
    transcribe-action slice preserves that same one-step behavior, rather
    than inventing a new two-step "upload now, transcribe later" flow.
  - transcript_text / novel_narration_text -- neither is persisted ahead
    of the transcribe/chunk action today either (both are plain,
    un-saved widget values until that action's own handler writes them);
    the future action should accept them directly in its own request
    body, matching current behavior exactly.

No Streamlit or FastAPI import: plain functions, plain dicts in, plain
values out, so a CLI or another service could call them too.
"""
import os

from db import drama_dir, get_drama, update_drama
from services.service_errors import InvalidInputError, NotFoundError, UnsupportedOperationError

_SOURCE_LANGUAGES = ("zh", "ja", "ko")
_CHINESE_SCRIPTS = ("simplified", "traditional")
_CONTENT_MODES = ("audio_drama", "streamer_vod", "novel_narration")
_TRANSCRIPT_MODES = ("have_transcript", "whisper", "hardsub_ocr")


def _audio_available(drama_id: int, drama: dict) -> bool:
    """Mirrors tab_source's own `existing_audio` check (workspace_tab.py:
    1714-1718): an audio_filename is set AND the file is actually there."""
    audio_filename = drama.get("audio_filename")
    if not audio_filename:
        return False
    return os.path.exists(os.path.join(drama_dir(drama_id), audio_filename))


def get_source_config(drama_id: int) -> dict:
    """Read-only Source-stage summary for one drama. has_video_source
    reflects only already-persisted state (`source_video_filename`) --
    a video upload that hasn't been saved yet has no server-side presence
    to report, unlike the Streamlit tab's own live `audio_file is not
    None` check, which only exists within that one request/rerun. Raises
    NotFoundError for an unknown drama id."""
    drama = get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    content_mode = drama.get("content_mode") or "audio_drama"
    has_video_source = bool(drama.get("source_video_filename"))
    transcript_mode_options = ["have_transcript", "whisper"]
    if has_video_source:
        transcript_mode_options.append("hardsub_ocr")

    return {
        "drama_id": drama_id,
        "source_language": drama.get("source_language") or "zh",
        "chinese_script": drama.get("chinese_script") or "simplified",
        "content_mode": content_mode,
        "has_audio_pipeline": content_mode in ("audio_drama", "streamer_vod"),
        "audio_available": _audio_available(drama_id, drama),
        "has_video_source": has_video_source,
        "transcript_mode": drama.get("transcript_mode") or "have_transcript",
        "transcript_mode_options": transcript_mode_options,
        "has_raw_novel_context": os.path.exists(
            os.path.join(drama_dir(drama_id), "raw_novel_context.txt")),
    }


def update_source_config(drama_id: int, *, source_language: str = None,
                         chinese_script: str = None, content_mode: str = None,
                         transcript_mode: str = None) -> dict:
    """Field-scoped partial update -- only the fields actually passed are
    validated and written, mirroring db.update_drama's own partial-update
    shape. Replicates tab_source's own content_mode=="streamer_vod" ->
    media_type sync exactly (workspace_tab.py:1643-1652): one-directional,
    never reverted when content_mode changes away from streamer_vod again
    (a deliberate existing behavior, not a gap to fix here). Raises
    NotFoundError for an unknown drama id, InvalidInputError for an
    unknown enum value, UnsupportedOperationError for transcript_mode=
    "hardsub_ocr" on a drama with no video source. Returns the updated
    get_source_config(drama_id)."""
    drama = get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    fields = {}
    if source_language is not None:
        if source_language not in _SOURCE_LANGUAGES:
            raise InvalidInputError(f"Unknown source_language {source_language!r}.")
        fields["source_language"] = source_language
    if chinese_script is not None:
        if chinese_script not in _CHINESE_SCRIPTS:
            raise InvalidInputError(f"Unknown chinese_script {chinese_script!r}.")
        fields["chinese_script"] = chinese_script
    if content_mode is not None:
        if content_mode not in _CONTENT_MODES:
            raise InvalidInputError(f"Unknown content_mode {content_mode!r}.")
        fields["content_mode"] = content_mode
    if transcript_mode is not None:
        if transcript_mode not in _TRANSCRIPT_MODES:
            raise InvalidInputError(f"Unknown transcript_mode {transcript_mode!r}.")
        if transcript_mode == "hardsub_ocr" and not drama.get("source_video_filename"):
            raise UnsupportedOperationError(
                f"Drama {drama_id} has no video source -- hardsub_ocr needs one.")
        fields["transcript_mode"] = transcript_mode

    if fields:
        update_drama(drama_id, **fields)
        if (content_mode == "streamer_vod" and drama.get("media_type") != "streamer_vod"):
            update_drama(drama_id, media_type="streamer_vod")

    return get_source_config(drama_id)
