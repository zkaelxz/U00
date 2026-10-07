"""
services/reader_service.py -- the Reader's page HTML and the rest of the
Reader's logic, for the `/api/reader` routes.

Loading a page must never make a **paid LLM call** or a **DB write**
(confirmed by the user, 2026-09-28). `get_reader_page_html` only serves
whatever's already in `vocab_lookups` (populated by the explicit
lookup_page_definitions action) -- never a live dictionary call, never a
write. A fresh/paid lookup stays a separate, explicit action, not a side
effect of viewing a page.

No HTTP types: takes plain values, returns an HTML
string, so `cli.py` or a script could call it too.

Also here: caption tracks, media availability,
reading progress, notes, click-to-define lookups
(an explicit action, never part of get_reader_page), the rich-Anki
queue, vocab CSV/.apkg export, story tools, the universe wiki and Q&A.
Conventions shared by all of them:
  - every function takes a drama id and raises NotFoundError for an
    unknown one (the ownership check); a sub-record (a vocab word, a
    wiki entry) is only ever read/written through its own drama;
  - plain dicts/lists/str/bytes out, never a filesystem path or key
    (media_file_path is the one server-internal exception, documented);
  - keys are resolved server-side, never accepted or returned, and an
    engine failure is passed through diagnostics.redact_for_support (keys
    and absolute paths removed), a local OSError becomes a fixed message;
  - AI results are matched back by id or name, never by list position;
  - writes are scoped to their own table/fields (vocab_lookups,
    wiki_entries, progress, personal_notes), never a line rewrite.
Reader data is per library, not per profile (retirement plan section
10): progress and notes use db's default profile (profile_id=None).

Spoiler boundary: every `up_to_line_idx=None` below means NO spoiler
limit (the whole drama); a route or React caller that wants spoiler-free
must pass that boundary explicitly.

Permission contract for routes (user decision, 2026-09-29):
  - reads (page, overview, notes, vocab list, wiki list,
    media availability): `library.read`;
  - caption tracks, media streaming and every export
    (CSV, .apkg, wiki Markdown): `lines.read`;
  - reader-data writes (progress, notes, rich-export queue, clear wiki):
    `lines.edit`;
  - LLM tools (who-is, explain, recap, relationships, wiki update, Q&A,
    and lookup_page_definitions with use_llm=True): `jobs.start` plus
    `require_engines_allowed`, where an omitted engine counts as paid.
"""

import os
import re
import shutil
import tempfile

import core as core_module
import db
import reader
import story_context
import subtitle_formats
import translate_engines
import universe_wiki
import vocab_export
from services import drama_service, settings_service, translate_service
from services.media_upload_service import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from services.service_errors import (
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    ServiceError,
    UnsupportedOperationError,
)

DEFAULT_CHAPTER_SIZE = 40


def _cached_definitions(drama_id: int, source_language: str) -> dict:
    """{word: {"reading": ..., "definitions": [...]}} from whatever's
    already been looked up and saved for this drama -- never a live call.
    Matches `source_language` (one language per drama), so a stale entry from a drama whose
    source language changed doesn't leak in."""
    out = {}
    for row in db.list_vocab_lookups(drama_id=drama_id):
        if row.get("language") != source_language:
            continue
        out[row["word"]] = {"reading": row.get("reading"), "definitions": row.get("definitions") or []}
    return out


def get_reader_page(drama_id: int, page: int = 1, chapter_size: int = DEFAULT_CHAPTER_SIZE,
                    theme: str = "light", font_size: int = 22, line_height: float = 2.4,
                    max_width: int = 1200, font: str = "system") -> dict:
    """{html, page, page_count, total_lines} for one page of a drama's
    reader view, definitions sourced only from what's already cached
    (see module docstring) -- never a live dictionary lookup. Raises
    InvalidInputError for a bad drama id/page/chapter_size and
    NotFoundError for an unknown drama or one with no lines yet, the
    same vocabulary every other service in this app's migration uses."""
    if not isinstance(drama_id, int) or isinstance(drama_id, bool) or not (1 <= drama_id <= drama_service.MAX_ID):
        raise InvalidInputError("A drama id is a positive whole number.")
    if not isinstance(chapter_size, int) or isinstance(chapter_size, bool) or not (10 <= chapter_size <= 200):
        raise InvalidInputError("chapter_size must be a whole number from 10 to 200.")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise InvalidInputError("page must be a positive whole number.")

    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    rows = db.load_lines(drama_id)
    lines = core_module.lines_from_rows(rows)
    if not lines:
        raise NotFoundError(f"Drama {drama_id} has no lines yet.")

    page_count = max(1, (len(lines) + chapter_size - 1) // chapter_size)
    if page > page_count:
        raise InvalidInputError(f"page {page} is past the last page ({page_count}).",
                                details={"page_count": page_count})

    page_lines = lines[(page - 1) * chapter_size: page * chapter_size]
    source_language = drama.get("source_language") or "zh"
    definitions = _cached_definitions(drama_id, source_language)

    html_str = reader.build_reader_html(
        page_lines, source_language, definitions, audio_data_uri=None, theme=theme,
        font_size=font_size, line_height=line_height, max_width=max_width, font=font)

    return {"html": html_str, "page": page, "page_count": page_count, "total_lines": len(lines)}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

MAX_NOTES_CHARS = 100_000
MAX_QUESTION_CHARS = 2000
MAX_CHAT_TURNS = 40
MAX_CHAT_TURN_CHARS = 20_000
MAX_CHAT_TOTAL_CHARS = 100_000
MAX_MODEL_CHARS = 100
MAX_LOOKUP_TEXT_CHARS = 200
MAX_VOCAB_WORDS_PER_CALL = 500
# Bounds on the synchronous LLM/ffmpeg work one request may do (security
# review M1/M2 of route batch 2B): a recap sends at most the last
# MAX_RECAP_LINES lines before the page and at most MAX_RECAP_CHARS of
# their text; a wiki update reads at most MAX_WIKI_CHUNKS_PER_CALL chunks
# of WIKI_CHUNK_LINES text lines (universe_wiki.extract_wiki_entries'
# own default chunk size) and reports where to resume; a rich .apkg has
# at most MAX_RICH_CARDS cards, each audio clip cut with a
# RICH_CLIP_TIMEOUT_SECONDS ffmpeg timeout and all clips within
# RICH_AUDIO_BUDGET_SECONDS (later cards come out text-only).
MAX_RECAP_LINES = 400
MAX_RECAP_CHARS = 60_000
WIKI_CHUNK_LINES = 150
MAX_WIKI_CHUNKS_PER_CALL = 10
MAX_RICH_CARDS = 300
RICH_CLIP_TIMEOUT_SECONDS = 15
RICH_AUDIO_BUDGET_SECONDS = 120
MEDIA_KINDS = ("original", "dub", "narration")
CAPTION_TRACK_FIELDS = {"Source": "zh", "English": "en", "Bilingual": "bilingual"}
_LISTENING_MEDIA_TYPES = ("audio_drama", "video_drama", "asmr")


def _require_drama(drama_id) -> dict:
    """The ownership check every function below starts with: a bad id is
    InvalidInputError, an unknown drama NotFoundError."""
    if not isinstance(drama_id, int) or isinstance(drama_id, bool) or not (1 <= drama_id <= drama_service.MAX_ID):
        raise InvalidInputError("A drama id is a positive whole number.")
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _lines(drama_id: int) -> list:
    return core_module.lines_from_rows(db.load_lines(drama_id))


def _require_lines(drama_id: int) -> list:
    lines = _lines(drama_id)
    if not lines:
        raise NotFoundError(f"Drama {drama_id} has no lines yet.")
    return lines


def _check_page(page, chapter_size, n_lines: int) -> int:
    if not isinstance(chapter_size, int) or isinstance(chapter_size, bool) or not (10 <= chapter_size <= 200):
        raise InvalidInputError("chapter_size must be a whole number from 10 to 200.")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise InvalidInputError("page must be a positive whole number.")
    page_count = max(1, (n_lines + chapter_size - 1) // chapter_size)
    if page > page_count:
        raise InvalidInputError(f"page {page} is past the last page ({page_count}).",
                                details={"page_count": page_count})
    return page_count


def _check_line_idx(up_to_line_idx):
    if up_to_line_idx is not None and (not isinstance(up_to_line_idx, int)
                                       or isinstance(up_to_line_idx, bool) or up_to_line_idx < 0):
        raise InvalidInputError("up_to_line_idx must be a non-negative whole number.")


def _scope(lines: list, up_to_line_idx) -> tuple:
    """(scoped_lines, limit_idx): the spoiler boundary. None means NO
    spoiler limit -- the whole drama; callers wanting spoiler-free must
    pass the boundary; otherwise it's clamped into the
    drama's own index range."""
    _check_line_idx(up_to_line_idx)
    last = lines[-1].idx if lines else -1
    if up_to_line_idx is None:
        return lines, last
    limit = min(up_to_line_idx, last)
    return [ln for ln in lines if ln.idx <= limit], limit


def _title(drama: dict, fallback: str) -> str:
    return drama.get("title_en") or drama.get("title_zh") or fallback


def _safe_filename(stem: str, ext: str, fallback: str = "vocab") -> str:
    """An ASCII-only download name built from a drama title: anything
    outside [A-Za-z0-9 ._-] becomes "_", so it can't name a path and a
    route can put it in a latin-1 Content-Disposition header."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]+", "_", stem or "").strip(" ._")
    if not re.search(r"[A-Za-z0-9]", cleaned):
        cleaned = fallback
    return f"{cleaned[:120]}.{ext}"


def llm_engine(engine_name=None, model=None):
    """A reference-capable LLM engine with its key resolved server-side.
    Claude is the default."""
    engine_name = engine_name or "claude"
    if model is not None and (not isinstance(model, str) or not model
                              or len(model) > MAX_MODEL_CHARS
                              or re.search(r"[\s\x00-\x1f\x7f]", model)):
        raise InvalidInputError(
            f"model must be at most {MAX_MODEL_CHARS} characters with no spaces or control characters.")
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    if engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't do this.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=settings_service.get_gemini_free_tier(),
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    if not getattr(engine, "supports_reference", False):
        raise UnsupportedOperationError(
            f"{engine_name} can't do this; use an LLM engine (Claude, DeepSeek, Ollama...).")
    return engine


def _run_engine(fn):
    try:
        return fn()
    except ServiceError:
        raise
    except OSError:  # local work (e.g. the CEDICT download/cache): no path out
        raise ServiceError("A local dictionary or file step failed; see the app log.") from None
    except Exception as e:  # engine/network failure: never leak a key or path
        import diagnostics
        raise ServiceError("The engine call failed: "
                           + diagnostics.redact_for_support(str(e))[:300]) from None


def _text_arg(value, field: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{field} must be non-empty text.")
    if len(value) > max_len:
        raise InvalidInputError(f"{field} is longer than {max_len} characters.")
    return value.strip()


# ---------------------------------------------------------------------------
# Watch / listen: caption tracks and media availability
# ---------------------------------------------------------------------------

def caption_tracks(lines):
    """The CC tracks for the Watch / listen video player, as
    {label: WebVTT text}. Only languages with at least one non-empty line
    get a track -- an all-blank English track would just be an empty menu
    entry -- and Bilingual only when both sides have something to pair.

    WebVTT rather than SRT: a browser's <track> element only plays
    WebVTT."""
    tracks = {}
    if any(ln.zh.strip() for ln in lines):
        tracks["Source"] = subtitle_formats.lines_to_vtt(lines, "zh")
    if any(ln.en.strip() for ln in lines):
        tracks["English"] = subtitle_formats.lines_to_vtt(lines, "en")
    if len(tracks) == 2:
        tracks["Bilingual"] = subtitle_formats.lines_to_vtt(lines, "bilingual")
    return tracks


def get_caption_tracks(drama_id: int) -> dict:
    """{drama_id, tracks: {label: WebVTT text}} built from the drama's
    current lines (so edits show without re-exporting). Empty tracks for
    a drama with no text."""
    _require_drama(drama_id)
    return {"drama_id": drama_id, "tracks": caption_tracks(_lines(drama_id))}


def _confined_file(base: str, name, allowed: tuple):
    """The absolute path of `name` inside the drama folder `base`, or None.
    Same confinement as media_playback_service.resolve_media (realpath +
    commonpath, regular file, whitelisted extension), and a symlink is
    refused outright rather than followed."""
    if not name or not isinstance(name, str) or os.path.splitext(name)[1].lower() not in allowed:
        return None
    joined = os.path.join(base, name)
    if os.path.islink(joined):
        return None
    path = os.path.realpath(joined)
    try:
        inside = os.path.commonpath([base, path]) == base
    except ValueError:   # different Windows drives, or mixed absolute/relative
        inside = False
    if not inside or path == base or not os.path.isfile(path):
        return None
    return path


def _media_paths(drama_id: int, drama: dict) -> dict:
    """{kind: (media_type, absolute path)} for files that pass
    _confined_file, video preferred over audio for the original.
    Server-internal."""
    base = os.path.realpath(db.drama_dir(drama_id))
    out = {}
    for field, media_type, allowed in (("source_video_filename", "video", VIDEO_EXTENSIONS),
                                       ("audio_filename", "audio", AUDIO_EXTENSIONS)):
        p = _confined_file(base, drama.get(field), allowed)
        if p:
            out["original"] = (media_type, p)
            break
    for kind, name in (("dub", "dub_track.wav"), ("narration", "narration_track.wav")):
        p = _confined_file(base, name, (".wav",))
        if p:
            out[kind] = ("audio", p)
    return out


def get_media_availability(drama_id: int) -> dict:
    """What the Watch / listen panel can show, without any path:
    {drama_id, original: "video"|"audio"|None, dub, narration,
    caption_tracks: [labels], captions_overlay}. captions_overlay is
    False for an audio-only original."""
    drama = _require_drama(drama_id)
    media = _media_paths(drama_id, drama)
    original = media.get("original", (None, None))[0]
    labels = list(caption_tracks(_lines(drama_id)))
    return {"drama_id": drama_id, "original": original, "dub": "dub" in media,
            "narration": "narration" in media, "caption_tracks": labels,
            "captions_overlay": original == "video" and bool(labels)}


def media_file_path(drama_id: int, kind: str) -> tuple:
    """(media_type, absolute path) of one playable file, for a Range-
    capable streaming endpoint to open server-side. SERVER-INTERNAL: the
    path must never be put in a response body, header or log. NotFound
    when the drama or that file doesn't exist."""
    drama = _require_drama(drama_id)
    if kind not in MEDIA_KINDS:
        raise InvalidInputError("kind must be one of: " + ", ".join(MEDIA_KINDS))
    found = _media_paths(drama_id, drama).get(kind)
    if found is None:
        raise NotFoundError(f"This drama has no {kind} media file.")
    return found


# ---------------------------------------------------------------------------
# Progress and length
# ---------------------------------------------------------------------------

def get_reading_overview(drama_id: int) -> dict:
    """The Length / Progress / Lines metrics plus the resume point:
    {drama_id, length_display, line_count, percent_complete, last_page,
    last_line_idx}. Timed media use the real duration, text a reading-time
    estimate."""
    drama = _require_drama(drama_id)
    lines = _lines(drama_id)
    est = (story_context.estimate_listening_time(lines)
           if drama.get("media_type") in _LISTENING_MEDIA_TYPES
           else story_context.estimate_reading_time(lines))
    prog = db.get_progress(drama_id) or {}
    return {"drama_id": drama_id, "length_display": est["display"], "line_count": len(lines),
            "percent_complete": float(prog.get("percent_complete") or 0.0),
            "last_page": int(prog.get("last_page") or 1),
            "last_line_idx": prog.get("last_line_idx")}


def save_reading_position(drama_id: int, page: int, chapter_size: int = DEFAULT_CHAPTER_SIZE) -> dict:
    """Records that `page` was viewed: last line = the page's last line,
    percent from it. Writes only the progress row (partial upsert)."""
    _require_drama(drama_id)
    lines = _require_lines(drama_id)
    _check_page(page, chapter_size, len(lines))
    page_lines = lines[(page - 1) * chapter_size: page * chapter_size]
    last_idx = page_lines[-1].idx
    percent = story_context.compute_percent_complete(last_idx, len(lines))
    db.save_progress(drama_id, last_line_idx=last_idx, last_page=page, percent_complete=percent)
    return {"drama_id": drama_id, "last_page": page, "last_line_idx": last_idx,
            "percent_complete": percent}


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def get_notes(drama_id: int) -> dict:
    _require_drama(drama_id)
    return {"drama_id": drama_id, "notes": db.get_personal_notes(drama_id)}


def save_notes(drama_id: int, notes: str) -> dict:
    _require_drama(drama_id)
    if not isinstance(notes, str):
        raise InvalidInputError("notes must be text.")
    if len(notes) > MAX_NOTES_CHARS:
        raise InvalidInputError(f"notes are longer than {MAX_NOTES_CHARS} characters.")
    db.save_personal_notes(drama_id, notes)
    return {"drama_id": drama_id, "notes": notes}


# ---------------------------------------------------------------------------
# Click-to-define lookups and vocab
# ---------------------------------------------------------------------------

def _define_words_llm(words: list, context_lines: list, engine, source_language: str) -> dict:
    """The LLM fallback for words the local dictionary lacks, matched
    back by an explicit id rather than dictionary.define_words_llm's own
    list-position zip (a short or reordered reply would otherwise put a
    definition on the wrong word). A word whose id doesn't come back is
    just left undefined."""
    lang_name = core_module.LANGUAGE_NAMES.get(source_language, "Chinese")
    context = "\n".join(context_lines[:50])
    out = {}
    unique = list(dict.fromkeys(words))
    for start in range(0, len(unique), 25):
        batch = unique[start:start + 25]
        numbered = "\n".join(f"{i}. {w}" for i, w in enumerate(batch, 1))
        prompt = (
            f"Given this {lang_name} text as context:\n\n{context}\n\n"
            f"For each numbered {lang_name} word/phrase below, give its reading "
            "(pinyin for Chinese, hiragana for Japanese, or null for Korean) and "
            "1-2 short English definitions as used in this context. "
            "Return ONLY a JSON object keyed by each word's number: "
            '{"1": {"reading": "...", "definitions": ["...", "..."]}}. '
            "No preamble, no markdown fences.\n\n" + numbered)
        text = translate_engines.call_llm_json(engine, prompt, max_tokens=3000, fallback=None)
        if not text:
            continue
        stripped = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
        data = translate_engines.extract_first_json_value(stripped)
        if not isinstance(data, dict):
            continue
        for i, w in enumerate(batch, 1):
            entry = data.get(str(i))
            if not isinstance(entry, dict):
                continue
            defs = entry.get("definitions")
            defs = [str(d) for d in defs if isinstance(d, (str, int, float))] if isinstance(defs, list) else []
            reading = entry.get("reading")
            out[w] = {"reading": reading if isinstance(reading, str) else None, "definitions": defs}
    return out


def lookup_page_definitions(drama_id: int, page: int, chapter_size: int = DEFAULT_CHAPTER_SIZE,
                            use_llm: bool = False, engine_name: str = None, model: str = None) -> dict:
    """"Load / refresh this page" as an explicit action:
    segments the page, defines every word (CC-CEDICT locally for Chinese;
    the LLM fallback only when use_llm, since that is a paid call), and
    saves each definition to the drama's vocab list (insert-if-absent,
    never overwriting an existing lookup). Returns {drama_id, page,
    definitions: {word: {reading, definitions}}, saved}."""
    import dictionary
    import segment
    drama = _require_drama(drama_id)
    lines = _require_lines(drama_id)
    _check_page(page, chapter_size, len(lines))
    page_lines = lines[(page - 1) * chapter_size: page * chapter_size]
    lang = drama.get("source_language") or "zh"
    script = drama.get("chinese_script") or "simplified"
    engine = llm_engine(engine_name, model) if use_llm else None

    def work():
        try:
            words = [w for ln in page_lines
                     for w, _reading in segment.segment_and_annotate(ln.zh, lang, chinese_script=script)
                     if w.strip()]
        except ImportError as e:  # the word splitter is an optional install
            package = e.name or "a word-splitting package"
            raise DependencyUnavailableError(
                f"Looking up words needs {package}, which isn't installed (see Diagnostics).") from None
        defs, needs_llm = {}, []
        for w in dict.fromkeys(words):
            hit = dictionary.lookup_cedict(w) if lang == "zh" else None
            if hit:
                defs[w] = {"reading": hit["pinyin"], "definitions": hit["definitions"][:3]}
            else:
                needs_llm.append(w)
        if needs_llm and engine is not None:
            defs.update(_define_words_llm(needs_llm, [ln.zh for ln in page_lines], engine, lang))
        return defs

    defs = _run_engine(work)
    for word, entry in defs.items():
        first_line = next((ln.idx for ln in page_lines if word in ln.zh), None)
        db.save_vocab_lookup(drama_id, word, entry.get("reading"),
                             entry.get("definitions", []), lang, first_line)
    return {"drama_id": drama_id, "page": page, "definitions": defs, "saved": len(defs)}


def _vocab_row(row: dict) -> dict:
    return {"word": row["word"], "reading": row.get("reading"),
            "definitions": row.get("definitions") or [], "language": row.get("language"),
            "first_seen_line_idx": row.get("first_seen_line_idx"),
            "export_rich": bool(row.get("export_rich"))}


def list_vocab(drama_id: int, rich_only: bool = False) -> dict:
    """{drama_id, count, words: [...]} -- this drama's looked-up words
    only (list_vocab_lookups with no drama id would return every drama's)."""
    _require_drama(drama_id)
    rows = [_vocab_row(r) for r in db.list_vocab_lookups(drama_id, rich_only=rich_only)]
    return {"drama_id": drama_id, "count": len(rows), "words": rows}


def set_rich_export(drama_id: int, words: list, queued: bool = True) -> dict:
    """Queues (or un-queues) words for the rich sentence+audio Anki card.
    Every word must already be one of this drama's lookups -- NotFound
    otherwise, so another drama's vocab can't be touched."""
    _require_drama(drama_id)
    if (not isinstance(words, list) or not words or len(words) > MAX_VOCAB_WORDS_PER_CALL
            or not all(isinstance(w, str) and w for w in words)):
        raise InvalidInputError(
            f"words must be a list of 1-{MAX_VOCAB_WORDS_PER_CALL} looked-up words.")
    known = {r["word"] for r in db.list_vocab_lookups(drama_id)}
    missing = [w for w in words if w not in known]
    if missing:
        raise NotFoundError("Not in this drama's vocab list: " + ", ".join(missing[:10]))
    unique = list(dict.fromkeys(words))
    for w in unique:
        db.set_vocab_export_rich(drama_id, w, bool(queued))
    return {"drama_id": drama_id, "updated": len(unique), "queued": bool(queued),
            "rich_count": len(db.list_vocab_lookups(drama_id, rich_only=True))}


def export_vocab_csv(drama_id: int) -> dict:
    """{filename, media_type, content: str} -- Anki-importable CSV."""
    drama = _require_drama(drama_id)
    vocab = db.list_vocab_lookups(drama_id)
    if not vocab:
        raise NotFoundError("No words have been looked up in this drama yet.")
    return {"filename": _safe_filename(_title(drama, "vocab"), "csv"), "media_type": "text/csv",
            "content": vocab_export.export_vocab_csv(vocab)}


def export_vocab_apkg(drama_id: int, rich: bool = False, include_audio: bool = True) -> dict:
    """{filename, media_type, content: bytes, audio_omitted, cards_capped}
    -- an Anki deck. rich=True builds the sentence(+audio clip) cards from
    the queued words (at most MAX_RICH_CARDS), with an audio clip only when
    the drama has an original audio/video file AND include_audio (the
    route passes False for a caller without media.stream; audio_omitted
    then says the drama had audio that was left out). Built in a temp dir
    that is always removed; nothing is left in the drama folder.
    DependencyUnavailable without genanki."""
    drama = _require_drama(drama_id)
    vocab = db.list_vocab_lookups(drama_id, rich_only=bool(rich))
    if not vocab:
        raise NotFoundError("No words queued for the rich export yet." if rich
                            else "No words have been looked up in this drama yet.")
    cards_capped = bool(rich) and len(vocab) > MAX_RICH_CARDS
    if cards_capped:
        vocab = vocab[:MAX_RICH_CARDS]
    original = _media_paths(drama_id, drama).get("original") if rich else None
    audio_omitted = original is not None and not include_audio
    if not include_audio:
        original = None
    deck = _title(drama, "Baihe Vocab")
    name = "vocab_sentence.apkg" if rich else "vocab.apkg"
    tmp = tempfile.mkdtemp(prefix="baihe_vocab_")
    try:
        out_path = os.path.join(tmp, name)
        try:
            if rich:
                vocab_export.export_vocab_apkg_sentence(
                    vocab, _lines(drama_id), deck + " (sentences)", out_path,
                    audio_path=original[1] if original else None,
                    clip_timeout=RICH_CLIP_TIMEOUT_SECONDS,
                    audio_budget_seconds=RICH_AUDIO_BUDGET_SECONDS)
            else:
                vocab_export.export_vocab_apkg(vocab, deck, out_path)
        except ImportError:
            raise DependencyUnavailableError(
                "Needs `pip install genanki` for .apkg export -- CSV works without it.") from None
        with open(out_path, "rb") as f:
            content = f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"filename": name, "media_type": "application/octet-stream", "content": content,
            "audio_omitted": audio_omitted, "cards_capped": cards_capped}


# ---------------------------------------------------------------------------
# Story tools (paid LLM calls; never write)
# ---------------------------------------------------------------------------

def who_is_character(drama_id: int, name: str, up_to_line_idx: int = None,
                     engine_name: str = None, model: str = None) -> dict:
    drama = _require_drama(drama_id)
    name = _text_arg(name, "name", MAX_LOOKUP_TEXT_CHARS)
    scoped, _ = _scope(_require_lines(drama_id), up_to_line_idx)
    engine = llm_engine(engine_name, model)
    answer = _run_engine(lambda: story_context.who_is_character(name, scoped, drama, engine))
    return {"drama_id": drama_id, "answer": answer}


def explain_reference(drama_id: int, phrase: str, up_to_line_idx: int = None,
                      engine_name: str = None, model: str = None) -> dict:
    drama = _require_drama(drama_id)
    phrase = _text_arg(phrase, "phrase", MAX_LOOKUP_TEXT_CHARS)
    scoped, _ = _scope(_require_lines(drama_id), up_to_line_idx)
    engine = llm_engine(engine_name, model)
    answer = _run_engine(lambda: story_context.explain_reference(
        phrase, scoped, engine, source_language=drama.get("source_language") or "zh"))
    return {"drama_id": drama_id, "answer": answer}


def recap(drama_id: int, page: int, chapter_size: int = DEFAULT_CHAPTER_SIZE,
          engine_name: str = None, model: str = None) -> dict:
    """Summary of what came before `page` (the first page's lines when on
    page 1) ("Recap what I've read so far"). Bounded to the
    last MAX_RECAP_LINES lines before the page and MAX_RECAP_CHARS of
    their English text (oldest dropped first); `truncated` says so."""
    _require_drama(drama_id)
    lines = _require_lines(drama_id)
    _check_page(page, chapter_size, len(lines))
    section = lines[:(page - 1) * chapter_size] or lines[:chapter_size]
    full = len(section)
    section = section[-MAX_RECAP_LINES:]
    total = sum(len(ln.en or "") for ln in section)
    start = 0
    while total > MAX_RECAP_CHARS and start < len(section) - 1:
        total -= len(section[start].en or "")
        start += 1
    section = section[start:]
    engine = llm_engine(engine_name, model)
    summary = _run_engine(lambda: story_context.summarize_section(
        section, engine, section_label=f"up to page {page}"))
    return {"drama_id": drama_id, "summary": summary, "truncated": len(section) < full}


def relationship_map(drama_id: int, up_to_line_idx: int = None,
                     engine_name: str = None, model: str = None) -> dict:
    """{drama_id, characters: [{name, role, description}], relationships,
    mermaid} -- characters/relationships are keyed by name, not position."""
    _require_drama(drama_id)
    scoped, _ = _scope(_require_lines(drama_id), up_to_line_idx)
    engine = llm_engine(engine_name, model)
    rel = _run_engine(lambda: story_context.build_relationship_map(scoped, engine)) or {}
    has_chars = bool(rel.get("characters"))
    return {"drama_id": drama_id, "characters": rel.get("characters") or [],
            "relationships": rel.get("relationships") or [],
            "mermaid": story_context.relationship_map_to_mermaid(rel) if has_chars else ""}


# ---------------------------------------------------------------------------
# Universe wiki
# ---------------------------------------------------------------------------

def _wiki_row(e: dict) -> dict:
    return {"id": e.get("id"), "entry_type": e.get("entry_type"), "name": e.get("name"),
            "aliases": e.get("aliases"), "description": e.get("description"),
            "attributes": e.get("attributes") or {},
            "first_seen_line_idx": e.get("first_seen_line_idx"),
            "known_through_line_idx": e.get("known_through_line_idx")}


def list_wiki(drama_id: int, up_to_line_idx: int = None, entry_type: str = None) -> dict:
    """{drama_id, entry_types, entries} -- entries first introduced after
    up_to_line_idx are hidden (spoiler-free); None means no spoiler
    limit and shows everything -- pass the boundary for spoiler-free."""
    _require_drama(drama_id)
    if entry_type is not None and entry_type not in universe_wiki.ENTRY_TYPES:
        raise InvalidInputError("entry_type must be one of: " + ", ".join(universe_wiki.ENTRY_TYPES))
    _check_line_idx(up_to_line_idx)
    rows = db.list_wiki_entries(drama_id, entry_type=entry_type,
                                spoiler_limit_line_idx=up_to_line_idx)
    return {"drama_id": drama_id, "entry_types": list(universe_wiki.ENTRY_TYPES),
            "entries": [_wiki_row(e) for e in rows]}


def update_wiki(drama_id: int, up_to_line_idx: int = None,
                engine_name: str = None, model: str = None, from_line_idx: int = 0) -> dict:
    """Extracts entries from the lines up to the boundary and upserts each
    by its own (entry_type, name) key -- never by list position. One call
    reads at most MAX_WIKI_CHUNKS_PER_CALL chunks of text lines starting
    at from_line_idx; call again with from_line_idx=next_line_idx while
    `remaining` (text lines still to read) is non-zero. Returns
    {drama_id, updated, remaining, next_line_idx}."""
    drama = _require_drama(drama_id)
    scoped, limit = _scope(_require_lines(drama_id), up_to_line_idx)
    _check_line_idx(from_line_idx)
    todo = [ln for ln in scoped if ln.idx >= (from_line_idx or 0) and (ln.en or ln.zh)]
    batch = todo[:MAX_WIKI_CHUNKS_PER_CALL * WIKI_CHUNK_LINES]
    remaining = len(todo) - len(batch)
    engine = llm_engine(engine_name, model)
    if not batch:
        return {"drama_id": drama_id, "updated": 0, "remaining": 0, "next_line_idx": None}
    batch_limit = batch[-1].idx if remaining else limit
    found = _run_engine(lambda: universe_wiki.extract_wiki_entries(
        batch, engine, batch_limit, drama, existing_entries=db.list_wiki_entries(drama_id)))
    for e in found:
        db.upsert_wiki_entry(
            drama_id, e["entry_type"], e["name"], description=e.get("description"),
            aliases=e.get("aliases"), attributes=e.get("attributes"),
            first_seen_line_idx=e.get("first_seen_line_idx"),
            known_through_line_idx=e.get("known_through_line_idx"))
    return {"drama_id": drama_id, "updated": len(found), "remaining": remaining,
            "next_line_idx": todo[len(batch)].idx if remaining else None}


def clear_wiki(drama_id: int, confirm: bool = False) -> dict:
    """Deletes every wiki entry for the drama; destructive, so the caller
    must pass confirm=True (InvalidInput otherwise)."""
    _require_drama(drama_id)
    if confirm is not True:
        raise InvalidInputError("Clearing the wiki deletes every entry; pass confirm=True.")
    db.clear_wiki(drama_id)
    return {"drama_id": drama_id, "cleared": True}


def export_wiki_markdown(drama_id: int, up_to_line_idx: int = None, entry_type: str = None) -> dict:
    """{filename, media_type, content} -- the shown entries as Markdown,
    with a spoiler note when a boundary is set."""
    drama = _require_drama(drama_id)
    shown = list_wiki(drama_id, up_to_line_idx, entry_type)["entries"]
    note = f"Built from lines 1–{up_to_line_idx + 1}." if up_to_line_idx is not None else ""
    return {"filename": "universe_wiki.md", "media_type": "text/markdown",
            "content": universe_wiki.format_wiki_as_markdown(shown, _title(drama, ""),
                                                             spoiler_note=note)}


# ---------------------------------------------------------------------------
# Q&A
# ---------------------------------------------------------------------------

def ask_about_drama(drama_id: int, question: str, chat_history: list = None,
                    engine_name: str = None, model: str = None) -> dict:
    """One grounded Q&A turn. Stateless: the client keeps the history and
    sends it back ([{role: user|assistant, content}]). Grounded in the
    whole drama's lines (not spoiler-scoped)."""
    import qa
    drama = _require_drama(drama_id)
    question = _text_arg(question, "question", MAX_QUESTION_CHARS)
    history = chat_history if chat_history is not None else []
    if not isinstance(history, list) or len(history) > MAX_CHAT_TURNS:
        raise InvalidInputError(f"chat_history must be a list of at most {MAX_CHAT_TURNS} turns.")
    for m in history:
        if (not isinstance(m, dict) or m.get("role") not in ("user", "assistant")
                or not isinstance(m.get("content"), str) or len(m["content"]) > MAX_CHAT_TURN_CHARS):
            raise InvalidInputError("Each chat_history turn is {role: user|assistant, content: text}"
                                    f" of at most {MAX_CHAT_TURN_CHARS} characters.")
    if sum(len(m["content"]) for m in history) > MAX_CHAT_TOTAL_CHARS:
        raise InvalidInputError(f"chat_history is longer than {MAX_CHAT_TOTAL_CHARS} characters in total.")
    history = [{"role": m["role"], "content": m["content"]} for m in history]
    lines = _require_lines(drama_id)
    engine = llm_engine(engine_name, model)
    answer = _run_engine(lambda: qa.ask_about_drama(question, lines, drama, engine,
                                                    chat_history=history))
    return {"drama_id": drama_id, "answer": answer}
