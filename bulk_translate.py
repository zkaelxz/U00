"""
bulk_translate.py -- Step 9's "Bulk (cheaper, slower)" translation, for
work nobody is waiting on.

  - Claude: Message Batches API (50% off, most batches within an hour,
    24h at most).
  - Gemini: Batch API (50% off, 24h target turnaround).
  - DeepSeek: no batch API, but off-peak hours are half price -- the job
    is scheduled into the next off-peak window and runs as a normal
    translation then.

Every batch request is submitted at once, numbered by permanent line id
(Step 2), and the batch id is saved on disk (db.bulk_jobs) with each
line's id, a hash of its source text and its English at submission, so a
restarted app can pick the batch back up. Results can come back hours
later and in any order, so they're applied by line id only, and only to
a line that still exists, whose source text still hashes the same, and
whose English hasn't changed since -- a merged/deleted line's result is
dropped, a line whose source changed is flagged for review instead, and
an edit made meanwhile is kept.
"""
import datetime
import hashlib
import json
import threading
import time
import uuid

import background_jobs
import db
import emotion
import translate_engines
import translation_guide as tguide

BULK_ENGINES = ("claude", "gemini", "deepseek")
# Claude's Message Batches and Gemini's Batch API both bill at half the
# normal per-token price.
BATCH_PRICE_FACTOR = 0.5
POLL_INTERVAL_SECONDS = 60

# DeepSeek's peak hours, UTC, Monday-Friday (datetime.weekday() 0-4);
# every other hour, and weekends, bill at half price. Checked against
# api-docs.deepseek.com/quick_start/pricing in September 2026. Chinese
# public holidays are off-peak too, but aren't listed here -- on one of
# those a job just waits for a window it didn't strictly need to.
DEEPSEEK_PEAK_HOURS_UTC = ((1, 4), (6, 10))


class BulkAuthError(Exception):
    """The provider refused our credentials while polling (the key was
    rotated or revoked after submission) -- shown on the Bulk jobs panel
    instead of retried forever."""


def zh_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def request_key(drama_id: int, batch_index: int) -> str:
    # Anthropic's custom_id must match ^[a-zA-Z0-9_-]{1,64}$ -- no colons.
    return f"d{drama_id}_b{batch_index}"


def poll_job_id(bulk_job_id: int) -> str:
    return f"bulkpoll_{bulk_job_id}"


def _utcnow():
    return datetime.datetime.utcnow()


# ---------------------------------------------------------------------------
# DeepSeek off-peak window
# ---------------------------------------------------------------------------

def is_deepseek_offpeak(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return True
    return not any(start <= now.hour < end for start, end in DEEPSEEK_PEAK_HOURS_UTC)


def next_deepseek_offpeak_start(now: datetime.datetime) -> datetime.datetime:
    """now itself if it's already off-peak, else the end of the current
    peak window."""
    if is_deepseek_offpeak(now):
        return now
    for start, end in DEEPSEEK_PEAK_HOURS_UTC:
        if start <= now.hour < end:
            return now.replace(hour=end, minute=0, second=0, microsecond=0)
    return now


# ---------------------------------------------------------------------------
# Providers: submit / poll / results / cancel
# ---------------------------------------------------------------------------

class ClaudeBatchProvider:
    """Anthropic Message Batches through the official SDK."""

    def __init__(self, engine):
        self.engine = engine
        self.client = engine.client

    def build_request(self, key: str, context: dict, numbered: str) -> dict:
        return {"custom_id": key, "params": self.engine.build_request_params(context, numbered)}

    def build_prompt_request(self, key: str, prompt: str, max_tokens: int = 3000) -> dict:
        """Step 9d: a plain single-user-message request, no system prompt
        and no glossary/style context -- the same shape call_llm_json's
        own Claude branch sends, used by flag/consistency/emotion/
        translation-notes and Reflect's own three passes, none of which
        are the "translate with full context" request build_request
        above is for."""
        return {"custom_id": key,
                "params": {"model": self.engine.model, "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": prompt}]}}

    def _call(self, fn):
        import anthropic
        try:
            return fn()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise BulkAuthError(translate_engines.redact_secrets(str(e))) from e

    def submit(self, requests_: list) -> str:
        return self._call(lambda: self.client.messages.batches.create(requests=requests_)).id

    def poll(self, batch_id: str) -> str:
        batch = self._call(lambda: self.client.messages.batches.retrieve(batch_id))
        return "ended" if batch.processing_status == "ended" else "pending"

    def results(self, batch_id: str):
        """Yields (request_key, text or None, usage dict, error or None)."""
        for r in self._call(lambda: self.client.messages.batches.results(batch_id)):
            if r.result.type == "succeeded":
                msg = r.result.message
                text = "".join(b.text for b in msg.content if b.type == "text").strip()
                yield r.custom_id, text, translate_engines.claude_usage(msg.usage), None
            else:
                yield r.custom_id, None, {}, r.result.type

    def cancel(self, batch_id: str):
        self._call(lambda: self.client.messages.batches.cancel(batch_id))


class GeminiBatchProvider:
    """Gemini Batch API over REST, like GeminiEngine itself -- key in the
    x-goog-api-key header, never the URL."""
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, engine):
        self.engine = engine

    def build_request(self, key: str, context: dict, numbered: str) -> dict:
        return {"request": self.engine.build_request_body(context, numbered),
                "metadata": {"key": key}}

    def build_prompt_request(self, key: str, prompt: str, max_tokens: int = 3000) -> dict:
        """Step 9d: see ClaudeBatchProvider.build_prompt_request's own
        docstring -- same plain-prompt shape, no systemInstruction."""
        return {"request": {"contents": [{"parts": [{"text": prompt}]}]},
                "metadata": {"key": key}}

    def _check(self, resp):
        if resp.status_code in (401, 403):
            raise BulkAuthError(f"Gemini refused the API key (HTTP {resp.status_code}).")
        resp.raise_for_status()
        return resp.json()

    def _headers(self):
        return {"x-goog-api-key": self.engine.api_key}

    def submit(self, requests_: list) -> str:
        import requests
        resp = requests.post(
            f"{self.BASE}/models/{self.engine.model}:batchGenerateContent",
            headers=self._headers(), timeout=120,
            json={"batch": {"display_name": "baihe-bulk-translation",
                            "input_config": {"requests": {"requests": requests_}}}})
        return self._check(resp)["name"]

    def _get(self, batch_id: str) -> dict:
        import requests
        return self._check(requests.get(f"{self.BASE}/{batch_id}", headers=self._headers(),
                                        timeout=60))

    @staticmethod
    def _state(data: dict) -> str:
        for holder in (data, data.get("metadata") or {}, data.get("response") or {}):
            state = holder.get("state")
            if state:
                return state
        return ""

    def poll(self, batch_id: str) -> str:
        data = self._get(batch_id)
        state = self._state(data)
        if state.endswith("SUCCEEDED"):
            return "ended"
        if state.endswith(("FAILED", "EXPIRED", "CANCELLED")):
            return "failed:" + state
        return "ended" if data.get("done") else "pending"

    @staticmethod
    def _inlined(data: dict) -> list:
        resp = data.get("response") or {}
        for holder in (resp, resp.get("output") or {}):
            inl = holder.get("inlinedResponses")
            if isinstance(inl, dict):
                inl = inl.get("inlinedResponses")
            if isinstance(inl, list):
                return inl
        return []

    def results(self, batch_id: str):
        for item in self._inlined(self._get(batch_id)):
            # A response is only ever attributed through the key we sent
            # with its request -- never by its position in the list.
            key = (item.get("metadata") or {}).get("key")
            if item.get("error") or not item.get("response"):
                yield key, None, {}, json.dumps(item.get("error") or "no response")
                continue
            r = item["response"]
            try:
                text = r["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError, TypeError):
                yield key, None, {}, "empty response"
                continue
            yield key, text, translate_engines.gemini_usage(r.get("usageMetadata")), None

    def cancel(self, batch_id: str):
        import requests
        self._check(requests.post(f"{self.BASE}/{batch_id}:cancel", headers=self._headers(),
                                  json={}, timeout=60))


def make_provider(engine_choice: str, engine):
    if engine_choice == "claude":
        return ClaudeBatchProvider(engine)
    if engine_choice == "gemini":
        return GeminiBatchProvider(engine)
    return None


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


def build_bulk_requests(drama_id: int, lines, provider, context: dict, batch_size: int = 20,
                        force_retranslate: bool = False, context_window: int = 6,
                        context_window_ahead: int = 3, character_names: dict = None):
    """(requests, line_rows) for every batch at once. Each request's
    lines are numbered by their permanent line id, and its key is
    request_key(drama_id, batch_index). Look-back context can only use
    translations that already exist at submission -- a line translated in
    an earlier batch of this same submission doesn't have one yet."""
    character_names = character_names or {}
    targets = _target_lines(lines, force_retranslate)
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
        # Step 50: same per-batch signal the live translation loop already
        # sets, so build_stable_prompt()'s bounded novel-reference retrieval
        # works identically for a bulk-submitted batch.
        ctx["batch_source_lines"] = [ln.zh for ln in batch]
        ctx["speaker_labels"] = speaker_labels
        ids = [ln.id for ln in batch]
        numbered = translate_engines.build_numbered_lines(ids, [ln.zh for ln in batch], speaker_labels)
        key = request_key(drama_id, bi)
        requests_.append(provider.build_request(key, ctx, numbered))
        line_rows.extend((ln.id, key, zh_hash(ln.zh), ln.en or "") for ln in batch)
    return requests_, line_rows


def submit_bulk_translation(drama_id: int, lines, engine, engine_choice: str, context: dict,
                            provider=None, translate_args: dict = None, **build_kwargs) -> int:
    """Submits every batch in one provider batch and records it. Returns
    the bulk job id. The row is written before submitting and marked
    failed if submission raises, so a crash mid-submit never leaves a
    batch the app doesn't know about -- except the narrow window between
    the provider accepting it and the id being saved."""
    provider = provider or make_provider(engine_choice, engine)
    if provider is None:
        raise ValueError(f"{engine_choice} has no batch API -- use the off-peak schedule instead.")
    requests_, line_rows = build_bulk_requests(drama_id, lines, provider, context, **build_kwargs)
    if not requests_:
        raise ValueError("Nothing to translate -- every line already has a translation.")
    bulk_job_id = db.create_bulk_job(drama_id, engine_choice, getattr(engine, "model", ""),
                                     "submitting", line_rows, translate_args=translate_args)
    try:
        batch_id = provider.submit(requests_)
    except Exception as e:
        db.update_bulk_job(bulk_job_id, status="failed",
                           last_error=translate_engines.redact_secrets(str(e)))
        raise
    db.update_bulk_job(bulk_job_id, status="submitted", provider_batch_id=batch_id)
    return bulk_job_id


def schedule_offpeak_translation(drama_id: int, lines, engine_choice: str, model: str,
                                 translate_args: dict, force_retranslate: bool = False,
                                 now: datetime.datetime = None) -> int:
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
# Step 9d: generic per-line LLM batch kinds -- flag_uncertain_lines,
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
# by ln.idx -- exactly the reason Step 2 moved translation off idx in the
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
                            model: str, state_fn=None, translate_args: dict = None,
                            stage: str = None, pipeline_id: str = None,
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
                           last_error=translate_engines.redact_secrets(str(e)))
        raise
    db.update_bulk_job(bulk_job_id, status="submitted", provider_batch_id=batch_id)
    return bulk_job_id


def _log_generic_usage(job: dict, usage: dict, usage_kind: str):
    if usage and usage.get("input_tokens"):
        db.log_usage(
            job["drama_id"], job["engine"], job["model"], usage_kind,
            usage.get("input_tokens", 0), usage.get("output_tokens", 0),
            translate_engines.estimate_cost(
                job["model"], usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0))
            * BATCH_PRICE_FACTOR,
            cache_read_tokens=usage.get("cache_read_tokens", 0))


def submit_bulk_flag(drama_id: int, lines: list, engine, engine_choice: str, provider=None,
                     batch_size: int = 30) -> int:
    """The review-queue pass (translate_engines.flag_uncertain_lines),
    bulk. state_fn captures each line's own current .flag/.flag_note, so
    a flag the user set or cleared by hand while the batch was pending is
    kept rather than silently overwritten once results arrive."""
    provider = provider or make_provider(engine_choice, engine)
    if provider is None:
        raise ValueError(f"{engine_choice} has no batch API -- bulk mode needs Claude or Gemini.")
    eligible = [ln for ln in lines if (ln.en or "").strip()]
    batches = []
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start:start + batch_size]
        prompt = translate_engines.build_flag_prompt(batch, id_fn=lambda ln: ln.id)
        batches.append(([ln.id for ln in batch], prompt, batch))
    return submit_generic_bulk_job(
        drama_id, "flag", provider, batches, engine_choice, getattr(engine, "model", ""),
        state_fn=lambda ln: {"flag": ln.flag, "flag_note": ln.flag_note}, max_tokens=2000)


def submit_bulk_consistency(drama_id: int, lines: list, engine, engine_choice: str, provider=None,
                            batch_size: int = 60) -> int:
    """The consistency check (translate_engines.check_consistency_llm),
    bulk. No state_fn -- an issue names a term, not a line, so there's no
    per-line "did the user already change this" to guard against; the
    whole-window drop-if-stale check in apply_consistency_results is
    this kind's only staleness guard."""
    provider = provider or make_provider(engine_choice, engine)
    if provider is None:
        raise ValueError(f"{engine_choice} has no batch API -- bulk mode needs Claude or Gemini.")
    eligible = [ln for ln in lines if (ln.en or "").strip()]
    batches = []
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start:start + batch_size]
        prompt = translate_engines.build_consistency_prompt(batch)
        batches.append(([ln.id for ln in batch], prompt, batch))
    return submit_generic_bulk_job(drama_id, "consistency", provider, batches, engine_choice,
                                   getattr(engine, "model", ""), max_tokens=2000)


def submit_bulk_emotion(drama_id: int, lines: list, engine, engine_choice: str, provider=None,
                        batch_size: int = 40, use_audio_cues: bool = False) -> int:
    """Emotion tagging (emotion.detect_emotions), bulk. state_fn captures
    each line's own current tag (if any), same reasoning as
    submit_bulk_flag's."""
    provider = provider or make_provider(engine_choice, engine)
    if provider is None:
        raise ValueError(f"{engine_choice} has no batch API -- bulk mode needs Claude or Gemini.")
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
    provider = provider or make_provider(engine_choice, engine)
    if provider is None:
        raise ValueError(f"{engine_choice} has no batch API -- bulk mode needs Claude or Gemini.")
    eligible = [ln for ln in lines if (ln.en or "").strip()]
    batches = []
    for start in range(0, len(eligible), batch_size):
        batch = eligible[start:start + batch_size]
        prompt = tguide.build_translation_notes_prompt(batch, id_fn=lambda ln: ln.id)
        batches.append(([ln.id for ln in batch], prompt, batch))
    return submit_generic_bulk_job(drama_id, "translation_notes", provider, batches, engine_choice,
                                   getattr(engine, "model", ""), max_tokens=3000)


# ---------------------------------------------------------------------------
# Step 9d: Reflect mode, bulk -- the same faithfulness -> reflection ->
# expressiveness pipeline as translate_engines.reflect_translate_batch,
# but as three SEQUENTIAL bulk submissions instead of three in-process
# calls. Confirmed directly against both Claude's Message Batches and
# Gemini's Batch API: neither has a way to submit pass 2 once pass 1's
# own results are back, all within one batch -- there is no such thing
# as a batch that depends on another batch's output. So this submits
# stage "faithful" now (submit_reflect_pipeline); once ITS results are
# in, apply_reflect_stage_results persists them and auto-submits stage
# "reflect", using the SAME pipeline_id; once THAT stage's results are
# in, it auto-submits stage "expressive", which is the only stage that
# actually writes anything to a line's .en (the other two only ever
# populate bulk_job_lines.result_text for the NEXT stage to read).
#
# A real, deliberate simplification versus the live path: every stage's
# prompt here omits recent_context/upcoming_lines (the surrounding-line
# steering text build_bulk_requests still gives plain bulk translation).
# Recomputing that accurately across three submissions that can each take
# up to 24h, on a drama whose OTHER lines may themselves be translated or
# edited in between, isn't worth the complexity for what's already a
# cheaper/slower tradeoff mode -- and it's genuinely supplementary
# context, not the actual cross-pass information a Reflect pass depends
# on (the faithfulness draft and reflection critique THEMSELVES, which
# this pipeline carries through exactly via bulk_job_lines.result_text,
# unaffected by this simplification).
# ---------------------------------------------------------------------------

def _reflect_instructions(translate_args: dict) -> str:
    args = translate_args or {}
    instructions = translate_engines.build_llm_instructions(
        args.get("style_note", ""), args.get("drama_meta", {}),
        locale=args.get("locale", "en-US"), glossary_terms=args.get("glossary_terms"),
        style_guidelines=args.get("style_guidelines", ""))
    return instructions


def sibling_stage_job(pipeline_id: str, stage: str):
    """The other bulk_jobs row from the same Reflect pipeline for a given
    stage, or None if it doesn't exist (yet, or ever -- e.g. every line
    dropped out before reaching it)."""
    for job in db.list_bulk_jobs(pipeline_id=pipeline_id):
        if job.get("stage") == stage:
            return job
    return None


def submit_reflect_stage(drama_id: int, stage: str, provider, batches: list, engine_choice: str,
                         model: str, pipeline_id: str, translate_args: dict = None,
                         en_at_submit_by_id: dict = None, max_tokens: int = 4000) -> int:
    """batches: [(id_zh_pairs, prompt_text), ...], id_zh_pairs = [(line_id,
    zh), ...] -- zh is carried alongside each id purely so this stage's
    own bulk_job_lines rows record a real zh_hash to compare against
    later, without needing a whole Line object here. en_at_submit_by_id:
    only ever populated for stage="expressive" (see
    apply_reflect_stage_results) -- every earlier stage still records
    whatever value is given (harmless; unused until expressive's own
    "kept your edit" check)."""
    requests_, rows = [], []
    for bi, (id_zh_pairs, prompt_text) in enumerate(batches):
        key = request_key(drama_id, bi)
        requests_.append(provider.build_prompt_request(key, prompt_text, max_tokens=max_tokens))
        for lid, zh in id_zh_pairs:
            en_at_submit = (en_at_submit_by_id or {}).get(lid)
            rows.append((lid, key, zh_hash(zh), en_at_submit))
    if not requests_:
        raise ValueError("Nothing to process -- no eligible lines.")
    bulk_job_id = db.create_bulk_job(drama_id, engine_choice, model, "submitting", rows,
                                     translate_args=translate_args, kind="reflect", stage=stage,
                                     pipeline_id=pipeline_id)
    try:
        batch_id = provider.submit(requests_)
    except Exception as e:
        db.update_bulk_job(bulk_job_id, status="failed",
                           last_error=translate_engines.redact_secrets(str(e)))
        raise
    db.update_bulk_job(bulk_job_id, status="submitted", provider_batch_id=batch_id)
    return bulk_job_id


def submit_reflect_pipeline(drama_id: int, lines: list, engine, engine_choice: str,
                            translate_args: dict, provider=None, batch_size: int = 20,
                            force_retranslate: bool = False) -> int:
    """Submits stage "faithful", the pipeline's own first bulk job.
    Returns its bulk_job_id -- the Bulk jobs panel shows this one first;
    stages "reflect" and "expressive" only exist once the prior stage's
    own results have actually come back (see apply_reflect_stage_results)."""
    provider = provider or make_provider(engine_choice, engine)
    if provider is None:
        raise ValueError(f"{engine_choice} has no batch API -- bulk mode needs Claude or Gemini.")
    targets = _target_lines(lines, force_retranslate)
    if not targets:
        raise ValueError("Nothing to translate -- every line already has a translation.")
    instructions = _reflect_instructions(translate_args)
    pipeline_id = f"reflect_{drama_id}_{uuid.uuid4().hex[:12]}"
    batches = []
    for start in range(0, len(targets), batch_size):
        batch = targets[start:start + batch_size]
        ids = [ln.id for ln in batch]
        prompt = translate_engines.build_reflect_faithful_prompt(
            instructions, "", ids, {ln.id: ln.zh for ln in batch})
        batches.append(([(ln.id, ln.zh) for ln in batch], prompt))
    en_at_submit_by_id = {ln.id: ln.en or "" for ln in targets}
    return submit_reflect_stage(drama_id, "faithful", provider, batches, engine_choice,
                                getattr(engine, "model", ""), pipeline_id,
                                translate_args=translate_args, en_at_submit_by_id=en_at_submit_by_id)


# ---------------------------------------------------------------------------
# Applying results
# ---------------------------------------------------------------------------

def _parse_strict(text: str, expected_ids: list) -> dict:
    """Only an id-keyed JSON object is accepted for a bulk result (as on
    the live path). Keys outside this request's own ids are ignored."""
    stripped = (text or "").strip()
    for fence in ("```json", "```"):
        stripped = stripped.replace(fence, "")
    return translate_engines.parse_id_keyed_json(stripped, expected_ids)


def apply_bulk_results(bulk_job_id: int, results) -> dict:
    """Applies (request_key, text, usage, error) results to the drama's
    CURRENT lines, by line id. Returns counts of what happened."""
    job = db.get_bulk_job(bulk_job_id)
    job_lines = db.list_bulk_job_lines(bulk_job_id)
    ids_by_key, row_by_id = {}, {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
        row_by_id[r["line_id"]] = r
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    args = job.get("translate_args") or {}
    enforced = [t for t in (args.get("glossary_terms") or []) if t.get("enforce_exact")]
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "missing": 0, "failed_requests": 0, "unknown_requests": 0}
    changed_flag = False

    for key, text, usage, error in results:
        if usage and usage.get("input_tokens"):
            db.log_usage(
                job["drama_id"], job["engine"], job["model"], "translate_bulk",
                usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                translate_engines.estimate_cost(
                    job["model"], usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                    usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0))
                * BATCH_PRICE_FACTOR,
                cache_read_tokens=usage.get("cache_read_tokens", 0))
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        request_ids = ids_by_key[key]
        if error or text is None:
            counts["failed_requests"] += 1
            counts["missing"] += len(request_ids)
            continue
        parsed = _parse_strict(text, request_ids)
        for lid in request_ids:
            translation = (parsed.get(str(lid)) or "").strip()
            if not translation:
                counts["missing"] += 1
                continue
            ln = cur_by_id.get(lid)
            if ln is None:
                counts["dropped_deleted"] += 1
                continue
            if zh_hash(ln.zh) != row_by_id[lid]["zh_hash"]:
                counts["flagged_source_changed"] += 1
                if not ln.flag:
                    ln.flag = "bulk_source_changed"
                    ln.flag_note = f"Bulk result not applied: {translation}"
                    changed_flag = True
                continue
            if (ln.en or "") != (row_by_id[lid]["en_at_submit"] or ""):
                counts["kept_your_edit"] += 1
                continue
            if enforced:
                translation = tguide.apply_hard_term_substitutions(translation, enforced)
            ln.en = translation
            counts["applied"] += 1

    if counts["applied"]:
        import subtitle_formats
        changed_flag = subtitle_formats.flag_dense_lines(current) > 0 or changed_flag
    if counts["applied"] or changed_flag:
        db.save_lines(job["drama_id"], current, fields=("en", "flag", "flag_note"))
    if counts["applied"]:
        db.save_translation_version(
            job["drama_id"], current, label=f"{job['engine']} bulk · {args.get('style_preset', '')}",
            engine=job["engine"], model=job["model"] or "", make_active=True)
        # Step 25d item 13: same root cause as item 4's -- a batch that
        # applied SOME lines (dropped/flagged/kept-your-edit lines aside)
        # used to mark the whole drama "translated" even with lines still
        # missing, same as the CLI/Workspace bug Step 25c already fixed
        # there via this same untranslated_line_count() == 0 gate.
        _status = dict(translation_engine=job["engine"])
        if untranslated_line_count(job["drama_id"]) == 0:
            _status["status"] = "translated"
        db.update_drama(job["drama_id"], **_status)
    return counts


def _stale_line(ln, expected_hash: str) -> bool:
    """A line whose source has been deleted, or whose text no longer
    hashes to what it was at submission -- the drop-or-flag guard every
    kind below applies before touching anything, same as apply_bulk_results'
    own zh_hash check."""
    return ln is None or zh_hash(ln.zh) != expected_hash


def apply_reflect_stage_results(bulk_job_id: int, results, engine=None) -> dict:
    """Dispatches to the right stage handler -- see the module's own
    Reflect-pipeline comment above for the three-stage shape. engine is
    needed by every stage but "expressive" (the last), to submit the
    pipeline's next stage; if it's None (e.g. no key available right
    now), this stage's own results still apply/persist, the pipeline
    just doesn't advance -- the same "needs_key" situation
    resume_pending already surfaces for a plain bulk job."""
    job = db.get_bulk_job(bulk_job_id)
    stage = job.get("stage")
    if stage == "faithful":
        return _apply_reflect_faithful(job, results, engine)
    if stage == "reflect":
        return _apply_reflect_reflection(job, results, engine)
    if stage == "expressive":
        return _apply_reflect_expressive(job, results)
    raise ValueError(f"Unknown Reflect stage: {stage}")


def _apply_reflect_faithful(job: dict, results, engine) -> dict:
    job_lines = db.list_bulk_job_lines(job["id"])
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    counts = {"drafted": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "failed_requests": 0, "unknown_requests": 0}
    result_texts, en_at_submit_by_id = {}, {}
    surviving_ids = []
    changed_flag = False

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "reflect_faithful_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        expected_ids = [str(lid) for lid in ids_by_key[key]]
        draft_map = _parse_strict(text, expected_ids)
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                if ln is not None:
                    counts["flagged_source_changed"] += 1
                    if not ln.flag:
                        ln.flag = "bulk_source_changed"
                        ln.flag_note = "Source text changed while a bulk Reflect job was pending"
                        changed_flag = True
                else:
                    counts["dropped_deleted"] += 1
                continue
            draft = (draft_map.get(str(lid)) or "").strip()
            if not draft:
                continue  # no draft for this line -- it simply doesn't continue to the next stage
            result_texts[lid] = draft
            en_at_submit_by_id[lid] = row["en_at_submit"]
            surviving_ids.append(lid)
            counts["drafted"] += 1

    if changed_flag:
        db.save_lines(job["drama_id"], current, fields=("flag", "flag_note"))
    db.set_bulk_job_line_result_texts(job["id"], result_texts)

    if surviving_ids and engine is not None:
        _advance_to_reflection_stage(job, surviving_ids, result_texts, en_at_submit_by_id, engine)
    return counts


def _advance_to_reflection_stage(job: dict, ids: list, draft_by_id: dict, en_at_submit_by_id: dict,
                                 engine, batch_size: int = 20):
    provider = make_provider(job["engine"], engine)
    if provider is None:
        return
    instructions = _reflect_instructions(job.get("translate_args"))
    cur_by_id = {ln.id: ln for ln in db.load_line_objects(job["drama_id"])}
    batches = []
    for start in range(0, len(ids), batch_size):
        chunk = [lid for lid in ids[start:start + batch_size] if lid in cur_by_id]
        if not chunk:
            continue
        zh_by_id = {lid: cur_by_id[lid].zh for lid in chunk}
        prompt = translate_engines.build_reflect_reflection_prompt(
            instructions, "", chunk, zh_by_id, {lid: draft_by_id[lid] for lid in chunk})
        batches.append(([(lid, zh_by_id[lid]) for lid in chunk], prompt))
    if not batches:
        return
    try:
        submit_reflect_stage(job["drama_id"], "reflect", provider, batches, job["engine"],
                             job["model"], job["pipeline_id"], translate_args=job.get("translate_args"),
                             en_at_submit_by_id={lid: en_at_submit_by_id[lid] for lid in ids},
                             max_tokens=2000)
    except Exception as exc:
        # Only a provider.submit failure is recorded on the new job row by
        # submit_reflect_stage; failures before that (building requests,
        # creating the row) leave no trace, so log them. The caller's stage
        # already applied and must still be marked "applied".
        try:
            import applog
            applog.get_logger().warning(
                "reflect stage %s could not be submitted for drama %s: %s", "reflect", job["drama_id"],
                translate_engines.redact_secrets(str(exc)))
        except Exception:
            pass


def _apply_reflect_reflection(job: dict, results, engine) -> dict:
    job_lines = db.list_bulk_job_lines(job["id"])
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    counts = {"critiqued": 0, "no_critique_needed": 0, "dropped_deleted": 0,
              "flagged_source_changed": 0, "failed_requests": 0, "unknown_requests": 0}
    result_texts, en_at_submit_by_id = {}, {}
    surviving_ids = []
    changed_flag = False

    faithful_job = sibling_stage_job(job["pipeline_id"], "faithful")
    draft_by_id = ({r["line_id"]: r["result_text"] for r in db.list_bulk_job_lines(faithful_job["id"])}
                  if faithful_job else {})

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "reflect_reflection_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        expected_ids = [str(lid) for lid in ids_by_key[key]]
        critique_map = _parse_strict(text, expected_ids)
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                if ln is not None:
                    counts["flagged_source_changed"] += 1
                    if not ln.flag:
                        ln.flag = "bulk_source_changed"
                        ln.flag_note = "Source text changed while a bulk Reflect job was pending"
                        changed_flag = True
                else:
                    counts["dropped_deleted"] += 1
                continue
            critique = (critique_map.get(str(lid)) or "").strip()
            if critique:
                result_texts[lid] = critique
                counts["critiqued"] += 1
            else:
                # A line the model chose not to critique is a normal,
                # expected outcome here (see reflect_translate_batch's
                # own docstring) -- it still continues to expressiveness,
                # just with no "Critique:" line in that prompt.
                counts["no_critique_needed"] += 1
            en_at_submit_by_id[lid] = row["en_at_submit"]
            surviving_ids.append(lid)

    if changed_flag:
        db.save_lines(job["drama_id"], current, fields=("flag", "flag_note"))
    if result_texts:
        db.set_bulk_job_line_result_texts(job["id"], result_texts)

    if surviving_ids and engine is not None:
        _advance_to_expressive_stage(job, surviving_ids, draft_by_id, result_texts,
                                     en_at_submit_by_id, engine)
    return counts


def _advance_to_expressive_stage(job: dict, ids: list, draft_by_id: dict, critique_by_id: dict,
                                 en_at_submit_by_id: dict, engine, batch_size: int = 20):
    provider = make_provider(job["engine"], engine)
    if provider is None:
        return
    instructions = _reflect_instructions(job.get("translate_args"))
    cur_by_id = {ln.id: ln for ln in db.load_line_objects(job["drama_id"])}
    batches = []
    for start in range(0, len(ids), batch_size):
        chunk = [lid for lid in ids[start:start + batch_size] if lid in cur_by_id]
        if not chunk:
            continue
        zh_by_id = {lid: cur_by_id[lid].zh for lid in chunk}
        prompt = translate_engines.build_reflect_expressive_prompt(
            instructions, "", chunk, zh_by_id, {lid: draft_by_id.get(lid, "") for lid in chunk},
            {lid: critique_by_id.get(lid) for lid in chunk})
        batches.append(([(lid, zh_by_id[lid]) for lid in chunk], prompt))
    if not batches:
        return
    try:
        submit_reflect_stage(job["drama_id"], "expressive", provider, batches, job["engine"],
                             job["model"], job["pipeline_id"], translate_args=job.get("translate_args"),
                             en_at_submit_by_id={lid: en_at_submit_by_id[lid] for lid in ids},
                             max_tokens=4000)
    except Exception as exc:
        # See _advance_to_reflection_stage: stage 2 already applied, but a
        # failure before the job row exists is otherwise invisible.
        try:
            import applog
            applog.get_logger().warning(
                "reflect stage %s could not be submitted for drama %s: %s", "expressive", job["drama_id"],
                translate_engines.redact_secrets(str(exc)))
        except Exception:
            pass


def _apply_reflect_expressive(job: dict, results) -> dict:
    """The only Reflect stage that writes anything to a line -- the same
    drop-or-flag-on-source-changed and "kept your edit" handling as
    apply_bulk_results, since this is what a Reflect bulk job's own
    en_at_submit means: the line's English as it stood when the ORIGINAL
    faithfulness stage was submitted (carried through stage 2 unchanged,
    not re-snapshotted at every stage), same as an edit made any time
    during a long-running pipeline being respected, not just one made
    after the very last stage happened to be submitted. Also saves the
    reflection critique as a translation note, same as the live path's
    own notes_cb (translate_engines.translate_lines_with_engine)."""
    job_lines = db.list_bulk_job_lines(job["id"])
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    args = job.get("translate_args") or {}
    enforced = [t for t in (args.get("glossary_terms") or []) if t.get("enforce_exact")]

    reflect_job = sibling_stage_job(job["pipeline_id"], "reflect")
    critique_by_id = ({r["line_id"]: r["result_text"] for r in db.list_bulk_job_lines(reflect_job["id"])
                      if r["result_text"]} if reflect_job else {})
    faithful_job = sibling_stage_job(job["pipeline_id"], "faithful")
    draft_by_id = ({r["line_id"]: r["result_text"] for r in db.list_bulk_job_lines(faithful_job["id"])}
                  if faithful_job else {})

    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "failed_requests": 0, "unknown_requests": 0}
    notes = []
    changed_flag = False

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "reflect_expressive_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        request_ids = ids_by_key[key]
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        expected_ids = [str(lid) for lid in request_ids]
        final_map = _parse_strict(text, expected_ids)
        for lid in request_ids:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                if ln is not None:
                    counts["flagged_source_changed"] += 1
                    if not ln.flag:
                        ln.flag = "bulk_source_changed"
                        ln.flag_note = "Source text changed while a bulk Reflect job was pending"
                        changed_flag = True
                else:
                    counts["dropped_deleted"] += 1
                continue
            if (ln.en or "") != (row["en_at_submit"] or ""):
                counts["kept_your_edit"] += 1
                continue
            translation = (final_map.get(str(lid)) or draft_by_id.get(lid, "")).strip()
            if not translation:
                continue
            if enforced:
                translation = tguide.apply_hard_term_substitutions(translation, enforced)
            ln.en = translation
            counts["applied"] += 1
            critique = critique_by_id.get(lid)
            if critique:
                notes.append({"line_id": lid, "term": "", "note_type": "reflection", "note": critique})

    if counts["applied"]:
        import subtitle_formats
        changed_flag = subtitle_formats.flag_dense_lines(current) > 0 or changed_flag
    if counts["applied"] or changed_flag:
        db.save_lines(job["drama_id"], current, fields=("en", "flag", "flag_note"))
    if counts["applied"]:
        db.save_translation_version(
            job["drama_id"], current, label=f"{job['engine']} bulk reflect", engine=job["engine"],
            model=job["model"] or "", make_active=True)
        # Step 25d item 13: see apply_bulk_results' own comment above.
        _status = dict(translation_engine=job["engine"])
        if untranslated_line_count(job["drama_id"]) == 0:
            _status["status"] = "translated"
        db.update_drama(job["drama_id"], **_status)
    if notes:
        db.save_translation_notes(job["drama_id"], notes)
    return counts


def apply_flag_results(bulk_job_id: int, results) -> dict:
    """Applies review-queue flags (submit_bulk_flag) by line id, with the
    same drop-or-flag-on-source-changed handling as apply_bulk_results,
    plus its own "kept your edit" check: a line whose .flag/.flag_note the
    user already changed since submission (state_at_submit) is left
    alone rather than overwritten."""
    job = db.get_bulk_job(bulk_job_id)
    job_lines = db.list_bulk_job_lines(bulk_job_id)
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "failed_requests": 0, "unknown_requests": 0}

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "flag_review_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        flagged = translate_engines.parse_json_array(text, 0)
        by_id = {}
        if isinstance(flagged, list):
            for f in flagged:
                if isinstance(f, dict) and f.get("line_idx") is not None:
                    try:
                        by_id[int(f["line_idx"])] = f
                    except (TypeError, ValueError):
                        continue
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                if ln is not None:
                    counts["flagged_source_changed"] += 1
                    if not ln.flag:
                        ln.flag = "bulk_source_changed"
                        ln.flag_note = "Source text changed while bulk flagging was pending -- re-run it"
                else:
                    counts["dropped_deleted"] += 1
                continue
            state = json.loads(row["state_at_submit"]) if row.get("state_at_submit") else {}
            if (ln.flag, ln.flag_note or "") != (state.get("flag"), state.get("flag_note") or ""):
                counts["kept_your_edit"] += 1
                continue
            f = by_id.get(lid)
            if f is None:
                continue  # the model didn't flag this line -- nothing to apply, not an error
            reason = (f.get("reason") if f.get("reason") in translate_engines.FLAG_REASONS
                     else "uncertain_translation")
            ln.flag = reason
            ln.flag_note = f.get("note", "")
            counts["applied"] += 1

    if counts["applied"] or counts["flagged_source_changed"]:
        db.save_lines(job["drama_id"], current, fields=("flag", "flag_note"))
    return counts


def apply_consistency_results(bulk_job_id: int, results) -> dict:
    """Applies consistency-check issues (submit_bulk_consistency). An
    issue names a TERM, not a line (see build_consistency_prompt's own
    docstring), so there's no single line id to drop a stale issue by --
    instead, a whole WINDOW's issues are dropped together if any line in
    that window was edited or deleted since submission. Replaces the
    drama's consistency issues wholesale, same as a live check already
    does (db.save_consistency_issues) -- but only once at least one
    window's results were actually usable, so a bulk job that turned out
    entirely stale doesn't wipe out a still-valid earlier check."""
    job = db.get_bulk_job(bulk_job_id)
    job_lines = db.list_bulk_job_lines(bulk_job_id)
    ids_by_key, hash_by_id = {}, {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
        hash_by_id[r["line_id"]] = r["zh_hash"]
    cur_by_id = {ln.id: ln for ln in db.load_line_objects(job["drama_id"])}
    counts = {"issues_found": 0, "dropped_windows": 0, "failed_requests": 0, "unknown_requests": 0}
    issues = []
    usable_windows = 0

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "consistency_check_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        window_ids = ids_by_key[key]
        if any(_stale_line(cur_by_id.get(lid), hash_by_id[lid]) for lid in window_ids):
            counts["dropped_windows"] += 1
            continue
        usable_windows += 1
        window_issues = translate_engines.parse_json_array(text, 0)
        if isinstance(window_issues, list):
            issues.extend(i for i in window_issues if isinstance(i, dict) and i.get("term"))

    counts["issues_found"] = len(issues)
    if usable_windows:
        db.save_consistency_issues(job["drama_id"], issues)
    return counts


def apply_emotion_results(bulk_job_id: int, results) -> dict:
    """Applies emotion tags (submit_bulk_emotion) by line id, with the
    same drop-or-flag-on-source-changed handling as apply_bulk_results,
    plus its own "kept your edit" check against whatever tag the line
    already had at submission (db.save_emotions is itself a per-line
    upsert, so applying this incrementally never disturbs any OTHER
    line's tag, live-set or from a different bulk job)."""
    job = db.get_bulk_job(bulk_job_id)
    job_lines = db.list_bulk_job_lines(bulk_job_id)
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    current = db.load_line_objects(job["drama_id"])
    cur_by_id = {ln.id: ln for ln in current}
    existing = db.load_emotions(job["drama_id"])
    existing_by_id = {ln.id: existing.get(ln.idx) for ln in current}
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "kept_your_edit": 0, "failed_requests": 0, "unknown_requests": 0}
    to_save = {}
    changed_flag = False

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "emotion_detect_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        tagged = emotion.parse_emotion_tags(text)
        for lid in ids_by_key[key]:
            row = row_by_id[lid]
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row["zh_hash"]):
                if ln is not None:
                    counts["flagged_source_changed"] += 1
                    if not ln.flag:
                        ln.flag = "bulk_source_changed"
                        ln.flag_note = "Source text changed while bulk emotion tagging was pending"
                        changed_flag = True
                else:
                    counts["dropped_deleted"] += 1
                continue
            state = json.loads(row["state_at_submit"]) if row.get("state_at_submit") else None
            if state != existing_by_id.get(lid):
                counts["kept_your_edit"] += 1
                continue
            tag = tagged.get(lid)
            if tag is None:
                continue
            to_save[lid] = tag
            counts["applied"] += 1

    if to_save:
        # db.save_emotions expects its emotion_map keyed by line_idx (with
        # id_by_idx resolving idx -> id), not by line_id directly -- to_save
        # is keyed by line_id, so it's re-keyed by each line's CURRENT idx
        # here rather than passing line ids where idx values are expected.
        by_idx = {cur_by_id[lid].idx: tag for lid, tag in to_save.items()}
        id_by_idx = {cur_by_id[lid].idx: lid for lid in to_save}
        db.save_emotions(job["drama_id"], by_idx, id_by_idx=id_by_idx)
    if changed_flag:
        db.save_lines(job["drama_id"], current, fields=("flag", "flag_note"))
    return counts


def apply_notes_results(bulk_job_id: int, results) -> dict:
    """Applies translation notes (submit_bulk_translation_notes) by line
    id. No "kept your edit" check -- notes are additive
    (db.save_translation_notes upserts per drama+line+term), never
    overwriting a whole field the way flag/emotion do -- but a line whose
    source changed or was deleted since submission is still dropped, same
    as every other kind, since a note about since-changed text would be
    about the wrong thing."""
    job = db.get_bulk_job(bulk_job_id)
    job_lines = db.list_bulk_job_lines(bulk_job_id)
    row_by_id = {r["line_id"]: r for r in job_lines}
    ids_by_key = {}
    for r in job_lines:
        ids_by_key.setdefault(r["request_key"], []).append(r["line_id"])
    cur_by_id = {ln.id: ln for ln in db.load_line_objects(job["drama_id"])}
    counts = {"applied": 0, "dropped_deleted": 0, "flagged_source_changed": 0,
              "failed_requests": 0, "unknown_requests": 0}
    notes = []

    for key, text, usage, error in results:
        _log_generic_usage(job, usage, "translation_notes_bulk")
        if key not in ids_by_key:
            counts["unknown_requests"] += 1
            continue
        if error or text is None:
            counts["failed_requests"] += 1
            continue
        found = translate_engines.parse_json_array(text, 0)
        by_id = {}
        if isinstance(found, list):
            for n in found:
                if isinstance(n, dict) and n.get("note") and n.get("line_idx") is not None:
                    try:
                        by_id.setdefault(int(n["line_idx"]), []).append(n)
                    except (TypeError, ValueError):
                        continue
        for lid in ids_by_key[key]:
            ln = cur_by_id.get(lid)
            if _stale_line(ln, row_by_id[lid]["zh_hash"]):
                counts["dropped_deleted" if ln is None else "flagged_source_changed"] += 1
                continue
            for n in by_id.get(lid, []):
                n["line_id"] = lid
                n["note_type"] = n.get("note_type") if n.get("note_type") in tguide.NOTE_TYPES else "cultural"
                notes.append(n)
                counts["applied"] += 1

    if notes:
        db.save_translation_notes(job["drama_id"], notes)
    return counts


def apply_results_for_job(bulk_job_id: int, results, engine=None) -> dict:
    """Dispatches to the right apply_* function by the job's own kind --
    what check_once/_check_once_locked call instead of apply_bulk_results
    directly, now that a bulk job isn't always a translation. engine: only
    needed for kind="reflect" (see apply_reflect_stage_results), which may
    need to submit the pipeline's NEXT stage."""
    job = db.get_bulk_job(bulk_job_id)
    kind = job.get("kind") or "translate"
    if kind == "translate":
        return apply_bulk_results(bulk_job_id, results)
    if kind == "flag":
        return apply_flag_results(bulk_job_id, results)
    if kind == "consistency":
        return apply_consistency_results(bulk_job_id, results)
    if kind == "emotion":
        return apply_emotion_results(bulk_job_id, results)
    if kind == "translation_notes":
        return apply_notes_results(bulk_job_id, results)
    if kind == "reflect":
        return apply_reflect_stage_results(bulk_job_id, results, engine=engine)
    raise ValueError(f"Unknown bulk job kind: {kind}")


# ---------------------------------------------------------------------------
# Polling, scheduled runs, cancel, resume
# ---------------------------------------------------------------------------

_check_locks = {}
_check_locks_guard = threading.Lock()


def _check_lock(bulk_job_id: int) -> threading.Lock:
    with _check_locks_guard:
        return _check_locks.setdefault(bulk_job_id, threading.Lock())


def check_once(bulk_job_id: int, provider, engine=None) -> str:
    """One status check. Returns the job's new status. Applies results
    as soon as the provider reports the batch ended. Raises BulkAuthError
    (after recording it on the job) if the key was refused. Serialized per
    job, so the background poller and a "Check now" click can't both
    apply (and log the cost of) the same results.

    engine: only needed for a Reflect job (kind="reflect") -- its own
    apply step may submit the pipeline's next stage, which needs a live
    engine the same way the original submission did."""
    with _check_lock(bulk_job_id):
        return _check_once_locked(bulk_job_id, provider, engine=engine)


def _check_once_locked(bulk_job_id: int, provider, engine=None) -> str:
    job = db.get_bulk_job(bulk_job_id)
    if job is None or job["status"] not in ("submitted", "auth_error"):
        return job["status"] if job else "missing"
    try:
        state = provider.poll(job["provider_batch_id"])
        if state == "pending":
            if job["status"] != "submitted":
                db.update_bulk_job(bulk_job_id, status="submitted", last_error=None)
            return "submitted"
        if state.startswith("failed"):
            db.update_bulk_job(bulk_job_id, status="failed",
                               last_error=f"The provider reported the batch {state[7:] or 'failed'}.")
            return "failed"
        results = list(provider.results(job["provider_batch_id"]))
    except BulkAuthError as e:
        db.update_bulk_job(bulk_job_id, status="auth_error", last_error=str(e))
        raise
    # Re-read: a Cancel clicked while results were downloading wins, and
    # nothing that arrives after it is applied.
    if db.get_bulk_job(bulk_job_id)["status"] == "cancelled":
        return "cancelled"
    summary = apply_results_for_job(bulk_job_id, results, engine=engine)
    db.update_bulk_job(bulk_job_id, status="applied", result_summary=summary, last_error=None)
    return "applied"


def run_scheduled_job(bulk_job_id: int, engine, cost_cap_usd: float = None) -> dict:
    """DeepSeek off-peak: translates the scheduled lines that still exist
    and whose English hasn't changed since scheduling, as a normal run."""
    job = db.get_bulk_job(bulk_job_id)
    args = job.get("translate_args") or {}
    rows = {r["line_id"]: r for r in db.list_bulk_job_lines(bulk_job_id)}
    lines = db.load_line_objects(job["drama_id"])
    eligible = {ln.id for ln in lines
                if ln.id in rows and (ln.en or "") == (rows[ln.id]["en_at_submit"] or "")}
    drama = db.get_drama(job["drama_id"]) or {}
    series_id = drama.get("series_id")
    character_names = tguide.build_speaker_labels(
        db.list_characters_with_series_names(job["drama_id"]),
        db.list_series_characters(series_id) if series_id else [])
    cap = {}
    _, errors = translate_engines.translate_lines_with_engine(
        lines, engine, drama_meta=drama, style_note=args.get("style_note", ""),
        novel_reference=args.get("novel_reference"), force_retranslate=True,
        target_ids=eligible, locale=args.get("locale", "en-US"),
        glossary_terms=args.get("glossary_terms"), style_guidelines=args.get("style_guidelines", ""),
        context_window=args.get("context_window", 6), character_names=character_names,
        save_cb=lambda ls: db.save_lines(job["drama_id"], ls, fields=("en",)),
        usage_cb=lambda inp, out, cache_read=0, cache_write=0: db.log_usage(
            job["drama_id"], job["engine"], getattr(engine, "model", job["model"]), "translate_offpeak",
            inp, out, translate_engines.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write),
            cache_read_tokens=cache_read),
        cost_cap_usd=cost_cap_usd, cap_cb=lambda spent: cap.update(spent=spent))
    summary = {"translated": len(eligible), "skipped_changed": len(rows) - len(eligible),
               "batch_errors": len(errors), "cap_reached": cap.get("spent")}
    # Step 25d item 13: see apply_bulk_results' own comment above -- a run
    # with batch failures or skipped (source-changed) lines used to be
    # marked "translated" anyway.
    _status = dict(translation_engine=job["engine"])
    if untranslated_line_count(job["drama_id"]) == 0:
        _status["status"] = "translated"
    db.update_drama(job["drama_id"], **_status)
    return summary


def run_bulk_poller(job_id: str, bulk_job_id: int, provider=None, engine=None,
                    interval: float = POLL_INTERVAL_SECONDS, sleep=time.sleep, now_fn=None,
                    monthly_cap_usd: float = None):
    """Background-thread target: polls a submitted batch until it's
    applied, or waits for a scheduled DeepSeek job's off-peak window and
    runs it. Stops on cancel, on a terminal status, or on an auth error
    (recorded on the job for the Bulk jobs panel)."""
    now_fn = now_fn or _utcnow

    def wait(seconds):
        waited = 0.0
        while waited < seconds:
            if background_jobs.is_cancel_requested(job_id):
                return False
            step = min(1.0, seconds - waited)
            sleep(step)
            waited += step
        return True

    while not background_jobs.is_cancel_requested(job_id):
        job = db.get_bulk_job(bulk_job_id)
        if job is None or job["status"] not in db.BULK_PENDING_STATUSES:
            return
        if job["status"] == "scheduled":
            now = now_fn()
            start = datetime.datetime.fromisoformat(job["scheduled_for"])
            if now < start or not is_deepseek_offpeak(now):
                target = max(start, next_deepseek_offpeak_start(now))
                background_jobs.update_progress(job_id, 0.0, f"Waiting for the off-peak window "
                                                             f"({target:%Y-%m-%d %H:%M} UTC)")
                if not wait(max(1.0, min(interval, (target - now).total_seconds()))):
                    return
                continue
            db.update_bulk_job(bulk_job_id, status="running")
            cap, refusal = translate_engines.resolve_cost_cap(
                (job.get("translate_args") or {}).get("cost_cap_usd"), monthly_cap_usd,
                db.get_month_spend() if monthly_cap_usd else 0.0)
            if refusal:
                db.update_bulk_job(bulk_job_id, status="failed", last_error=refusal)
                return
            try:
                summary = run_scheduled_job(bulk_job_id, engine, cost_cap_usd=cap)
            except Exception as e:
                db.update_bulk_job(bulk_job_id, status="failed",
                                   last_error=translate_engines.redact_secrets(str(e)))
                return
            db.update_bulk_job(bulk_job_id, status="applied", result_summary=summary)
            background_jobs.set_result(job_id, summary)
            return
        try:
            status = check_once(bulk_job_id, provider, engine=engine)
        except BulkAuthError:
            return
        except Exception as e:
            # Transient (network, 5xx): note it and keep polling.
            db.update_bulk_job(bulk_job_id, last_error=translate_engines.redact_secrets(str(e)))
            status = "submitted"
        if status != "submitted":
            return
        background_jobs.update_progress(job_id, 0.0, "Waiting for the provider to finish the batch")
        if not wait(interval):
            return


def start_poller(bulk_job_id: int, provider=None, engine=None, monthly_cap_usd: float = None) -> bool:
    """Starts (or leaves running) the background poller for one job."""
    job_id = poll_job_id(bulk_job_id)
    return background_jobs.start_job(job_id, run_bulk_poller, job_id, bulk_job_id,
                                     provider=provider, engine=engine,
                                     monthly_cap_usd=monthly_cap_usd,
                                     description=f"Bulk translation #{bulk_job_id}")


def resume_pending(drama_id: int, engine_factory, monthly_cap_usd: float = None) -> dict:
    """After a restart the in-memory job tracker is empty, but pending
    bulk jobs are on disk: start a poller for each one that isn't already
    being polled. engine_factory(engine_choice, model) returns an engine
    built with the user's current key, or None if there's no key yet.
    Returns {bulk_job_id: "polling" | "needs_key" | "running"}."""
    out = {}
    for job in db.list_bulk_jobs(drama_id, statuses=("submitted", "scheduled", "running")):
        if background_jobs.is_running(poll_job_id(job["id"])):
            out[job["id"]] = "running"
            continue
        if job["status"] == "running":
            # An off-peak run the app died in the middle of. Safe to run
            # again: lines it already translated no longer match their
            # English at scheduling time, so they're skipped.
            db.update_bulk_job(job["id"], status="scheduled")
        engine = engine_factory(job["engine"], job["model"])
        if engine is None:
            out[job["id"]] = "needs_key"
            continue
        provider = make_provider(job["engine"], engine)
        start_poller(job["id"], provider=provider, engine=engine, monthly_cap_usd=monthly_cap_usd)
        out[job["id"]] = "polling"
    return out


def cancel_bulk_job(bulk_job_id: int, provider=None) -> str:
    """Stops polling and marks the job cancelled, so any result that
    still arrives is ignored. Cancels at the provider too when there's a
    batch id and a provider to ask; if that call fails the job is still
    cancelled locally. Returns a message for the panel."""
    job = db.get_bulk_job(bulk_job_id)
    background_jobs.request_cancel(poll_job_id(bulk_job_id))
    note = "Cancelled -- results that still arrive will be ignored."
    if job and job.get("provider_batch_id") and provider is not None:
        try:
            provider.cancel(job["provider_batch_id"])
            note = "Cancelled at the provider and here."
        except Exception as e:
            note = ("Stopped here, but the provider's own cancel call failed -- results that "
                    f"still arrive will be ignored. ({translate_engines.redact_secrets(str(e))})")
    db.update_bulk_job(bulk_job_id, status="cancelled", last_error=None)
    return note


# ---------------------------------------------------------------------------
# Step 25c: after a normal (non-bulk) whole-drama translation run
# ---------------------------------------------------------------------------

def untranslated_line_count(drama_id: int) -> int:
    """Lines with source text but no translation yet, as saved right now."""
    return sum(1 for r in db.load_lines(drama_id)
               if (r.get("zh") or "").strip() and not (r.get("en") or "").strip())


def _summary_engine_is_paid(summary_engine, summary_engine_choice) -> bool:
    name = summary_engine_choice or getattr(summary_engine, "name", "")
    return (name not in translate_engines.FREE_ENGINES
            and not getattr(summary_engine, "free_tier", False))


def own_lines_callbacks(drama_id: int, lines, on_saved):
    """(save_cb, notes_cb) for translate_lines_with_engine in a run that may
    write only its own lines' English, shared by the Workspace job and
    `cli.py translate --glossary-affected`. `en` is written only where the
    database still holds what the run loaded, so an edit saved mid-run is
    kept. A Reflect critique arrives before its batch is saved, so it is
    held until that save and dropped for a line whose write was skipped:
    it describes text that never landed. on_saved(lines written) runs
    after each save (provenance)."""
    id_by_idx = {ln.idx: ln.id for ln in lines if getattr(ln, "id", None) is not None}
    skipped, pending = set(), []

    def save_cb(ls):
        skipped.update(db.save_lines(drama_id, ls, fields=("en",), only_if_unchanged=True) or ())
        notes = [n for n in pending if id_by_idx.get(n.get("line_idx")) not in skipped]
        pending.clear()
        if notes:
            db.save_translation_notes(drama_id, notes, id_by_idx=id_by_idx)
        on_saved([ln for ln in ls if getattr(ln, "id", None) not in skipped])

    return save_cb, pending.extend


def finish_translation_run(drama_id: int, lines, engine, engine_choice: str, style_preset: str,
                           glossary_terms, errors, cancelled: bool = False,
                           summary_engine=None, summary_engine_choice: str = None,
                           line_scoped: bool = False,
                           summary_monthly_cap_usd: float = None,
                           enforce_ids=None, flags_needing_recheck: set = None) -> bool:
    """What happens after translate_engines.translate_lines_with_engine
    returns, shared by Workspace's run_translate_job and `cli.py translate`
    so the two can't drift (the CLI used to skip most of it): applies
    enforce_exact glossary terms, flags reading-speed-dense lines, saves
    the run as the active translation version, and persists its batch
    failures (dramas.last_translate_errors).

    The drama is marked "translated" only once no line is left
    untranslated. A run stopped short -- batch failures, the cost cap, a
    cancel -- leaves the status as it was, so the drama stays in `cli.py
    translate`'s default `--status aligned` retry and in Library's
    untranslated selection. A cancelled run saves no version: it isn't a
    finished translation to compare against.

    summary_engine (Step 74), if given, generates this episode's running
    summary ONCE, here -- only once the drama actually reaches "translated"
    and only for a non-cancelled run -- and stores it on the drama row for
    the next episode of the same series to read forward. None (the
    default) skips this entirely, e.g. when no engine could be built for
    it; a missing/unreachable summary engine must never fail the
    translation run itself. summary_monthly_cap_usd: Settings' monthly
    spending cap; a paid summary engine is skipped once it's used up
    (checked right before the call). None or 0 means no cap.

    line_scoped (B-27) marks a run restricted to some lines (e.g. a retry
    of one content-blocked line on another engine): it must not replace
    the drama's recorded translation_engine, which describes the whole-
    drama run, nor its last_translate_errors, nor save a new active
    translation version (the Streamlit retry touched only its line).

    Returns False, recording nothing, if every line this run translated
    has since been replaced (e.g. a new transcription finished meanwhile)
    -- its writes were no-ops, and a version or status would describe
    lines that no longer exist.

    enforce_ids: when given, the run may write only its own lines'
    English: the exact-term substitution touches only those line ids, and
    only where the database still holds the English this run wrote. A
    line whose English differs (its write was skipped because it was
    edited mid-run, or it was edited since) is the user's: it gets no
    substitution, and no flag computed from this run's text. A flag is
    saved only while the line's English, timing and flag in the database
    are still what it was computed from (the flag can be a reading-speed
    or a content-blocked one); the ids of lines that changed during the
    run are added to flags_needing_recheck (a set, if given)."""
    enforced = [t for t in (glossary_terms or []) if t.get("enforce_exact")]
    landed = own_fresh = None
    if enforce_ids is not None:
        # One load only: the substitution's compare-and-set guards against
        # edits after this read, so deciding which lines are still the run's
        # from a second, earlier read would let an edit in between through.
        run_en = {ln.id: (ln.en or "") for ln in lines if getattr(ln, "id", None) is not None}
        own_fresh = [ln for ln in db.load_line_objects(drama_id)
                     if ln.id in enforce_ids and ln.id in run_en
                     and (ln.en or "") == run_en[ln.id]]
        landed = {ln.id for ln in own_fresh}
    if enforced:
        # Step 25d item 5: this used to substitute into `lines` -- the
        # job's own in-memory copies, which can be stale by the time the
        # job actually finishes (a user can edit a line's English while
        # the job is still running). Writing that back unconditionally
        # meant a live edit could be clobbered by a substitution computed
        # from a baseline that was no longer current, with no warning.
        # Loading fresh here and substituting into *that* means this only
        # ever overwrites whatever is actually in the database right now,
        # and db.save_lines' own orig-comparison (see its docstring) then
        # skips writing any line the substitution didn't actually change.
        _fresh_lines = db.load_line_objects(drama_id) if own_fresh is None else own_fresh
        substituted = {}
        for ln in _fresh_lines:
            if ln.en:
                before = ln.en
                ln.en = tguide.apply_hard_term_substitutions(ln.en, enforced)
                if ln.en != before:
                    substituted[ln.id] = (before, ln.en)
        # Compare-and-set in own-lines mode: an edit saved since the load
        # just above is kept too.
        unwritten = db.save_lines(drama_id, _fresh_lines, fields=("en",),
                                  only_if_unchanged=enforce_ids is not None)
        for lid in unwritten or ():
            substituted.pop(lid, None)
        if landed is not None:
            # The density check below reads the run's own copies: give them
            # the substituted English that is now in the database.
            for ln in lines:
                if ln.id in substituted:
                    ln.en = substituted[ln.id][1]
        # The substitution is part of the machine translation: a line whose
        # provenance matched before still matches, so it isn't mistaken for
        # a hand-edited one.
        from services import line_provenance_service
        line_provenance_service.carry_forward(drama_id, substituted)

    # A translation too dense to read in the time it's on screen goes into
    # the review queue like any other flag (never replacing an existing one).
    # Unconditional (not just when flag_dense_lines finds something new):
    # translate_lines_with_engine may already have set a content_blocked flag
    # on some lines (Step 31), and that has to reach the database too, or it
    # only ever exists on this run's in-memory copies.
    import subtitle_formats
    subtitle_formats.flag_dense_lines(lines)
    if landed is None:
        db.save_lines(drama_id, lines, fields=("flag", "flag_note"))
    else:
        # Compare-and-set on the analysed text too: an edit saved after the
        # read above must not get a flag computed from this run's English.
        stale = db.save_lines(drama_id, [ln for ln in lines if ln.id in landed],
                              fields=("flag", "flag_note"), only_if_unchanged=True,
                              guard_fields=("en", "start", "end"))
        if flags_needing_recheck is not None:
            flags_needing_recheck.update(stale or ())

    line_ids = [ln.id for ln in lines if getattr(ln, "id", None) is not None]
    if line_ids and not db.line_ids_exist(drama_id, line_ids):
        return False

    # A line-scoped run (a one-line retry, B-27) matches the Streamlit
    # retry: it touches only its own lines, so it neither saves a new
    # active version (which would be labelled with the retry engine) nor
    # replaces the drama's persisted record of a whole run's failures.
    if not cancelled and not line_scoped:
        label = f"{engine_choice} · {style_preset}"
        if engine_choice in translate_engines.FREE_ENGINES or getattr(engine, "free_tier", False):
            label = f"[testing: {engine_choice}] {label}"
        db.save_translation_version(drama_id, lines, label=label, engine=engine_choice,
                                    model=getattr(engine, "model", ""), make_active=True)
    # Persisted, not just shown once: if the app restarts, the record of
    # what failed (and why some lines are untranslated) must not vanish.
    fields = {}
    if not line_scoped:
        fields["last_translate_errors"] = (json.dumps(errors, ensure_ascii=False)
                                           if errors else None)
        fields["translation_engine"] = engine_choice
    if untranslated_line_count(drama_id) == 0:
        fields["status"] = "translated"
    db.update_drama(drama_id, **fields)

    if (summary_engine is not None and summary_monthly_cap_usd
            and _summary_engine_is_paid(summary_engine, summary_engine_choice)):
        _cap, refusal = translate_engines.resolve_cost_cap(
            None, summary_monthly_cap_usd, db.get_month_spend())
        if refusal:
            summary_engine = None
    if fields.get("status") == "translated" and not cancelled and summary_engine is not None:
        _fresh_for_summary = db.load_line_objects(drama_id)
        summary = translate_engines.generate_episode_summary(
            _fresh_for_summary, summary_engine,
            usage_cb=lambda inp, out, cache_read=0, cache_write=0: db.log_usage(
                drama_id, summary_engine_choice or getattr(summary_engine, "name", ""),
                getattr(summary_engine, "model", ""), "episode_summary", inp, out,
                translate_engines.estimate_cost_for_engine(
                    summary_engine, inp, out, cache_read, cache_write),
                cache_read_tokens=cache_read))
        if summary:
            db.update_drama(drama_id, episode_summary=summary)
    return True
