"""
bulk_translate.py -- submitting the "Bulk (cheaper, slower)" translation, for
work nobody is waiting on, and the other per-line LLM kinds (flag,
consistency, emotion, translation notes).

  - Claude: Message Batches API (50% off, most batches within an hour,
    24h at most).
  - Gemini: Batch API (50% off, 24h target turnaround).
  - DeepSeek: no batch API, but off-peak hours are half price -- the job
    is scheduled into the next off-peak window and runs as a normal
    translation then.

Every batch request is submitted at once, numbered by permanent line id,
and the batch id is saved on disk (db.bulk_jobs) with each
line's id, a hash of its source text and its English at submission, so a
restarted app can pick the batch back up. Results can come back hours
later and in any order, so they're applied by line id only, and only to
a line that still exists, whose source text still hashes the same, and
whose English hasn't changed since -- a merged/deleted line's result is
dropped, a line whose source changed is flagged for review instead, and
an edit made meanwhile is kept. Applying lives in bulk_apply.py, the Reflect
pipeline in bulk_reflect.py, polling and cancel in bulk_poll.py.
"""
import datetime
from lib import clock
import hashlib
import json

from bulk_providers import make_provider, next_deepseek_offpeak_start
import db
import emotion
import source_change
from engine_backends import shared, llm_tasks
import translation_guide as tguide


BULK_ENGINES = ("claude", "gemini", "deepseek")
# Claude's Message Batches and Gemini's Batch API both bill at half the
# normal per-token price.
BATCH_PRICE_FACTOR = 0.5


def zh_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def request_key(drama_id: int, batch_index: int) -> str:
    # Anthropic's custom_id must match ^[a-zA-Z0-9_-]{1,64}$ -- no colons.
    return f"d{drama_id}_b{batch_index}"


def _utcnow():
    return clock.utcnow()


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------

def _target_lines(lines, force_retranslate: bool):
    targets = lines if force_retranslate else [ln for ln in lines if not (ln.en or "").strip()]
    missing_ids = [ln.idx for ln in targets if getattr(ln, "id", None) is None]
    if missing_ids:
        raise ValueError("Bulk mode needs saved lines (a permanent line id on each) -- "
                         f"lines {missing_ids} have none.")
    return targets


def _copy_english_lines(drama_id: int, targets: list) -> list:
    """Copies already-English targets across (no model call) and returns
    the rest, the same split the live translate loop makes."""
    english = [ln for ln in targets if shared.is_english_line(ln)]
    if english:
        for ln in english:
            ln.en = ln.zh
            source_change.clear_for_new_english(ln)
        # Compare-and-set, as bulk apply: an edit saved since the load is kept.
        db.save_lines(drama_id, english, fields=("en", "flag", "flag_note"),
                      only_if_unchanged=True, guard_fields=("zh",))
    return [ln for ln in targets if not shared.is_english_line(ln)]


def build_bulk_requests(drama_id: int, lines, provider, context: dict, batch_size: int,
                        force_retranslate: bool = False, context_window: int = 6,
                        context_window_ahead: int = 3, character_names: dict | None = None):
    """(requests, line_rows) for every batch at once. Each request's
    lines are numbered by their permanent line id, and its key is
    request_key(drama_id, batch_index). Look-back context can only use
    translations that already exist at submission -- a line translated in
    an earlier batch of this same submission doesn't have one yet."""
    character_names = character_names or {}
    # English lines are not sent; submit_bulk_translation copies them across.
    targets = [ln for ln in _target_lines(lines, force_retranslate)
               if not shared.is_english_line(ln)]
    title_language = context.get("source_language", "zh")
    pos_by_id = {ln.id: i for i, ln in enumerate(lines)}
    requests_, line_rows = [], []
    for bi, start in enumerate(range(0, len(targets), batch_size)):
        batch = targets[start:start + batch_size]
        ctx = dict(context)
        first_pos, last_pos = pos_by_id[batch[0].id], pos_by_id[batch[-1].id]
        batch_ids = {ln.id for ln in batch}
        ctx["recent_context"] = [(ln.zh, ln.en) for ln in lines[max(0, first_pos - context_window):first_pos]
                                 if (ln.en or "").strip()] if context_window > 0 else []
        ctx["upcoming_lines"] = [ln.zh for ln in lines[last_pos + 1:last_pos + 1 + context_window_ahead]
                                 if ln.zh.strip() and ln.id not in batch_ids] if context_window_ahead > 0 else []
        speaker_labels = [character_names.get(ln.speaker) for ln in batch]
        # Same per-batch signal the live translation loop already
        # sets, so build_stable_prompt()'s bounded novel-reference retrieval
        # works identically for a bulk-submitted batch.
        ctx["batch_source_lines"] = [ln.zh for ln in batch]
        ctx["speaker_labels"] = speaker_labels
        ids = [ln.id for ln in batch]
        numbered = shared.build_numbered_lines(
            ids, [ln.zh for ln in batch], speaker_labels,
            shared.tagged_line_languages(batch, title_language))
        key = request_key(drama_id, bi)
        requests_.append(provider.build_request(key, ctx, numbered))
        line_rows.extend((ln.id, key, zh_hash(ln.zh), ln.en or "") for ln in batch)
    return requests_, line_rows


def _require_batch_engine(provider, engine_choice: str,
                          hint: str = "bulk mode needs Claude or Gemini"):
    """The batch provider, or the one error every bulk submit raises for an
    engine without a batch API (make_provider returns None for those)."""
    if provider is None:
        raise ValueError(f"{engine_choice} has no batch API -- {hint}.")
    return provider


def submit_bulk_translation(drama_id: int, lines, engine, engine_choice: str, context: dict,
                            provider=None, translate_args: dict | None = None, **build_kwargs) -> int:
    """Submits every batch in one provider batch and records it. Returns
    the bulk job id. The row is written before submitting and marked
    failed if submission raises, so a crash mid-submit never leaves a
    batch the app doesn't know about -- except the narrow window between
    the provider accepting it and the id being saved."""
    provider = _require_batch_engine(provider or make_provider(engine_choice, engine), engine_choice,
                                     "use the off-peak schedule instead")
    _copy_english_lines(drama_id, _target_lines(lines, build_kwargs.get("force_retranslate", False)))
    requests_, line_rows = build_bulk_requests(drama_id, lines, provider, context, **build_kwargs)
    if not requests_:
        raise ValueError("Nothing to translate -- every line already has a translation.")
    bulk_job_id = db.create_bulk_job(drama_id, engine_choice, getattr(engine, "model", ""),
                                     "submitting", line_rows, translate_args=translate_args)
    try:
        batch_id = provider.submit(requests_)
    except Exception as e:
        db.update_bulk_job(bulk_job_id, status="failed",
                           last_error=shared.redact_secrets(str(e)))
        raise
    db.update_bulk_job(bulk_job_id, status="submitted", provider_batch_id=batch_id)
    return bulk_job_id


def schedule_offpeak_translation(drama_id: int, lines, engine_choice: str, model: str,
                                 translate_args: dict, force_retranslate: bool = False,
                                 now: datetime.datetime | None = None) -> int:
    """DeepSeek: records the lines to translate and the next off-peak
    start; the poller runs it as a normal translation once that arrives."""
    targets = _target_lines(lines, force_retranslate)
    if not targets:
        raise ValueError("Nothing to translate -- every line already has a translation.")
    start = next_deepseek_offpeak_start(now or _utcnow())
    return db.create_bulk_job(
        drama_id, engine_choice, model, "scheduled",
        [(ln.id, None, zh_hash(ln.zh), ln.en or "") for ln in targets],
        scheduled_for=start.isoformat(), translate_args=translate_args)


# ---------------------------------------------------------------------------
# Generic per-line LLM batch kinds -- flag_uncertain_lines,
# check_consistency_llm, detect_emotions and generate_translation_notes_llm
# all already batch-process a drama's lines through one LLM call per
# window; this reuses everything above (the providers, db.bulk_jobs/
# bulk_job_lines, the poller, resume_pending, cancel_bulk_job) for them
# too, instead of only translation. Unlike translate_batch's fixed
# "translate with full glossary/style context" shape, each of these is
# already just a single free-form prompt (see call_llm_json) -- so a
# request here is provider.build_prompt_request(key, prompt), not
# provider.build_request(key, context, numbered).

# Every generic kind (flag, consistency, emotion, translation_notes)
# numbers its prompt by permanent line id (id_fn=lambda
# ln: ln.id on the id-aware builders; consistency's own prompt has no
# per-line id at all -- see build_consistency_prompt's docstring), never
# by ln.idx -- exactly the reason translation moved off idx in the
# first place: a bulk result can land hours later, by which point a
# position could point at a completely different line.


def build_generic_bulk_requests(drama_id: int, provider, batches: list, max_tokens: int = 3000):
    """batches: [(ids, prompt_text, batch_lines), ...], already built by
    a kind-specific function below (or a Reflect stage, see
    submit_reflect_stage). Returns (requests, line_rows) the same shape
    db.create_bulk_job expects."""
    requests_, line_rows = [], []
    for bi, (ids, prompt_text, batch_lines) in enumerate(batches):
        key = request_key(drama_id, bi)
        requests_.append(provider.build_prompt_request(key, prompt_text, max_tokens=max_tokens))
        line_rows.extend((ln.id, key, zh_hash(ln.zh)) for ln in batch_lines)
    return requests_, line_rows


def submit_generic_bulk_job(drama_id: int, kind: str, provider, batches: list, engine_choice: str,
                            model: str, state_fn=None, translate_args: dict | None = None,
                            stage: str | None = None, pipeline_id: str | None = None,
                            max_tokens: int = 3000) -> int:
    """Shared submission path for every generic kind above plus each
    Reflect stage. state_fn(ln), if given, snapshots whatever field(s)
    this kind is about to write, as they stand right now -- so its own
    apply step can tell "the user already changed this since submission"
    (keep their edit) apart from "still exactly what it was" (see the
    `bulk_job_lines.state_at_submit` column's own comment). Kinds with no
    such conflict (consistency, translation_notes, and every Reflect
    stage but the last) simply don't pass one."""
    requests_, id_key_hash_rows = build_generic_bulk_requests(drama_id, provider, batches,
                                                              max_tokens=max_tokens)
    if not requests_:
        raise ValueError("Nothing to process -- no eligible lines.")
    lines_by_id = {ln.id: ln for _, _, batch_lines in batches for ln in batch_lines}
    rows = [(lid, key, h, None,
            json.dumps(state_fn(lines_by_id[lid]), ensure_ascii=False) if state_fn else None)
           for lid, key, h in id_key_hash_rows]
    bulk_job_id = db.create_bulk_job(drama_id, engine_choice, model, "submitting", rows,
                                     translate_args=translate_args, kind=kind, stage=stage,
                                     pipeline_id=pipeline_id)
    try:
        batch_id = provider.submit(requests_)
    except Exception as e:
        db.update_bulk_job(bulk_job_id, status="failed",
                           last_error=shared.redact_secrets(str(e)))
        raise
    db.update_bulk_job(bulk_job_id, status="submitted", provider_batch_id=batch_id)
    return bulk_job_id


def submit_bulk_flag(drama_id: int, lines: list, engine, engine_choice: str, provider=None,
                     batch_size: int = 30, source_language=None) -> int:
    """The review-queue pass (llm_tasks.flag_uncertain_lines),
    bulk. state_fn captures each line's own current .flag/.flag_note, so
    a flag the user set or cleared by hand while the batch was pending is
    kept rather than silently overwritten once results arrive."""
    provider = _require_batch_engine(provider or make_provider(engine_choice, engine), engine_choice)
    eligible = llm_tasks.flaggable_lines(lines)
    batches = []
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start:start + batch_size]
        prompt = llm_tasks.build_flag_prompt(batch, lambda ln: ln.id, source_language)
        batches.append(([ln.id for ln in batch], prompt, batch))
    return submit_generic_bulk_job(
        drama_id, "flag", provider, batches, engine_choice, getattr(engine, "model", ""),
        state_fn=lambda ln: {"flag": ln.flag, "flag_note": ln.flag_note}, max_tokens=2000)


def submit_bulk_consistency(drama_id: int, lines: list, engine, engine_choice: str, provider=None,
                            batch_size: int = 60, source_language=None) -> int:
    """The consistency check (llm_tasks.check_consistency_llm),
    bulk. No state_fn -- an issue names a term, not a line, so there's no
    per-line "did the user already change this" to guard against; the
    whole-window drop-if-stale check in apply_consistency_results is
    this kind's only staleness guard."""
    provider = _require_batch_engine(provider or make_provider(engine_choice, engine), engine_choice)
    eligible = [ln for ln in lines if (ln.en or "").strip()]
    batches = []
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start:start + batch_size]
        prompt = llm_tasks.build_consistency_prompt(batch, source_language)
        batches.append(([ln.id for ln in batch], prompt, batch))
    return submit_generic_bulk_job(drama_id, "consistency", provider, batches, engine_choice,
                                   getattr(engine, "model", ""), max_tokens=2000)


def submit_bulk_emotion(drama_id: int, lines: list, engine, engine_choice: str, provider=None,
                        batch_size: int = 40, use_audio_cues: bool = False) -> int:
    """Emotion tagging (emotion.detect_emotions), bulk. state_fn captures
    each line's own current tag (if any), same reasoning as
    submit_bulk_flag's."""
    provider = _require_batch_engine(provider or make_provider(engine_choice, engine), engine_choice)
    eligible = [ln for ln in lines if (ln.zh or "").strip()]
    existing = db.load_emotions(drama_id)  # keyed by CURRENT idx, same as detect_emotions' own return
    existing_by_id = {ln.id: existing.get(ln.idx) for ln in lines}
    batches = []
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start:start + batch_size]
        prompt = emotion.build_emotion_prompt(batch, use_audio_cues=use_audio_cues,
                                              id_fn=lambda ln: ln.id)
        batches.append(([ln.id for ln in batch], prompt, batch))
    return submit_generic_bulk_job(
        drama_id, "emotion", provider, batches, engine_choice, getattr(engine, "model", ""),
        state_fn=lambda ln: existing_by_id.get(ln.id), max_tokens=3000)


def submit_bulk_translation_notes(drama_id: int, lines: list, engine, engine_choice: str,
                                  provider=None, batch_size: int = 40) -> int:
    """Translation notes (translation_guide.generate_translation_notes_llm),
    bulk. No state_fn -- notes are additive (db.save_translation_notes
    upserts per drama+line+term), never overwriting a whole field the way
    flag/emotion do, so there's no "did the user already change this"
    check to make; a stale line is still dropped in
    apply_notes_results, same as every other kind."""
    provider = _require_batch_engine(provider or make_provider(engine_choice, engine), engine_choice)
    eligible = [ln for ln in lines if (ln.en or "").strip()]
    batches = []
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start:start + batch_size]
        prompt = tguide.build_translation_notes_prompt(batch, id_fn=lambda ln: ln.id)
        batches.append(([ln.id for ln in batch], prompt, batch))
    return submit_generic_bulk_job(drama_id, "translation_notes", provider, batches, engine_choice,
                                   getattr(engine, "model", ""), max_tokens=3000)


def _character_names(drama_id: int) -> dict:
    series_id = (db.get_drama(drama_id) or {}).get("series_id")
    return tguide.build_speaker_labels(
        db.list_characters_with_series_names(drama_id),
        db.list_series_characters(series_id) if series_id else [])
