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

import background_jobs
import db
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
        ids = [ln.id for ln in batch]
        numbered = translate_engines._build_numbered_lines(
            ids, [ln.zh for ln in batch], [character_names.get(ln.speaker) for ln in batch])
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
# Applying results
# ---------------------------------------------------------------------------

def _parse_strict(text: str, expected_ids: list) -> dict:
    """Only an id-keyed JSON object is accepted for a bulk result -- not
    the positional-array fallback the live path tolerates. Keys outside
    this request's own ids are ignored."""
    stripped = (text or "").strip()
    for fence in ("```json", "```"):
        stripped = stripped.replace(fence, "")
    if not isinstance(translate_engines._extract_first_json_value(stripped.strip()), dict):
        return {}
    return translate_engines._parse_id_keyed_json(stripped, expected_ids)


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
        db.update_drama(job["drama_id"], status="translated", translation_engine=job["engine"])
    return counts


# ---------------------------------------------------------------------------
# Polling, scheduled runs, cancel, resume
# ---------------------------------------------------------------------------

_check_locks = {}
_check_locks_guard = threading.Lock()


def _check_lock(bulk_job_id: int) -> threading.Lock:
    with _check_locks_guard:
        return _check_locks.setdefault(bulk_job_id, threading.Lock())


def check_once(bulk_job_id: int, provider) -> str:
    """One status check. Returns the job's new status. Applies results
    as soon as the provider reports the batch ended. Raises BulkAuthError
    (after recording it on the job) if the key was refused. Serialized per
    job, so the background poller and a "Check now" click can't both
    apply (and log the cost of) the same results."""
    with _check_lock(bulk_job_id):
        return _check_once_locked(bulk_job_id, provider)


def _check_once_locked(bulk_job_id: int, provider) -> str:
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
    summary = apply_bulk_results(bulk_job_id, results)
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
    db.update_drama(job["drama_id"], status="translated", translation_engine=job["engine"])
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
            status = check_once(bulk_job_id, provider)
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
