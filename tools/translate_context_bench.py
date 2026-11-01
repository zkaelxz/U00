"""Context/batch-size bench: `python -m tools.translate_context_bench TITLE_ID [--yes]`.

Re-translates the SAME window of a title's lines under several (batch size,
context before/after) configs with the title's own engine, glossary,
character sheet and style, through the app's real translate_lines_with_engine,
and writes a markdown report comparing the results. Nothing is saved to the
title: every config works on fresh copies of the lines, and the only database
write is the usage_log row for each paid batch (as the Benchmark Lab does).

Without --yes it is a dry run: the same pipeline runs against a stand-in
client that makes no network call, and prints the estimated tokens and cost.
Paid calls need --yes and an estimate under --max-usd. Claude only: the
prompt is measured and the calls are counted through its client.
"""
import argparse
import collections
import datetime
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/translate_context_bench.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Must run before the app imports below (see portable.py's docstring).
import portable
portable.activate_portable_mode()

import core
import db
from engine_backends import pricing, translate_pipeline, engine_registry
from engine_backends import shared as engine_shared
from services import (engine_routing_service, settings_service, translate_run_service, translate_service,
                      workspace_job_service)
from lib.errors import ServiceError
from tools import self_test

REPORT_TITLE = "# Baihe translate context bench report"
REPORT_NAME = "context-bench-report.md"
USAGE_OPERATION = "context_bench"
DEFAULT_CONFIGS = ("30:10/6", "60:10/6", "60:20/10")
DEFAULT_MAX_USD = 1.00
DEFAULT_WINDOW = 300
MAX_CONFIGS = 6
DEFAULT_EDGE = 5
# The estimate is a tokenizer-free heuristic, so the gate adds a margin rather
# than trust it to the cent; the per-config cost cap during the run is the
# second line of defence.
ESTIMATE_MARGIN = 1.25
MAX_CELL_CHARS = 300

_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿]")
_CONFIG = re.compile(r"(\d+):(\d+)/(\d+)")
_PRONOUN_CLASSES = {
    "he": re.compile(r"\b(?:he|him|his|himself)\b"),
    "she": re.compile(r"\b(?:she|her|hers|herself)\b"),
    "they": re.compile(r"\b(?:they|them|their|theirs|themself|themselves)\b"),
}
_ANSWER_MARKER = "Translate these lines:\n\n"
_NUMBERED_LINE = re.compile(r"^(\d+)\. (.*)$", re.MULTILINE)


class Refusal(Exception):
    """A safety check said no; nothing was spent."""


@dataclass(frozen=True)
class Config:
    batch: int
    before: int
    after: int
    label: str = ""


@dataclass
class Stats:
    calls: int = 0
    failed_calls: int = 0
    invocations: int = 0
    once: int = 0
    missing: int = 0
    duplicate: int = 0
    extra: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cost: float = 0.0
    errors: list = field(default_factory=list)
    stopped_by_cap: bool = False
    not_run: str = ""

    @property
    def retry_calls(self) -> int:
        # A retry re-sends only the ids a reply left out, as an extra call.
        return max(0, self.calls - self.invocations)


def parse_config(text: str) -> Config:
    m = _CONFIG.fullmatch(text.strip())
    if not m:
        raise ValueError(f"'{text}' is not BATCH:BEFORE/AFTER (for example 30:10/6)")
    batch, before, after = (int(g) for g in m.groups())
    if batch < 1 or batch > translate_run_service.MAX_BATCH_SIZE:
        raise ValueError(f"batch size must be 1 to {translate_run_service.MAX_BATCH_SIZE}")
    return Config(batch, before, after)


def label_configs(configs) -> list:
    """Unique labels, so the same config passed twice (a noise check) stays two columns."""
    seen = collections.Counter()
    out = []
    for c in configs:
        base = f"batch {c.batch}, ctx {c.before}/{c.after}"
        seen[base] += 1
        out.append(Config(c.batch, c.before, c.after,
                          base if seen[base] == 1 else f"{base} #{seen[base]}"))
    return out


def estimate_tokens(text: str) -> int:
    """Deliberately high: CJK characters at 1.3 tokens each, the rest at 3 chars a token."""
    cjk = len(_CJK.findall(text))
    return math.ceil(cjk * 1.3 + (len(text) - cjk) / 3.0)


# ---------------------------------------------------------------------------
# Client stand-ins: the dry run never touches the network
# ---------------------------------------------------------------------------

def _request_text(kwargs: dict) -> str:
    system = "".join(b.get("text", "") for b in kwargs.get("system") or [])
    return system + "".join(str(m.get("content", "")) for m in kwargs.get("messages") or [])


class _DryMessages:
    def create(self, **kwargs):
        user = str(kwargs["messages"][0]["content"])
        numbered = user.split(_ANSWER_MARKER, 1)[-1]
        answers = {i: "x" * max(4, 3 * len(zh)) for i, zh in _NUMBERED_LINE.findall(numbered)}
        text = json.dumps(answers)
        usage = SimpleNamespace(
            input_tokens=estimate_tokens(_request_text(kwargs)),
            output_tokens=estimate_tokens(text) + 8 * len(answers),
            cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                               stop_reason="end_turn", usage=usage)


class _DryClient:
    messages = _DryMessages()


def reply_keys(text: str) -> list:
    """Every key of the reply's first JSON object whose value is a string, duplicates kept
    (json.loads would drop them, and a repeated id is one of the things counted)."""
    decoder = json.JSONDecoder(object_pairs_hook=lambda pairs: pairs)
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        return [str(k) for k, v in obj if isinstance(v, str)] if isinstance(obj, list) else []
    return []


class Recorder:
    """Counts the model calls and the ids each reply answered, around one config's run."""

    def __init__(self):
        self.stats = Stats()
        self.batches = []           # id lists of each translate_batch call, in order
        self._current = None

    def begin(self, ids):
        self.stats.invocations += 1
        self._current = {"ids": [str(i) for i in ids], "answers": []}
        self.batches.append(self._current)

    def end(self):
        cur, self._current = self._current, None
        if cur is None:
            return
        expected = set(cur["ids"])
        counts = collections.Counter(cur["answers"])
        for i in expected:
            n = counts.get(i, 0)
            if n == 0:
                self.stats.missing += 1
            elif n == 1:
                self.stats.once += 1
            else:
                self.stats.duplicate += 1
        self.stats.extra += sum(n for k, n in counts.items() if k not in expected)

    def on_reply(self, text: str):
        self.stats.calls += 1
        if self._current is not None:
            self._current["answers"].extend(reply_keys(text))


class _CountingMessages:
    def __init__(self, inner, recorder):
        self._inner, self._rec = inner, recorder

    def create(self, **kwargs):
        try:
            resp = self._inner.create(**kwargs)
        except Exception:
            self._rec.stats.failed_calls += 1
            raise
        self._rec.on_reply("".join(b.text for b in resp.content if b.type == "text"))
        return resp


class _CountingClient:
    def __init__(self, inner, recorder):
        self.messages = _CountingMessages(inner.messages, recorder)


def instrument(engine, recorder, dry: bool):
    """Counts calls on the engine's client (replacing it outright in a dry run) and
    the batches the pipeline sends. The engine is used for one config only."""
    engine.client = _CountingClient(_DryClient() if dry else engine.client, recorder)
    original = engine.translate_batch

    def translate_batch(zh_lines, context):
        ids = context.get("line_ids")
        if not (ids is not None and len(ids) == len(zh_lines) and all(isinstance(i, int) for i in ids)
                and len(set(ids)) == len(ids)):
            ids = range(1, len(zh_lines) + 1)
        recorder.begin(ids)
        try:
            return original(zh_lines, context)
        finally:
            recorder.end()

    engine.translate_batch = translate_batch


# ---------------------------------------------------------------------------
# Plan: everything read from the title, shared by every config
# ---------------------------------------------------------------------------

@dataclass
class Plan:
    title_id: int
    drama: dict
    rows: list
    window_ids: list
    engine_name: str
    model: str
    locale: str
    glossary_terms: object
    style_guidelines: str
    character_names: dict
    novel_reference: object
    scene_aware: bool


def choose_window(rows: list, lo, hi, size: int = DEFAULT_WINDOW) -> list:
    """Ids of the lines to re-translate. Explicit --from/--to (line numbers, inclusive) win;
    otherwise the first `size` lines of the longest run of consecutive translated lines."""
    def has_zh(r):
        return bool((r.get("zh") or "").strip())

    if lo is not None or hi is not None:
        lo = -math.inf if lo is None else lo
        hi = math.inf if hi is None else hi
        return [r["id"] for r in rows if lo <= r["idx"] <= hi and has_zh(r)]
    best, run = [], []
    for r in rows:
        if has_zh(r) and (r.get("en") or "").strip():
            run.append(r["id"])
            if len(run) > len(best):
                best = list(run)
        else:
            run = []
    return best[:size]


def build_plan(args) -> Plan:
    drama = db.get_drama(args.title_id)
    if not drama:
        raise Refusal(f"Title {args.title_id} not found.")
    engine_name = (args.engine or drama.get("translation_engine")
                   or engine_routing_service.resolve_capability("translation.cheap"))
    if engine_name != "claude":
        raise Refusal(f"This bench measures Claude only; this title uses '{engine_name}'. "
                      "Pass --engine claude to measure with Claude anyway.")
    translate_run_service._require_offered_model(engine_name, args.model)
    rows = db.load_lines(args.title_id)
    window_ids = choose_window(rows, args.from_idx, args.to_idx)
    if not window_ids:
        raise Refusal("No lines to re-translate: this title has no translated lines in range "
                      "(use --from/--to to pick lines).")
    is_novel = drama.get("content_mode") == "novel_narration"
    style_preset = "novel" if is_novel else "audio_drama"
    lines = core.lines_from_rows(rows)
    glossary_terms, style_guidelines, character_names = workspace_job_service.build_run_style_context(
        args.title_id, drama, lines, style_preset)
    return Plan(args.title_id, drama, rows, window_ids, engine_name, args.model,
                settings_service.get("default_locale") or "en-US", glossary_terms, style_guidelines,
                character_names, translate_run_service.load_novel_reference(args.title_id, drama),
                bool(settings_service.get("scene_aware_batches")))


def effective_cap(engine_name: str, max_usd: float) -> float:
    """The tighter of --max-usd and what is left of this month's cap (a refusal if it is used up)."""
    monthly = translate_run_service.month_cap_usd() if translate_run_service.engine_cap_applies(
        engine_name, settings_service.resolve_gemini_free_tier(None)) else 0.0
    spend = db.get_month_spend() if monthly else 0.0
    cap, refusal = pricing.resolve_cost_cap(max_usd, monthly, spend)
    if refusal:
        raise Refusal(refusal)
    # NaN compares false against everything, so a bad cap would pass every later check.
    if cap is None or not math.isfinite(cap) or cap <= 0:
        raise Refusal("No usable spending limit; nothing was spent.")
    return cap


# ---------------------------------------------------------------------------
# One config run
# ---------------------------------------------------------------------------

def run_config(plan: Plan, cfg: Config, engine, dry: bool, budget: float):
    """Translates the window under cfg on fresh copies of the lines. Returns
    (Stats, Recorder, {line id: English}). Saves nothing."""
    lines = core.lines_from_rows(plan.rows)
    targets = set(plan.window_ids)
    for ln in lines:
        if ln.id in targets:
            ln.en = ""  # so a batch that fails stays visibly empty instead of keeping the old English
    rec = Recorder()
    instrument(engine, rec, dry)
    stats = rec.stats

    def usage_cb(inp, out, cache_read=0, cache_write=0):
        cost = pricing.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write)
        stats.tokens_in += inp
        stats.tokens_out += out
        stats.cache_read += cache_read
        stats.cost += cost
        if not dry:
            try:
                # drama_id None: the spend counts toward the monthly cap without touching the title.
                db.log_usage(None, plan.engine_name, getattr(engine, "model", "") or "",
                             USAGE_OPERATION, inp, out, cost, cache_read_tokens=cache_read)
            except Exception as exc:
                stats.errors.append("usage log write failed: " + engine_shared.redact_secrets(str(exc)))

    # The merged/shifted re-asks would add requests the configs being compared don't share.
    engine.checks_replies = False
    _, errors = translate_pipeline.translate_lines_with_engine(
        lines, engine, drama_meta=plan.drama, batch_size=cfg.batch, locale=plan.locale,
        glossary_terms=plan.glossary_terms, style_guidelines=plan.style_guidelines,
        context_window=cfg.before, context_window_ahead=cfg.after,
        character_names=plan.character_names, novel_reference=plan.novel_reference,
        force_retranslate=True, target_ids=targets, scene_aware_batches=plan.scene_aware,
        usage_cb=usage_cb, cost_cap_usd=budget,
        cap_cb=lambda spent: setattr(stats, "stopped_by_cap", True))
    stats.errors.extend(engine_shared.redact_secrets(str(e.get("error", ""))) for e in errors)
    return stats, rec, {ln.id: ln.en for ln in lines if ln.id in targets}


def build_engine(plan: Plan, api_key: str):
    return engine_registry.get_engine(plan.engine_name, api_key, plan.model)


def run_all(plan: Plan, configs, dry: bool, cap: float, api_key: str):
    results = []
    spent = 0.0
    for cfg in configs:
        remaining = cap - spent
        if not remaining > 0:
            results.append((cfg, Stats(not_run="budget used up before this config"), Recorder(), {}))
            continue
        engine = build_engine(plan, api_key)
        plan.model = plan.model or getattr(engine, "model", None)
        stats, rec, english = run_config(plan, cfg, engine, dry, remaining)
        spent += stats.cost
        results.append((cfg, stats, rec, english))
    return results


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _cell(text: str) -> str:
    text = engine_shared.redact_secrets(_norm(text))
    text = self_test._URL.sub("[URL]", text).replace("|", "\\|")
    return text if len(text) <= MAX_CELL_CHARS else text[:MAX_CELL_CHARS - 1] + "…"


def edge_ids(baseline: Recorder, edge: int) -> set:
    """First and last `edge` lines of every batch the baseline config sent
    (a re-sent subset of an earlier batch is not a new batch)."""
    seen, marked = set(), set()
    for batch in baseline.batches:
        ids = batch["ids"]
        if set(ids) <= seen:
            continue
        seen.update(ids)
        marked.update(ids[:edge])
        marked.update(ids[-edge:])
    return {int(i) for i in marked}


def pronoun_signature(text: str) -> frozenset:
    low = (text or "").casefold()
    return frozenset(k for k, rx in _PRONOUN_CLASSES.items() if rx.search(low))


def character_name_list(character_names: dict) -> dict:
    """{speaker label: bare character name} from the 'Name (pronouns)' labels the translator sees."""
    return {label: re.sub(r"\s*\(.*\)\s*$", "", value).strip()
            for label, value in (character_names or {}).items() if value}


def consistency(plan: Plan, results, edge_set: set) -> dict:
    """How often a character name, or the he/she/they used, differs between configs for the same line."""
    done = [(cfg, english) for cfg, stats, _, english in results if english and not stats.not_run]
    rows = {r["id"]: r for r in plan.rows}
    speaker_name = character_name_list(plan.character_names)
    names = sorted({n for n in speaker_name.values() if len(n) >= 2})
    out = {"names": {n: {"lines": 0, "vary": 0} for n in names},
           "pronoun": collections.defaultdict(lambda: {"lines": 0, "vary": 0}),
           "edge": {"lines": 0, "vary": 0}, "interior": {"lines": 0, "vary": 0}, "compared": 0}
    if len(done) < 2:
        return out
    for lid in plan.window_ids:
        texts = [english.get(lid, "") for _, english in done]
        if not all(t.strip() for t in texts):
            continue
        out["compared"] += 1
        varies = False
        low = [t.casefold() for t in texts]
        for n in names:
            present = [bool(re.search(rf"\b{re.escape(n.casefold())}\b", t)) for t in low]
            if any(present):
                out["names"][n]["lines"] += 1
                if not all(present):
                    out["names"][n]["vary"] += 1
                    varies = True
        sigs = {pronoun_signature(t) for t in texts}
        if any(sigs):
            who = speaker_name.get(rows[lid].get("speaker")) or "(no named speaker)"
            out["pronoun"][who]["lines"] += 1
            if len(sigs) > 1:
                out["pronoun"][who]["vary"] += 1
                varies = True
        bucket = out["edge" if lid in edge_set else "interior"]
        bucket["lines"] += 1
        bucket["vary"] += varies
    return out


def _rate(vary: int, lines: int) -> str:
    return f"{vary} of {lines} ({100 * vary / lines:.0f}%)" if lines else "0 of 0"


def format_report(plan: Plan, configs, results, edge: int, cap: float) -> str:
    baseline_rec = results[0][2]
    edge_set = edge_ids(baseline_rec, edge)
    idx_by_id = {r["id"]: r["idx"] for r in plan.rows}
    zh_by_id = {r["id"]: r.get("zh") or "" for r in plan.rows}
    out = [REPORT_TITLE, "",
           f"Date: {datetime.date.today().isoformat()}  ",
           f"Title id: {plan.title_id}  ",
           f"Engine: {plan.engine_name} {plan.model or '(default model)'}  ",
           f"Window: lines {min(idx_by_id[i] for i in plan.window_ids)}-"
           f"{max(idx_by_id[i] for i in plan.window_ids)} ({len(plan.window_ids)} lines re-translated, "
           f"nothing saved to the title)  ",
           f"Baseline: {configs[0].label}. Edge lines: the first and last {edge} of each baseline batch "
           f"({len(edge_set)} lines).  ",
           f"Budget: --max-usd cap in force ${cap:.2f}", "",
           "## Per config", "",
           "| config | calls | batches | retry calls | failed calls | ids once | missing | duplicate | "
           "extra | tokens in | tokens out | cost USD | note |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cfg, s, _, _ in results:
        note = s.not_run or ("stopped at the cost cap" if s.stopped_by_cap else "")
        if s.errors:
            note = (note + "; " if note else "") + f"{len(s.errors)} error(s)"
        out.append(f"| {cfg.label} | {s.calls} | {s.invocations} | {s.retry_calls} | {s.failed_calls} | "
                   f"{s.once} | {s.missing} | {s.duplicate} | {s.extra} | {s.tokens_in} | {s.tokens_out} | "
                   f"{s.cost:.4f} | {note} |")
    out.append(f"| total | | | | | | | | | {sum(r[1].tokens_in for r in results)} | "
               f"{sum(r[1].tokens_out for r in results)} | {sum(r[1].cost for r in results):.4f} | |")
    errors = [(cfg.label, e) for cfg, s, _, _ in results for e in s.errors]
    if errors:
        out += ["", "Errors (secrets, addresses and file locations removed):", ""]
        out += [f"- {label}: {self_test._clean(e)}" for label, e in errors[:20]]

    cons = consistency(plan, results, edge_set)
    out += ["", "## Name and pronoun consistency", "",
            f"Lines compared (translated under every config): {cons['compared']}. "
            "A line \"varies\" when a character name appears in some configs' English and not others, "
            "or the he/she/they used differs. This counts disagreement between configs, not which one is right.",
            "", f"- Edge lines that vary: {_rate(cons['edge']['vary'], cons['edge']['lines'])}",
            f"- Interior lines that vary: {_rate(cons['interior']['vary'], cons['interior']['lines'])}", "",
            "| character name | lines naming them | lines where it varies |", "|---|---|---|"]
    out += [f"| {_cell(n)} | {v['lines']} | {v['vary']} |" for n, v in cons["names"].items()] or ["| (no named characters) | 0 | 0 |"]
    out += ["", "| speaker | lines with he/she/they | lines where it varies |", "|---|---|---|"]
    out += [f"| {_cell(who)} | {v['lines']} | {v['vary']} |" for who, v in sorted(cons["pronoun"].items())] \
        or ["| (none) | 0 | 0 |"]

    done = [(cfg, english) for cfg, s, _, english in results if english]
    out += ["", "## Lines whose English differs between configs", "",
            "E marks an edge line of the baseline's batches.", "",
            "| line | E | source | " + " | ".join(_cell(cfg.label) for cfg, _ in done) + " |",
            "|---|---|---|" + "---|" * len(done)]
    shown = 0
    for lid in plan.window_ids:
        texts = [english.get(lid, "") for _, english in done]
        if len({_norm(t) for t in texts}) > 1:
            shown += 1
            out.append(f"| {idx_by_id[lid]} | {'E' if lid in edge_set else ''} | {_cell(zh_by_id[lid])} | "
                       + " | ".join(_cell(t) or "(none)" for t in texts) + " |")
    if not shown:
        out.append("| (identical under every config) | | |" + " |" * len(done))
    out += ["", f"{shown} of {len(plan.window_ids)} lines differ. Two runs of the same config also differ "
            "a little; to see that noise, pass the same --config twice.", ""]
    return "\n".join(out)


def default_report_path() -> str:
    # The data folder, as self_test does: the title's own folder holds the library's media and
    # is backed up and exported with it.
    return os.path.join(portable.data_dir(), REPORT_NAME)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_estimate(configs, results, cap: float, gate_total: float):
    print(f"{'config':<26}{'calls':>6}{'tokens in':>12}{'tokens out':>12}{'est. USD':>10}")
    for cfg, s, _, _ in results:
        print(f"{cfg.label:<26}{s.calls:>6}{s.tokens_in:>12}{s.tokens_out:>12}{s.cost:>10.4f}")
    total = sum(r[1].cost for r in results)
    print(f"{'total':<26}{'':>6}{sum(r[1].tokens_in for r in results):>12}"
          f"{sum(r[1].tokens_out for r in results):>12}{total:>10.4f}")
    print(f"Checked against the cap with a {int((ESTIMATE_MARGIN - 1) * 100)}% margin: "
          f"${gate_total:.4f} (cap ${cap:.2f}). Prompt caching is not assumed, so a real run "
          "usually costs less.")


def run(args) -> int:
    configs = label_configs([parse_config(c) for c in (args.config or DEFAULT_CONFIGS)])
    if len(configs) > MAX_CONFIGS:
        raise Refusal(f"At most {MAX_CONFIGS} configs per run.")
    if not math.isfinite(args.max_usd) or args.max_usd <= 0:
        raise Refusal("--max-usd must be a finite number above zero.")
    if args.edge < 1:
        raise Refusal("--edge must be at least 1.")
    out = args.out or (default_report_path() if args.yes else None)
    # Before any paid call: a refused file must not cost money.
    if out and self_test._refuse_to_overwrite(out, REPORT_TITLE):
        raise Refusal("Refusing to write the report: that file exists and is not a previous "
                      "context-bench report.")
    plan = build_plan(args)
    cap = effective_cap(plan.engine_name, args.max_usd)

    print(f"Estimating {len(plan.window_ids)} lines under {len(configs)} configs (no network calls)...")
    estimate = run_all(plan, configs, True, float("inf"), "dry-run")
    total = sum(r[1].cost for r in estimate)
    gate_total = total * ESTIMATE_MARGIN
    _print_estimate(configs, estimate, cap, gate_total)
    if not math.isfinite(gate_total) or not gate_total <= cap:
        raise Refusal(f"Estimate ${gate_total:.2f} is above the ${cap:.2f} limit. Lower the window "
                      "(--from/--to), use fewer configs, or raise --max-usd.")
    if not args.yes:
        print("\nDry run only: nothing was sent and nothing was written. Add --yes to run it for real.")
        return 0

    api_key = translate_service.resolve_api_key(plan.engine_name)
    if not api_key:
        raise Refusal(f"No {plan.engine_name} key is set up (Settings or the .env file).")
    results = run_all(plan, configs, False, cap, api_key)
    report = format_report(plan, configs, results, args.edge, cap)
    spent = sum(r[1].cost for r in results)
    try:
        # The first check was minutes ago; the file may have been replaced since.
        if self_test._refuse_to_overwrite(out, REPORT_TITLE):
            raise OSError("report path changed")
        Path(out).write_text(report + "\n", encoding="utf-8")
        saved = f"Report written to {Path(out).name}"
    except OSError:
        saved = "Could not write the report file; the totals above are all that was kept."
    print(f"Spent about ${spent:.4f} (logged to usage as '{USAGE_OPERATION}'). {saved}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.translate_context_bench",
                                     description=__doc__.splitlines()[0])
    parser.add_argument("title_id", type=int, help="the title to measure")
    parser.add_argument("--from", dest="from_idx", type=int, help="first line number (default: see --help)")
    parser.add_argument("--to", dest="to_idx", type=int, help="last line number, inclusive")
    parser.add_argument("--config", action="append", metavar="BATCH:BEFORE/AFTER",
                        help=f"repeatable; default {' '.join(DEFAULT_CONFIGS)}; the first is the baseline")
    parser.add_argument("--yes", action="store_true", help="make the paid calls (default: dry run)")
    parser.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                        help="refuse above this estimated total (default 1.00)")
    parser.add_argument("--out", help=f"report file (default: {REPORT_NAME} in the title's folder)")
    parser.add_argument("--edge", type=int, default=DEFAULT_EDGE,
                        help="lines at each end of a baseline batch counted as batch edges")
    parser.add_argument("--engine", help="override the title's engine (claude only)")
    parser.add_argument("--model", help="override the engine's model")
    args = parser.parse_args(argv)
    self_test._console_safe()
    if not os.path.isfile(db.DB_PATH):
        print("No library found here; nothing to measure.")
        return 2
    try:
        return run(args)
    except Refusal as refusal:
        print(f"Refused: {refusal}")
        return 2
    except ValueError as bad:
        print(f"Refused: {bad}")
        return 2
    except ServiceError as exc:
        print("Failed: " + self_test._clean(engine_shared.redact_secrets(str(exc))))
        return 1
    except Exception as exc:
        print(f"Failed ({type(exc).__name__}): "
              + self_test._clean(engine_shared.redact_secrets(str(exc))))
        return 1


if __name__ == "__main__":
    sys.exit(main())
