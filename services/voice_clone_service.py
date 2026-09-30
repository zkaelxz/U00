"""
services/voice_clone_service.py -- voice-clone setup for one drama's
characters (parity audit deletion blocker #7; inventory C01, C03, C09,
C13), the UI-free half of the Translate tab's "6. Name your characters &
set up voice cloning" block in `tabs/workspace_tab.py`.

Covers:
  - C09: upload a reference clip for a speaker, replace it, remove it.
  - C01: auto-extract candidate clips for ONE speaker from the drama's
    audio as a background job (`voiceref_<drama_id>`, progress + cancel),
    list the candidates, stream one for preview, choose one.
  - C13: save a speaker's clip (with its transcript, engine, design) to
    the library voice bank. Applying a bank entry already exists
    (characters_service.apply_voice_bank_entry).
  - C03: link a speaker to a known series character (or unlink).
  (C05 voice actor is a field of characters_service.update_character.)

Storage: every file this module writes lives in `<drama>/voice_refs/`
under a generated name (`clone_ref_<32 hex><ext>` for uploads,
`clone_pick_<candidate id>.wav` for a chosen candidate, candidates under
`voice_refs/candidates/<32 hex>.wav`), never a name built from the client
filename or the speaker label. The candidate manifest
(`voice_refs/candidates/manifest.json`) maps speaker label -> candidates.
Responses carry booleans and opaque candidate ids only: no path, filename
or URL.

Extraction differs from the tab in two deliberate ways: the tab picks one
clip per speaker for every speaker at once and stores it immediately; here
the job extracts up to `max_candidates` clips for the one speaker asked
for and the user picks one (the audit asked for preview-then-pick). Source
segments are the speaker's diarization turns when the drama has them (the
tab's source), else the speaker's own transcript lines. Scoring is the
same as `dub.extract_reference_clips` (3-12 s, closest to 6 s first); the
transcript for a candidate is found the tab's way (a line of that speaker
starting inside the segment). Clips are cut with ffmpeg (a timeout, and
cancel kills it) instead of pydub.

The job never writes the database (only files in voice_refs/); choosing a
candidate is a field-scoped `db.upsert_character` of one speaker's row.

Upload (replace) and remove, both local_only routes, delete the old clip
if this module wrote it (`clone_ref_` or `clone_pick_`), and both refuse
(ConflictError) while a dub, narration or audiobook job for the drama is
running or queued, since such a job holds absolute paths to the clips it
was started with. Choosing a candidate (lines.edit, reachable remotely)
repoints the speaker's field and deletes only the speaker's previous
`clone_pick_` copy (never an upload, a voice-bank copy or a tab file),
only if no other speaker points at it and no such job is active;
otherwise the old clip stays on disk. So repeated remote choosing keeps
at most one pick per speaker instead of growing the disk.

No Streamlit or FastAPI import.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid

import background_jobs
import db
import dub
from services import characters_service, drama_service
from services.media_upload_service import AUDIO_EXTENSIONS
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)

REFS_DIR = "voice_refs"
CANDIDATES_DIR = "candidates"
MANIFEST_NAME = "manifest.json"
JOB_PREFIX = "voiceref_"
# Jobs that read speakers' reference clips by absolute path while they run
# (dub_service.start_dub_run builds the clone map up front). A clip must not
# be deleted under them. Not drama_service.job_running_for_drama: that also
# counts voiceref_ (an extraction for another speaker) and every other job.
_CLIP_READING_JOB_PREFIXES = ("dub_", "narration_", "audiobook_")
_CLIP_IN_USE = ("A dub, narration or audiobook job is running for this drama and may be "
                "using this clip. Wait for it to finish or cancel it first.")

# Upload caps (C09). The tab accepts wav/mp3/m4a; flac/ogg are also plain audio.
MAX_CLIP_BYTES = 20 * 1024 * 1024
MIN_CLIP_SECONDS = 1.0
MAX_CLIP_SECONDS = 30.0
FFPROBE_TIMEOUT_SECONDS = 30
_CHUNK = 1024 * 1024

# Extraction (C01): same bounds and target as dub.extract_reference_clips.
EXTRACT_MIN_SECONDS = 3.0
EXTRACT_MAX_SECONDS = 12.0
EXTRACT_TARGET_SECONDS = 6.0
DEFAULT_CANDIDATES = 3
MAX_CANDIDATES = 5
FFMPEG_CUT_TIMEOUT_SECONDS = 120

MAX_BANK_NAME_LEN = 200
MAX_NOTES_LEN = 1000

_TOKEN = re.compile(r"^[0-9a-f]{32}$")
# Clips this module wrote: uploads (clone_ref_) and chosen candidates
# (clone_pick_). Only these are ever deleted.
_OWNED_CLIP = re.compile(r"^voice_refs/(clone_ref_[0-9a-f]{32}\.(wav|mp3|m4a|flac|ogg)"
                         r"|clone_pick_[0-9a-f]{32}\.wav)$")
# A chosen candidate's copy: the only kind choose_candidate may delete.
_PICKED_CLIP = re.compile(r"^voice_refs/clone_pick_[0-9a-f]{32}\.wav$")
_BAD_TYPE = "Unsupported file type. Upload a wav, mp3, m4a, flac or ogg clip."
_NOT_AUDIO = "That file isn't a readable audio clip."
_NO_FFPROBE = "ffprobe is not installed or not on PATH, which checking a clip needs."
_NO_FFMPEG = "ffmpeg is not installed or not on PATH, which cutting clips needs."


# --- shared helpers -----------------------------------------------------------

def _require_drama(drama_id: int) -> dict:
    return characters_service._require_drama(drama_id)


def _require_speaker(drama_id: int, speaker_label) -> str:
    if not isinstance(speaker_label, str) or not speaker_label or len(speaker_label) > 200:
        raise InvalidInputError("speaker_label is required.")
    if speaker_label not in characters_service._known_speakers(drama_id):
        raise NotFoundError("No such speaker in this drama.")
    return speaker_label


def _drama_path(drama_id: int) -> str:
    return os.path.join(db.DRAMAS_DIR, str(drama_id))


def _inside(root: str, path: str) -> bool:
    real_root = os.path.realpath(root)
    try:
        return os.path.commonpath([real_root, os.path.realpath(path)]) == real_root
    except ValueError:
        return False


def _safe_file(root: str, rel: str):
    """Absolute path of rel inside root if it is a real regular file
    (no symlink, no escape), else None."""
    if not rel:
        return None
    path = os.path.join(root, rel)
    if os.path.islink(path) or not os.path.isfile(path) or not _inside(root, path):
        return None
    return path


def _character_row(drama_id: int, speaker_label: str) -> dict:
    for row in db.list_characters(drama_id):
        if row["speaker_label"] == speaker_label:
            return row
    return {"speaker_label": speaker_label}


def _remove_owned_clip(drama_id: int, rel: str, keep_speaker: str):
    """Deletes a clip this module wrote (generated name only), unless
    another speaker of this drama still points at it. Anything else (a
    tab upload, a voice-bank copy) is left on disk, as the tab does."""
    if not rel or not _OWNED_CLIP.match(rel):
        return
    for row in db.list_characters(drama_id):
        if row["speaker_label"] != keep_speaker and row.get("ref_audio_filename") == rel:
            return
    path = _safe_file(_drama_path(drama_id), rel)
    if path:
        os.remove(path)


def _clip_reading_job_active(drama_id: int) -> bool:
    """True while a dub/narration/audiobook job for this drama is running
    or queued: in this process, or a fresh job_records row written by
    another process (as drama_service.job_running_for_drama checks)."""
    job_ids = {f"{prefix}{drama_id}" for prefix in _CLIP_READING_JOB_PREFIXES}
    for job_id in job_ids:
        job = background_jobs.get_status(job_id)
        if job and job.get("status") in ("running", "queued"):
            return True
    cutoff = time.time() - drama_service._STALE_JOB_RECORD_SECONDS
    for job_id in job_ids:
        rec = db.get_job_record(job_id)
        if (rec and rec.get("status") in ("running", "queued")
                and (rec.get("updated_at") or 0) >= cutoff):
            return True
    return False


def _require_no_clip_reading_job(drama_id: int):
    if _clip_reading_job_active(drama_id):
        raise ConflictError(_CLIP_IN_USE)


# --- C09: upload / remove ---------------------------------------------------------

def _clip_extension(client_filename) -> str:
    name = str(client_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise InvalidInputError(_BAD_TYPE)
    ext = os.path.splitext(name)[1].lower()
    if ext not in AUDIO_EXTENSIONS:
        raise InvalidInputError(_BAD_TYPE)
    return ext


def _probe_duration(path: str) -> float:
    """Audio duration via ffprobe (timeout). Raises InvalidInputError for a
    file ffprobe can't read or with no audio stream, and
    DependencyUnavailableError when ffprobe is missing. ffprobe's own
    message is never passed on (it names the temp path)."""
    import media_inspect
    if shutil.which("ffprobe") is None:
        raise DependencyUnavailableError(_NO_FFPROBE)
    try:
        info = media_inspect.run_ffprobe(path, timeout=FFPROBE_TIMEOUT_SECONDS)
    except media_inspect.ProbeError:
        raise InvalidInputError(_NOT_AUDIO) from None
    streams = info.get("streams") or []
    if not any(s.get("codec_type") == "audio" for s in streams):
        raise InvalidInputError(_NOT_AUDIO)
    if any(s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
           for s in streams):
        raise InvalidInputError("Upload an audio clip, not a video.")
    try:
        return float((info.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        raise InvalidInputError(_NOT_AUDIO) from None


def upload_reference_clip(drama_id: int, speaker_label, client_filename, fileobj,
                          ref_text=None) -> dict:
    """Stores an uploaded clip as this speaker's clone reference, replacing
    any clip this module stored before. Only the extension of the client's
    name is used (whitelisted); the body is streamed to a temp file in
    voice_refs/ (capped at MAX_CLIP_BYTES), checked with ffprobe (an audio
    stream, MIN..MAX_CLIP_SECONDS long), then renamed into place. The
    tab keeps the uploaded format (no transcode), and so does this.
    ref_text, when given, replaces the stored transcript. Returns the
    speaker's characters_service entry. ConflictError while a dub,
    narration or audiobook job for the drama is running or queued (the
    old clip it may be reading would be deleted)."""
    _require_drama(drama_id)
    _require_speaker(drama_id, speaker_label)
    ext = _clip_extension(client_filename)
    _require_no_clip_reading_job(drama_id)
    if ref_text is not None:
        characters_service._check_len("ref_text", ref_text, characters_service.MAX_REF_TEXT_LEN)
    refs = os.path.join(db.drama_dir(drama_id), REFS_DIR)
    os.makedirs(refs, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".clip_", suffix=ext, dir=refs)
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = fileobj.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_CLIP_BYTES:
                    raise InvalidInputError(
                        f"The clip is too large (max {MAX_CLIP_BYTES // (1024 * 1024)} MB).")
                out.write(chunk)
        if size == 0:
            raise InvalidInputError("The uploaded clip is empty.")
        duration = _probe_duration(tmp)
        if not (MIN_CLIP_SECONDS <= duration <= MAX_CLIP_SECONDS):
            raise InvalidInputError(
                f"A reference clip must be {MIN_CLIP_SECONDS:g} to {MAX_CLIP_SECONDS:g} seconds long.")
        _require_no_clip_reading_job(drama_id)  # again: the probe can take a while
        rel = f"{REFS_DIR}/clone_ref_{uuid.uuid4().hex}{ext}"
        os.replace(tmp, os.path.join(db.drama_dir(drama_id), rel))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    old = _character_row(drama_id, speaker_label).get("ref_audio_filename") or ""
    db.upsert_character(drama_id, speaker_label, ref_audio_filename=rel,
                        ref_text=None if ref_text is None else ref_text.strip())
    if old != rel:
        _remove_owned_clip(drama_id, old, speaker_label)
    return characters_service._get_one(drama_id, speaker_label)


def remove_reference_clip(drama_id: int, speaker_label, confirm: bool = False) -> dict:
    """Clears this speaker's clone reference (the transcript is kept, as
    it can be reused with a new clip). A clip file this module wrote is
    deleted; any other file is left on disk. Needs confirm=True.
    ConflictError while a dub, narration or audiobook job for the drama
    is running or queued."""
    _require_drama(drama_id)
    _require_speaker(drama_id, speaker_label)
    if confirm is not True:
        raise InvalidInputError("Removing a reference clip needs confirm=true.")
    _require_no_clip_reading_job(drama_id)
    old = _character_row(drama_id, speaker_label).get("ref_audio_filename") or ""
    if old:
        db.upsert_character(drama_id, speaker_label, ref_audio_filename="")
        _remove_owned_clip(drama_id, old, speaker_label)
    return characters_service._get_one(drama_id, speaker_label)


# --- C01: extract candidates ----------------------------------------------------

def _candidates_dir(drama_id: int) -> str:
    return os.path.join(_drama_path(drama_id), REFS_DIR, CANDIDATES_DIR)


def _candidate_file(drama_id: int, token: str):
    """A candidate's wav if it is a real file inside the drama folder
    (checked against the drama root, so a symlinked voice_refs/ doesn't
    count), else None."""
    if not _TOKEN.match(str(token)):
        return None
    return _safe_file(_drama_path(drama_id), f"{REFS_DIR}/{CANDIDATES_DIR}/{token}.wav")


def _load_manifest(drama_id: int) -> dict:
    path = os.path.join(_candidates_dir(drama_id), MANIFEST_NAME)
    if os.path.islink(path) or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_manifest(drama_id: int, manifest: dict):
    cdir = _candidates_dir(drama_id)
    os.makedirs(cdir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".manifest_", suffix=".json", dir=cdir)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(cdir, MANIFEST_NAME))


def _speaker_segments(drama_id: int, speaker_label: str, lines: list) -> list:
    """[(start, end)] for this speaker: diarization turns if stored (the
    tab's source), else the speaker's own transcript lines."""
    import diarize
    try:
        turns = diarize.load_turns(_drama_path(drama_id)) or []
    except (OSError, ValueError):
        turns = []
    segs = []
    for t in turns:
        if isinstance(t, dict) and t.get("speaker") == speaker_label:
            try:
                segs.append((float(t["start"]), float(t["end"])))
            except (KeyError, TypeError, ValueError):
                continue
    if segs:
        return segs
    return [(float(ln["start"]), float(ln["end"])) for ln in lines
            if ln.get("speaker") == speaker_label and ln.get("end") is not None]


def pick_segments(segments, max_candidates: int):
    """(chosen, skip): the up-to-max_candidates segments in [3, 12] s,
    closest to 6 s first (dub.extract_reference_clips' score), and, when
    none qualify, {"reason": "too_short"|"too_long"|"no_segments",
    "closest_duration"} like its `skipped` dict."""
    usable = [(s, e) for s, e in segments
              if EXTRACT_MIN_SECONDS <= e - s <= EXTRACT_MAX_SECONDS]
    usable.sort(key=lambda se: (abs((se[1] - se[0]) - EXTRACT_TARGET_SECONDS), se[0]))
    chosen = usable[:max_candidates]
    if chosen:
        return chosen, None
    if not segments:
        return [], {"reason": "no_segments", "closest_duration": None}
    closest = min((e - s for s, e in segments),
                  key=lambda d: (EXTRACT_MIN_SECONDS - d) if d < EXTRACT_MIN_SECONDS
                  else (d - EXTRACT_MAX_SECONDS))
    return [], {"reason": "too_short" if closest < EXTRACT_MIN_SECONDS else "too_long",
                "closest_duration": round(closest, 2)}


def _match_line(lines, speaker_label, start, end):
    """The tab's ref_text match: the first line of this speaker whose start
    falls inside the segment (+1 s)."""
    for ln in lines:
        if (ln.get("speaker") == speaker_label and (ln.get("zh") or "").strip()
                and start <= float(ln["start"]) <= end + 1):
            return ln
    return None


def start_extract_candidates(drama_id: int, speaker_label, max_candidates: int = DEFAULT_CANDIDATES) -> dict:
    """Starts job `voiceref_<drama_id>`: cuts up to max_candidates
    reference-clip candidates for one speaker from the drama's audio.
    Poll GET /api/jobs/{job_id}; the result is {"candidate_count": n}.
    Raises NotFoundError (drama/speaker), InvalidInputError (no audio,
    bad max_candidates), DependencyUnavailableError (no ffmpeg),
    ConflictError (an extraction already running for this drama)."""
    drama = _require_drama(drama_id)
    _require_speaker(drama_id, speaker_label)
    if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) \
            or not 1 <= max_candidates <= MAX_CANDIDATES:
        raise InvalidInputError(f"max_candidates must be 1 to {MAX_CANDIDATES}.")
    audio = _safe_file(_drama_path(drama_id), drama.get("audio_filename") or "")
    if audio is None:
        raise InvalidInputError("This drama has no audio to extract clips from.")
    if shutil.which("ffmpeg") is None:
        raise DependencyUnavailableError(_NO_FFMPEG)
    job_id = f"{JOB_PREFIX}{drama_id}"
    started = background_jobs.start_job(
        job_id, _extract_job, job_id, drama_id, speaker_label, max_candidates,
        description=f"Reference clip extraction (drama #{drama_id})")
    if not started:
        raise ConflictError("A reference clip extraction is already running for this drama.")
    return {"job_id": job_id}


def _extract_job(job_id, drama_id, speaker_label, max_candidates):
    drama = db.get_drama(drama_id)
    if drama is None:
        raise RuntimeError("The drama no longer exists.")
    root = _drama_path(drama_id)
    audio = _safe_file(root, drama.get("audio_filename") or "")
    if audio is None:
        raise RuntimeError("The drama's audio is missing.")
    lines = db.load_lines(drama_id)
    chosen, skip = pick_segments(_speaker_segments(drama_id, speaker_label, lines), max_candidates)
    cdir = _candidates_dir(drama_id)
    os.makedirs(cdir, exist_ok=True)
    made = []
    try:
        for n, (start, end) in enumerate(chosen):
            if background_jobs.is_cancel_requested(job_id):
                raise background_jobs.JobCancelled(job_id)
            background_jobs.update_progress(job_id, n / max(len(chosen), 1),
                                            f"Cutting clip {n + 1} of {len(chosen)}...")
            token = uuid.uuid4().hex
            out = os.path.join(cdir, f"{token}.wav")
            cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
                   "-i", audio, "-vn", "-ac", "1", "-ar", "24000", "-acodec", "pcm_s16le", out]
            try:
                background_jobs.run_cancellable(job_id, cmd, cwd=root,
                                                timeout=FFMPEG_CUT_TIMEOUT_SECONDS)
            except BaseException as exc:
                if os.path.exists(out):
                    os.remove(out)
                if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError)):
                    # ffmpeg's stderr names paths: never passed on.
                    raise RuntimeError("Could not cut a clip from the drama's audio.") from None
                raise
            line = _match_line(lines, speaker_label, start, end)
            made.append({"id": token, "start": round(start, 3), "end": round(end, 3),
                         "line_id": line.get("id") if line else None})
    except BaseException:
        for c in made:
            path = os.path.join(cdir, f"{c['id']}.wav")
            if os.path.exists(path):
                os.remove(path)
        raise
    manifest = _load_manifest(drama_id)
    previous = manifest.get(speaker_label)
    for old in (previous.get("candidates") or []) if isinstance(previous, dict) else []:
        path = _candidate_file(drama_id, old.get("id", "")) if isinstance(old, dict) else None
        if path:
            os.remove(path)
    manifest[speaker_label] = {"candidates": made, "skip": skip}
    _save_manifest(drama_id, manifest)
    background_jobs.set_result(job_id, {"candidate_count": len(made)})
    background_jobs.update_progress(job_id, 1.0, f"{len(made)} candidate clip(s) ready.")


def list_candidates(drama_id: int) -> dict:
    """Candidates per speaker from the last extraction of each speaker:
    id, start/end, duration and the transcript line matched to it (line
    text, hence lines.read). Candidates whose file is gone are dropped.
    Speakers no longer in the drama are skipped."""
    _require_drama(drama_id)
    known = characters_service._known_speakers(drama_id)
    texts = {ln["id"]: ln.get("zh") or "" for ln in db.load_lines(drama_id)}
    speakers = []
    for label, entry in sorted(_load_manifest(drama_id).items()):
        if label not in known or not isinstance(entry, dict):
            continue
        cands = []
        for c in entry.get("candidates") or []:
            if not isinstance(c, dict) or _candidate_file(drama_id, c.get("id", "")) is None:
                continue
            try:
                start, end = float(c.get("start") or 0), float(c.get("end") or 0)
            except (TypeError, ValueError):
                continue
            cands.append({"id": c["id"], "start": start, "end": end,
                          "duration": round(end - start, 2),
                          "ref_text": texts.get(c.get("line_id"), "")})
        skip = entry.get("skip") if isinstance(entry.get("skip"), dict) else {}
        reason = skip.get("reason")
        closest = skip.get("closest_duration")
        speakers.append({
            "speaker_label": label, "candidates": cands,
            "skip_reason": reason if reason in ("too_short", "too_long", "no_segments") else None,
            "closest_duration": (float(closest) if isinstance(closest, (int, float))
                                 and not isinstance(closest, bool) else None),
        })
    return {"drama_id": drama_id, "speakers": speakers}


def _find_candidate(drama_id: int, candidate_id: str):
    if not isinstance(candidate_id, str) or not _TOKEN.match(candidate_id):
        raise NotFoundError("No such candidate clip.")
    for label, entry in _load_manifest(drama_id).items():
        if not isinstance(entry, dict):
            continue
        for c in entry.get("candidates") or []:
            if isinstance(c, dict) and c.get("id") == candidate_id:
                path = _candidate_file(drama_id, candidate_id)
                if path is None:
                    break
                return label, c, path
    raise NotFoundError("No such candidate clip.")


def candidate_audio_path(drama_id: int, candidate_id: str) -> str:
    """Server-side path of one candidate's wav, for streaming (never
    returned to clients). NotFoundError for an unknown id."""
    _require_drama(drama_id)
    return _find_candidate(drama_id, candidate_id)[2]


def choose_candidate(drama_id: int, candidate_id: str) -> dict:
    """Makes one candidate its speaker's clone reference: the wav is copied
    to `voice_refs/clone_pick_<candidate id>.wav` (so a later extraction
    can't pull it away; choosing the same candidate again reuses that copy,
    as candidate files never change under an id) and the matched line's
    source text becomes ref_text, as the tab's auto-extract does; with no
    matched line the stored ref_text is left alone. Field-scoped write of
    that one speaker's row.
    This route is reachable remotely, so it deletes only the speaker's
    previous `clone_pick_` copy (one it made itself), never an upload, a
    voice-bank copy or a tab file, and only when no other speaker of the
    drama points at it and no dub/narration/audiobook job is active (such
    a job may hold its path); otherwise the old clip stays on disk."""
    _require_drama(drama_id)
    label, cand, path = _find_candidate(drama_id, candidate_id)
    if label not in characters_service._known_speakers(drama_id):
        raise NotFoundError("No such speaker in this drama.")
    root = db.drama_dir(drama_id)
    rel = f"{REFS_DIR}/clone_pick_{candidate_id}.wav"
    if _safe_file(root, rel) is None:
        fd, tmp = tempfile.mkstemp(prefix=".pick_", suffix=".tmp", dir=os.path.join(root, REFS_DIR))
        os.close(fd)
        try:
            shutil.copyfile(path, tmp)
            os.replace(tmp, os.path.join(root, rel))
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
    old = _character_row(drama_id, label).get("ref_audio_filename") or ""
    ref_text = None
    line_id = cand.get("line_id")
    if line_id is not None:
        for ln in db.load_lines(drama_id):
            if ln.get("id") == line_id and (ln.get("zh") or "").strip():
                ref_text = ln["zh"].strip()
                break
    db.upsert_character(drama_id, label, ref_audio_filename=rel, ref_text=ref_text)
    if old != rel and _PICKED_CLIP.match(old) and not _clip_reading_job_active(drama_id):
        _remove_owned_clip(drama_id, old, label)
    return characters_service._get_one(drama_id, label)


# --- C13: save to the voice bank ------------------------------------------------

def save_to_voice_bank(drama_id: int, speaker_label, name, notes: str = "") -> dict:
    """Copies this speaker's clip into the library voice bank with its
    transcript, clone engine (the default when unset, as the tab's picker
    shows), voice design, the drama's source language, and provenance
    text (drama title, character name). Raises InvalidInputError for a
    blank/long name or a speaker with no usable clip."""
    drama = _require_drama(drama_id)
    _require_speaker(drama_id, speaker_label)
    if not isinstance(name, str) or not name.strip():
        raise InvalidInputError("Type a name for this voice first.")
    characters_service._check_len("name", name, MAX_BANK_NAME_LEN)
    characters_service._check_len("notes", notes or "", MAX_NOTES_LEN)
    row = _character_row(drama_id, speaker_label)
    clip = _safe_file(_drama_path(drama_id), row.get("ref_audio_filename") or "")
    if clip is None:
        raise InvalidInputError("Set a reference clip for this speaker first.")
    display = next((c["character_name"] for c in db.list_characters_with_series_names(drama_id)
                    if c["speaker_label"] == speaker_label and c.get("character_name")), "")
    entry_id = db.save_voice_bank_entry(
        name.strip(), clip, ref_text=row.get("ref_text") or "",
        clone_engine=row.get("clone_engine") or dub.DEFAULT_CLONE_ENGINE,
        voice_design=row.get("voice_design") or "",
        language=drama.get("source_language") or "zh", notes=(notes or "").strip(),
        source_drama=drama.get("title_en") or drama.get("title_zh") or f"drama #{drama_id}",
        source_speaker=display or speaker_label)
    for e in characters_service.list_voice_bank():
        if e["id"] == entry_id:
            return e
    raise NotFoundError("The voice bank entry could not be read back.")  # pragma: no cover


# --- C03: series character link -------------------------------------------------

def link_series_character(drama_id: int, speaker_label, series_character_id) -> dict:
    """Links a speaker to a character of the drama's own series (the tab's
    "Known characters in this series" pick: sets series_character_id and
    copies the series name into character_name), or unlinks it when
    series_character_id is None. A character of another series is a 404."""
    drama = _require_drama(drama_id)
    _require_speaker(drama_id, speaker_label)
    if series_character_id is None:
        db.clear_character_series_link(drama_id, speaker_label)
        return characters_service._get_one(drama_id, speaker_label)
    characters_service._check_id("series_character_id", series_character_id)
    series_id = drama.get("series_id")
    match = next((sc for sc in (db.list_series_characters(series_id) if series_id else [])
                  if sc["id"] == series_character_id), None)
    if match is None:
        raise NotFoundError("No such character in this drama's series.")
    db.upsert_character(drama_id, speaker_label, character_name=match["character_name"],
                        series_character_id=match["id"])
    return characters_service._get_one(drama_id, speaker_label)
