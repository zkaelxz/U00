"""
sources/adaptive.py -- the order things are tried in when a pasted URL
has no dedicated adapter (roadmap Step 23g items 4, 6, 7, 8).

    saved site profile        -> 0 LLM calls
    (dedicated adapter        -> 0 LLM calls; handled before this module)
    deterministic extraction  -> 0 LLM calls  (trafilatura / heuristic /
                                              the comic content-vs-chrome filter)
    ambiguous or empty        -> exactly 1 LLM call, unless this exact page
                                 content was already extracted (cache)

The LLM tier only ever reads the page the access ladder already
legitimately received (static, rendered or user-assisted) -- it adds no
new way of reaching a site, and nothing here solves a challenge, logs in
or decrypts anything.

When the AI tier (or a profile that stopped fitting) produces a result,
a reusable profile is generated from it, re-run against the same page and
validated before it is offered: auto-saved only at HIGH confidence,
otherwise held for the person's approval. Previous versions are kept.

Every attempt is written to the same access-attempt log the ladder uses
(sources/store.log_attempt, source "generic"), extended with which
extraction tier worked, the profile story and the plain-language reason --
that log is what the Source Diagnostics view reads.
"""

import time
from dataclasses import dataclass, field

from . import ai_extract as ax
from . import detect, generic_import, profiles, store
from .generic_import import GENERIC_SOURCE, ComicImportResult, NoContentFound, NovelImportResult
from .ladder import TIER_LABELS, access_facts
from .models import PROTECTION_REASONS, AccessTier

EXTRACTION_TIER_LABELS = {
    "adapter": "Dedicated adapter",
    "profile": "Saved site profile",
    "deterministic": "Deterministic extraction",
    "llm": "AI-assisted extraction",
    "none": "Nothing usable found",
}

# "over the" and "accepted image type": the API page rules (services/page_import_limits.py).
_HARD_REJECTS = ("readable", "download", "too small", "over the", "accepted image type")


@dataclass
class ExtractionReport:
    """What happened on one attempt, for the Review and Diagnostics views."""
    url: str
    kind: str
    access_tier: str = None
    access_lines: list = field(default_factory=list)
    extraction_tier: str = "none"
    method: str = ""
    llm_calls: int = 0
    cache_hit: bool = False
    profile: dict = field(default_factory=dict)
    protection: list = field(default_factory=list)
    reason: str = ""
    lines: list = field(default_factory=list)
    needs_review: bool = False
    pending_profile: dict = None
    # Hold even a HIGH-confidence profile candidate for approval instead of
    # saving it (a run the owner at the PC didn't start: profile writes are
    # PC-only in the API).
    hold_profiles: bool = False
    data: dict = None
    access: dict = field(default_factory=dict)          # Step 23k: ladder.access_facts()
    resource_types: list = field(default_factory=list)  # ContentAccess values found on the page

    def note(self, line: str):
        self.lines.append(line)

    def headline(self) -> str:
        access = TIER_LABELS.get(AccessTier(self.access_tier)) if self.access_tier else None
        extraction = EXTRACTION_TIER_LABELS.get(self.extraction_tier, self.extraction_tier)
        worked = " + ".join(x for x in (access, extraction) if x)
        return f"Worked: {worked}" if self.extraction_tier not in ("none",) else \
            f"Failed{f' after {access}' if access else ''}: {self.reason or 'no usable content'}"

    def to_log(self) -> dict:
        return {"kind": "extraction", "content_type": self.kind, "tier": self.access_tier,
                "extraction_tier": self.extraction_tier, "method": self.method,
                "llm_calls": self.llm_calls, "cache_hit": self.cache_hit,
                "profile": self.profile, "protection": self.protection, "reason": self.reason,
                "confidence": (self.data or {}).get("overall"), "headline": self.headline(),
                "access": self.access, "resource_types": self.resource_types,
                "lines": self.access_lines + self.lines}


def _log(report: ExtractionReport):
    store.log_attempt(GENERIC_SOURCE, report.url, report.to_log())


def _note_access(report: ExtractionReport, lr):
    report.access_tier = lr.tier
    report.access_lines = lr.summary_lines()
    report.protection = [r.value for r in lr.reasons if r in PROTECTION_REASONS]
    report.access = access_facts(lr)
    report.resource_types = list(lr.resource_types)
    if report.protection:
        report.note("Protection detected: " + ", ".join(report.protection) +
                    " -- recorded, never decoded or worked around.")
    _note_if_translated(report, getattr(lr, "html", "") or "")


def _note_if_translated(report: ExtractionReport, html: str):
    """Warn when the page arrived already machine-translated.

    A browser translator replaces the original text in the page rather
    than annotating it, so everything downstream would be working from
    the translation: the source text saved for a chapter would be English,
    and this app would then "translate" English it believes is Chinese.
    That failure is completely silent -- the import succeeds and the text
    looks fine -- which is exactly why it is worth saying out loud.

    Reaches the user-assisted tier most often, since that HTML comes
    straight from the person's own browser.
    """
    translators = detect.machine_translated(html)
    if not translators:
        return
    report.needs_review = True
    report.note(
        "This page arrived already translated by " + ", ".join(translators) +
        ". The translator replaces the original text, so anything read from this page "
        "would be the translation, not the source. Turn the browser's page translation "
        "off for this site and fetch it again.")


def _unreachable_reason(report: ExtractionReport) -> str:
    """The most specific reason a page couldn't be read -- a protected
    resource or a missing purchase is named as exactly that, never folded
    into a generic failure (Step 23k item 6)."""
    if report.access.get("protection_detail"):
        return " ".join(report.access["protection_detail"])
    if report.access.get("purchase_required"):
        return report.access["entitlement"]
    return "Couldn't load this page (each tier's reason is listed above)."


def _unreachable_message(report: ExtractionReport, lr) -> str:
    head = "Couldn't load this page:" if report.reason.startswith("Couldn't load") else report.reason
    return head + "\n" + "\n".join(lr.summary_lines())


def _no_content(message: str, report: ExtractionReport) -> NoContentFound:
    e = NoContentFound(message)
    e.report = report
    return e


def _fresh_profile_state(domain: str) -> dict:
    return {"domain": domain, "existed": False, "used": False, "version": None,
            "failed_version": None, "generated": False, "saved": None, "pending": False}


def _done(report, data, tier, line):
    report.data = data
    report.extraction_tier = tier
    report.method = data.get("method", tier)
    report.note(line)
    b = ax.overall(data)["bucket"]
    if b in (ax.LOW, ax.FAILED):
        report.needs_review = True
    return data


def _redact(text: str) -> str:
    from translate_engines import redact_secrets
    return redact_secrets(text)


# ---------------------------------------------------------------------------
# The one LLM tier, behind the result cache
# ---------------------------------------------------------------------------

def _llm_picks(kind, payload, prompt, engine, report, max_tokens, use_cache):
    """(picks or None, content_hash). Cache first; at most one real call."""
    h = ax.content_hash(kind, payload)
    if use_cache:
        entry = store.get_extraction(kind, h)
        if entry is not None:
            if entry.get("schema_version") != ax.EXTRACTION_SCHEMA_VERSION:
                store.delete_extraction(kind, h)
                report.note("A cached AI extraction of this page was made in an older format -- "
                            "discarded rather than reused.")
            else:
                report.cache_hit = True
                report.note("This exact page content was already extracted -- reused the cached "
                            f"result ({entry.get('provider')}/{entry.get('model')}), 0 AI calls.")
                return entry.get("picks"), h
    if not ax.llm_available(engine):
        report.note("No AI engine is set up for the fallback, so it wasn't tried.")
        return None, h
    report.llm_calls += 1
    try:
        picks = ax.ask_json(engine, prompt + payload, max_tokens)
    except Exception as e:
        report.note(f"The AI engine call failed: {_redact(f'{type(e).__name__}: {e}')[:300]}")
        return None, h
    if picks is None:
        report.note("The AI engine didn't return usable JSON.")
    return picks, h


def _cache_put(kind, h, url, picks, data, engine, profile_version=None):
    info = ax.engine_info(engine)
    store.put_extraction(kind, h, url, {
        "picks": picks, "result": {k: v for k, v in data.items() if k != "paragraphs"},
        "schema_version": ax.EXTRACTION_SCHEMA_VERSION, **info,
        "profile_version": profile_version, "created_at": time.time(),
        "confidence": data.get("overall"),
        "validation": {"valid": data.get("valid"), "problems": data.get("problems") or []}})


def _offer(report, domain, kind, rules, validation, origin):
    """Save a validated candidate at HIGH, hold it for approval below."""
    report.profile["generated"] = True
    if not validation.get("valid"):
        report.profile["saved"] = False
        report.note(f"Generated a profile candidate for {domain}, but re-running it on this page "
                    "failed its checks, so it was NOT saved: "
                    + "; ".join(validation.get("problems") or ["no usable result"]))
        return
    bucket = validation["overall"]["bucket"]
    if bucket == ax.HIGH and not report.hold_profiles:
        entry = profiles.save_version(domain, kind, rules, validation, origin=origin)
        report.profile["saved"] = entry["version"]
        report.note(f"Saved site profile v{entry['version']} for {domain} -- the next chapter "
                    "from this site won't need an AI call."
                    + (f" v{entry['replaces']} is kept and can be restored." if entry["replaces"] else ""))
    else:
        report.pending_profile = {"domain": domain, "kind": kind, "rules": rules, "origin": origin,
                                  "validation": {k: validation.get(k) for k in
                                                 ("valid", "overall", "problems", "confidence")}}
        report.profile["pending"] = True
        report.note(f"A profile candidate for {domain} passed validation at {bucket} confidence -- "
                    "it's only saved if you approve it.")


def approve_pending(pending: dict) -> dict:
    """The person approved a held candidate on the Review screen."""
    return profiles.save_version(pending["domain"], pending["kind"], pending["rules"],
                                 pending["validation"], origin=pending.get("origin", "llm"),
                                 approved=True)


# ---------------------------------------------------------------------------
# Novel chapters
# ---------------------------------------------------------------------------

def _novel_candidate_check(page, source: dict, rules: dict) -> dict:
    """Re-runs generated rules on the page and checks they reproduce the
    result they were generated from before anyone is offered them."""
    cand, why = profiles.apply_novel_rules(page, rules)
    if cand is None:
        return {"valid": False, "problems": [why], "overall": {"score": 0, "bucket": ax.FAILED}}
    ax.validate_novel(cand, page)
    want = {ax._squash(p["text"]) for p in source.get("paragraphs") or []}
    got = {ax._squash(p["text"]) for p in cand.get("paragraphs") or []}
    if want:
        match = len(want & got) / len(want)
        extra = len(got - want) / max(1, len(got))
        if match < 0.9 or extra > 0.2:
            cand["valid"] = False
            cand["problems"].append(f"the profile reproduces only {match:.0%} of this extraction "
                                    f"({extra:.0%} extra text)")
    return cand


def _offer_novel_profile(page, data, report, domain, origin):
    rules = profiles.infer_novel_rules(page, data)
    if not rules:
        report.profile["generated"] = False
        report.note("Couldn't turn this result into a reusable profile (no stable way to "
                    "point at the chapter text on this page).")
        return
    _offer(report, domain, "novel", rules, _novel_candidate_check(page, data, rules), origin)


def extract_novel(html: str, url: str, engine=None, use_cache: bool = True,
                  report: ExtractionReport = None, remember: bool = True):
    """Runs the extraction ladder on an already-fetched page. Returns
    (data or None, report). Never fetches anything itself.

    `remember=False` writes nothing that outlives the call: no profile is
    saved or offered, no profile use or failure is recorded, and no AI
    result is cached (the API passes it for page source pasted from another
    device, which could otherwise plant a site profile for any domain)."""
    report = report or ExtractionReport(url, "novel")
    page = ax.PageModel(html, url)
    domain = profiles.domain_of(url)
    report.profile = _fresh_profile_state(domain)
    failed_profile = None

    prof = profiles.active(domain, "novel")
    if prof:
        report.profile.update(existed=True, version=prof["version"])
        data, why = profiles.apply_novel_rules(page, prof["rules"])
        if data is not None:
            ax.validate_novel(data, page)
            if data["valid"]:
                if remember:
                    profiles.record_use(domain, "novel", prof["version"], True)
                report.profile["used"] = True
                return _done(report, data, "profile",
                             f"Used the saved profile for {domain} (v{prof['version']}) -- 0 AI calls."), report
            why = "; ".join(data["problems"]) or "its result failed the checks"
        if remember:
            profiles.record_use(domain, "novel", prof["version"], False, why)
        failed_profile = prof
        report.profile["failed_version"] = prof["version"]
        report.note(f"The saved profile for {domain} (v{prof['version']}) no longer fits this page "
                    f"({why}). It's kept as it was; ran the full ladder instead.")

    det = ax.deterministic_novel(page)
    ax.validate_novel(det, page)
    if det["valid"]:
        if failed_profile and remember:
            _offer_novel_profile(page, det, report, domain, "deterministic")
        return _done(report, det, "deterministic",
                     f"Deterministic extraction ({det['method']}) found the chapter -- 0 AI calls."), report
    report.note("Deterministic extraction was ambiguous or empty: "
                + ("; ".join(det["problems"]) or "no chapter text"))

    picks, h = _llm_picks("novel", ax.blocks_payload(page), ax.NOVEL_PROMPT, engine, report,
                          3000, use_cache)
    if picks is not None:
        data = ax.novel_from_picks(page, picks)
        ax.validate_novel(data, page, picks.get("confidence"))
        if data["valid"]:
            if not report.cache_hit and remember:
                _cache_put("novel", h, url, picks, data, engine)
            if remember:
                _offer_novel_profile(page, data, report, domain, "llm")
            return _done(report, data, "llm",
                         "AI-assisted extraction identified the chapter (text copied from the "
                         "page, never rewritten)."), report
        report.note("The AI answer failed the independent checks: " + "; ".join(data["problems"]))

    if det["confidence"]["content"]["bucket"] == ax.LOW:
        report.reason = ("Found text, but it isn't clearly the chapter (" +
                         "; ".join(det["problems"]) + ") -- review it before importing.")
        return _done(report, det, "deterministic", "Kept the deterministic result for review."), report
    report.reason = ("Couldn't find the chapter text on this page: "
                     + ("; ".join(det["problems"]) or "no block of prose long enough")
                     + (". Set up an AI engine for the fallback, or paste/upload the text instead."
                        if not report.llm_calls and not report.cache_hit else
                        ". Paste or upload the text instead."))
    report.needs_review = True
    return None, report


def import_novel(url: str, engine=None, client=None, rendered_fetch=None, user_html: str = None,
                 use_cache: bool = True, allow_signed_in: bool = True,
                 allow_browser: bool = True, remember: bool = True,
                 hold_profiles: bool = False):
    """The generic novel import with the Step 23g ladder. Returns
    (NovelImportResult, report); raises NoContentFound (with `.report`).
    `remember=False`: see extract_novel; the ladder result is not recorded
    on the source's capability record either. `hold_profiles`: never
    auto-save a generated site profile."""
    report = ExtractionReport(url, "novel", hold_profiles=hold_profiles)
    lr = generic_import.fetch_page(url, generic_import._client(client, url), rendered_fetch, user_html,
                                   allow_signed_in=allow_signed_in, allow_browser=allow_browser,
                                   record=remember)
    _note_access(report, lr)
    if lr.handoff:
        report.reason = f"Stopped at a browser verification page ({lr.handoff['reason']}) -- handed to you."
        _log(report)
        return NovelImportResult(url, "", "", "", ladder=lr), report
    if not lr.ok:
        report.reason = _unreachable_reason(report)
        _log(report)
        raise _no_content(_unreachable_message(report, lr), report)
    data, report = extract_novel(lr.html, url, engine, use_cache, report, remember=remember)
    _log(report)
    if data is None:
        raise _no_content(report.reason, report)
    title = data.get("chapter_title") or ax.PageModel(lr.html, url).page_title
    return NovelImportResult(url, title or "", data["content"], data.get("method") or "", ladder=lr), report


# ---------------------------------------------------------------------------
# Comic chapters
# ---------------------------------------------------------------------------

def measured(candidates) -> dict:
    return {c.url: {"width": c.width, "height": c.height, "sha256": c.sha256}
            for c in candidates if c.sha256}


def _drop_unusable(data, by_url):
    """Hard facts from the download (unreadable, undersized) beat any
    classification."""
    for p in data["pages"]:
        c = by_url.get(p["resource_url"])
        if p["role"] == "content" and c is not None and c.reject_reason and \
                any(k in c.reject_reason for k in _HARD_REJECTS):
            p["role"], p["index"], p["reason"] = "other", None, c.reject_reason
    for i, p in enumerate(q for q in data["pages"] if q["role"] == "content"):
        p["index"] = i


def _offer_comic_profile(page, candidates, data, report, domain, origin):
    rules = profiles.infer_comic_rules(candidates, data)
    if not rules:
        report.profile["generated"] = False
        report.note("Couldn't turn this result into a reusable profile.")
        return
    picked, why = profiles.apply_comic_rules(candidates, rules)
    if picked is None:
        check = {"valid": False, "problems": [why]}
    else:
        roles = {c.url: "content" for c in picked}
        check = ax.comic_data(page, candidates, roles, method="profile",
                              order={c.url: i for i, c in enumerate(picked)})
        ax.validate_comic(check, page, measured(candidates))
        want = [p["resource_url"] for p in data["pages"] if p["role"] == "content"]
        got = [c.url for c in picked]
        if set(want) != set(got):
            check["valid"] = False
            check["problems"].append("the profile doesn't pick out the same pages as this result")
    _offer(report, domain, "comic", rules, check, origin)


def _classify_comic(page, candidates, engine, report, use_cache, download=None):
    """The comic AI tier: one call (or a cache hit) classifying candidates
    already surfaced. The classification itself downloads nothing; with a
    `download` (the existing downloader), only the pages it picked that
    haven't been fetched yet are then fetched, and their download results
    are applied as hard facts."""
    picks, h = _llm_picks("comic", ax.comic_payload(page, candidates), ax.COMIC_PROMPT, engine,
                          report, 3000, use_cache)
    if picks is None:
        return None
    data = ax.comic_from_picks(page, candidates, picks)
    by_url = {c.url: c for c in candidates}
    if download is not None:
        download([by_url[p["resource_url"]] for p in data["pages"] if p["role"] == "content"
                  and not by_url[p["resource_url"]].content])
    _drop_unusable(data, by_url)
    ax.validate_comic(data, page, measured(candidates))
    if data["valid"] and not report.cache_hit:
        _cache_put("comic", h, page.url, picks, data, engine)
    return data


def classify_comic_page(html: str, url: str, engine=None, use_cache: bool = True):
    """Classifies a comic page's candidate images (content pages, order,
    covers, ads, thumbnails, duplicates) from the HTML alone -- the AI
    tier on its own, with nothing downloaded. Returns (data or None, report)."""
    report = ExtractionReport(url, "comic")
    page = ax.PageModel(html, url)
    data = _classify_comic(page, ax.comic_candidates(html, url), engine, report, use_cache)
    if data is not None:
        _done(report, data, "llm", "AI-assisted classification of the page's images.")
    return data, report


def extract_comic(page, candidates, engine=None, download=None, remember: bool = True,
                  learn: bool = True,
                  use_cache: bool = True, report: ExtractionReport = None):
    """Runs the ladder on the candidates the deterministic pass surfaced.
    `download(candidates)` is the existing resource downloader
    (generic_import.download_candidates); classification itself never
    downloads. Returns (data or None, report)."""
    report = report or ExtractionReport(page.url, "comic")
    url = page.url
    domain = profiles.domain_of(url)
    report.profile = _fresh_profile_state(domain)
    by_url = {c.url: c for c in candidates}
    download = download or (lambda cs: None)
    failed_profile = None

    prof = profiles.active(domain, "comic")
    if prof:
        report.profile.update(existed=True, version=prof["version"])
        picked, why = profiles.apply_comic_rules(candidates, prof["rules"])
        if picked:
            download(picked)
            roles = {c.url: "content" for c in picked}
            reasons = {c.url: "not matched by the saved profile" for c in candidates
                       if c.url not in roles}
            data = ax.comic_data(page, candidates, roles, method="profile",
                                 order={c.url: i for i, c in enumerate(picked)}, reasons=reasons)
            _drop_unusable(data, by_url)
            ax.validate_comic(data, page, measured(candidates))
            if data["valid"]:
                profiles.record_use(domain, "comic", prof["version"], True)
                report.profile["used"] = True
                return _done(report, data, "profile",
                             f"Used the saved profile for {domain} (v{prof['version']}) -- 0 AI calls."), report
            why = "; ".join(data["problems"]) or "its result failed the checks"
        profiles.record_use(domain, "comic", prof["version"], False, why)
        failed_profile = prof
        report.profile["failed_version"] = prof["version"]
        report.note(f"The saved profile for {domain} (v{prof['version']}) no longer fits this page "
                    f"({why}). It's kept as it was; ran the full ladder instead.")

    # The existing filter, on <img>/<source> candidates first (as before
    # Step 23g). Script-listed (manifest) URLs are only downloaded when the
    # tags alone give nothing -- a script blob can list hundreds of
    # unrelated covers, and each download is a paced request.
    pool = [c for c in candidates if c.attr != "manifest"]
    download(pool)
    kept, rejected = generic_import.filter_candidates(pool, url, remember, learn)
    if not kept and len(pool) < len(candidates):
        pool = candidates
        download(pool)
        kept, rejected = generic_import.filter_candidates(pool, url, remember, learn)
    det = ax.comic_from_filter(page, pool, kept, rejected)
    ax.validate_comic(det, page, measured(pool))
    ambiguous = ax.comic_needs_review(det, pool)
    if not ambiguous:
        if failed_profile:
            _offer_comic_profile(page, pool, det, report, domain, "deterministic")
        return _done(report, det, "deterministic",
                     "The content-vs-chrome filter found the pages -- 0 AI calls."), report
    report.note("Deterministic result was ambiguous: " + "; ".join(ambiguous))

    data = _classify_comic(page, candidates, engine, report, use_cache, download)
    if data is not None:
        if data["valid"]:
            _offer_comic_profile(page, candidates, data, report, domain, "llm")
            return _done(report, data, "llm",
                         "AI-assisted classification picked out the pages (nothing downloaded by "
                         "the AI step itself)."), report
        report.note("The AI answer failed the independent checks: " + "; ".join(data["problems"]))

    if any(p["role"] == "content" for p in det["pages"]):
        report.reason = "Pages found, but some may be covers/ads/thumbnails -- review before importing."
        report.needs_review = True
        return _done(report, det, "deterministic", "Kept the filter's result for review."), report
    report.reason = ("Couldn't find page images here -- every image on the page was filtered out "
                     "as site furniture"
                     + (", and no AI engine is set up for the fallback" if not report.llm_calls
                        and not report.cache_hit else "") + ".")
    report.needs_review = True
    return None, report


def import_comic(url: str, engine=None, client=None, rendered_fetch=None, user_html: str = None,
                 remember: bool = True, use_cache: bool = True, allow_signed_in: bool = True,
                 allow_browser: bool = True, budget=None, hold_profiles: bool = False,
                 learn: bool = True):
    """The generic comic import with the Step 23g ladder. Returns
    (ComicImportResult, report); raises NoContentFound (with `.report`).
    `budget` (generic_import.DownloadBudget) caps the image downloads;
    `hold_profiles`: never auto-save a generated site profile; `learn=False`
    reads the site's cross-chapter image memory but doesn't add to it."""
    report = ExtractionReport(url, "comic", hold_profiles=hold_profiles)
    client = generic_import._client(client, url)
    lr = generic_import.fetch_page(url, client, rendered_fetch, user_html,
                                   allow_signed_in=allow_signed_in, allow_browser=allow_browser)
    _note_access(report, lr)
    out = ComicImportResult(page_url=url, ladder=lr)
    if lr.handoff:
        report.reason = f"Stopped at a browser verification page ({lr.handoff['reason']}) -- handed to you."
        _log(report)
        return out, report
    if not lr.ok:
        report.reason = _unreachable_reason(report)
        _log(report)
        raise _no_content(_unreachable_message(report, lr), report)
    candidates = ax.comic_candidates(lr.html, url)
    if not candidates:
        report.reason = ("The page has no image tags or listed image URLs this importer "
                         "recognizes -- upload the pages manually instead.")
        _log(report)
        raise _no_content("Couldn't find page images here -- " + report.reason, report)
    page = ax.PageModel(lr.html, url)
    data, report = extract_comic(
        page, candidates, engine,
        download=lambda cs: generic_import.download_candidates(cs, url, client, budget),
        remember=remember, learn=learn, use_cache=use_cache, report=report)
    _log(report)
    if data is None:
        raise _no_content(report.reason, report)
    out.images, out.rejected = images_for(data, candidates)
    return out, report


def images_for(data: dict, candidates) -> tuple:
    """(kept ImageCandidates in reading order, the rest with a reason)."""
    by_url = {c.url: c for c in candidates}
    kept = [by_url[p["resource_url"]] for p in sorted(
        (p for p in data["pages"] if p["role"] == "content"), key=lambda p: p["index"])
        if p["resource_url"] in by_url and by_url[p["resource_url"]].content]
    rejected = []
    for p in data["pages"]:
        c = by_url.get(p["resource_url"])
        if c is not None and c not in kept:
            c.reject_reason = c.reject_reason or f"{p['role']}: {p.get('reason') or 'not a page'}"
            rejected.append(c)
    return kept, rejected


# ---------------------------------------------------------------------------
# Video / audio / subtitle resources on an unrecognized page
# ---------------------------------------------------------------------------

def identify_media(url: str, html: str, engine=None, use_cache: bool = True):
    """Identifies media resources on a page no existing video path
    recognizes. Returns (data or None, report). Identifies only: the
    chosen resource is downloaded by the existing video download path."""
    from . import detect, front_door, registry
    report = ExtractionReport(url, "video")
    if registry.find_for_url(url) is not None:
        report.extraction_tier = "adapter"
        report.reason = "A dedicated adapter handles this URL -- nothing to identify."
        return None, report
    if front_door.is_video_url(url):
        report.reason = "yt-dlp already recognizes this site -- use Import video directly."
        return None, report
    page = ax.PageModel(html, url)
    report.protection = [r.value for r in detect.classify(200, {}, html, url, url)
                         if r in PROTECTION_REASONS]
    candidates = ax.media_candidates(html, url)
    if not candidates:
        report.reason = ("No video, audio or subtitle resources are exposed on this page (it may "
                         "load them only after interaction or behind a login).")
        _log(report)
        return None, report
    det, ambiguous = ax.deterministic_media(page, candidates, report.protection)
    ax.validate_media(det, page)
    if not ambiguous and det["valid"]:
        _done(report, det, "deterministic", "Found one clear media resource -- 0 AI calls.")
        _log(report)
        return det, report
    report.note("Deterministic result was ambiguous: " + "; ".join(ambiguous or det["problems"]))
    picks, h = _llm_picks("video", ax.media_payload(page, candidates), ax.MEDIA_PROMPT, engine,
                          report, 1500, use_cache)
    if picks is not None:
        data = ax.media_from_picks(page, candidates, picks, report.protection)
        ax.validate_media(data, page)
        if data["valid"]:
            if not report.cache_hit:
                _cache_put("video", h, url, picks, data, engine)
            _done(report, data, "llm", "AI-assisted classification identified the resources "
                                       "(nothing downloaded).")
            _log(report)
            return data, report
        report.note("The AI answer failed the independent checks: " + "; ".join(data["problems"]))
    report.reason = "Found media resources, but which one is the content isn't clear -- pick one yourself."
    _done(report, det, "deterministic", "Listed every resource found for you to choose from.")
    report.needs_review = True
    _log(report)
    return det, report


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def recent_extractions(limit: int = 30) -> list:
    """The Source Diagnostics view's rows: extraction attempts from the
    shared access-attempt log, newest first."""
    return [a for a in store.recent_attempts(GENERIC_SOURCE, limit=limit * 3)
            if a.get("kind") == "extraction"][:limit]


def describe_profile(p: dict) -> str:
    if not p:
        return "Site profile: not applicable."
    bits = []
    if p.get("used"):
        bits.append(f"saved profile v{p['version']} used")
    elif p.get("existed"):
        bits.append(f"saved profile v{p['version']} existed but no longer fit (kept)")
    else:
        bits.append("no saved profile for this site")
    if p.get("saved"):
        bits.append(f"new profile v{p['saved']} generated, validated and saved")
    elif p.get("pending"):
        bits.append("a validated profile candidate is waiting for your approval")
    elif p.get("generated") and p.get("saved") is False:
        bits.append("a generated profile failed validation and was not saved")
    return f"Site profile ({p.get('domain')}): " + "; ".join(bits) + "."
