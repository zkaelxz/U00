"""
services/maintenance_assistant_service.py -- the in-app AI maintenance
assistant, read-only v1. UI-free; the API
(api/routers/assistant_routes.py, every route PC-only) and the React
Assistant page call it.

What it does: answers "why did X fail" / "where is Y" / "what changed"
questions about Baihe itself by letting a configured LLM engine call a
fixed set of READ-ONLY tools (list/read/search the code, read-only git,
the redacted log, job history, the support report, dependency and model
checks, one test file at a time), then returns the answer, any proposed
fix as a patch the user applies by hand, and backlog items it suggests.

Read-only is structural, not a prompt instruction: READ_ONLY_TOOLS below
is the only tool table, every entry is 🟢 in action_tiers, and no tool
that writes a file, runs a mutating git command, changes a setting or
touches the library exists in this module. A tool name the model invents
("write_file", "apply_patch") is answered "no such tool" and nothing
runs. The backlog (roadmap item 7) is written only by the user through
its own routes; the assistant can only *suggest* items.

The pieces live beside this file: services/assistant_settings_service.py
(settings, consent, tier ladder), assistant_tools_service.py (the tool table),
assistant_repo_access.py (redaction, path rules, git runner),
assistant_diagnosis_service.py (the tool loop) and assistant_backlog_service.py.
This module answers one turn (ask), the developer report, the independent
review and the changelog.

qa._dispatch_chat makes the multi-engine Claude/OpenAI-shaped/Gemini/Ollama
chat call. The assistant answers from what it can see and says so honestly
otherwise; it searches the real code (frontend/src included) with
search_code.

Safety rails on every tool: paths are repo-relative, resolved and kept
inside the repo, and never reach the library folder, .git internals,
virtualenvs, node_modules, dotfiles or anything named like a secret; all
text leaving a tool has keys/tokens and this PC's folder prefixes
removed (_redact); in a git checkout only git-tracked files are
readable (so an untracked local config never reaches the engine);
subprocesses (git, pytest) take a fixed argument list, a timeout, and no
shell; a test run gets a throwaway library and no keys.
"""

import threading

from services import diagnostics_gaps_service
from services.assistant_diagnosis_service import (MAX_EVIDENCE_CHARS, _chat, _check_evidence, _check_history,
                                                  _check_question, _clean_answer, _tier_failure, build_engine,
                                                  extract_backlog_suggestions, extract_patches, run_diagnosis,
                                                  tools_prompt)
from services.assistant_repo_access import _clip, _git, _redact
from services.assistant_settings_service import (DEFAULT_ENGINE, _check_engine_name, _check_model,
                                                 _is_local_engine, _require_developer_mode, _tier_of,
                                                 get_settings, require_cloud_consent, tier_ladder)
# transcribe_service still reads Developer Mode through this module; its
# owner repoints that call and this line goes.
from services.assistant_settings_service import developer_mode_enabled  # noqa: F401
from services.assistant_tools_service import _ref_arg
from services.service_errors import ConflictError, InvalidInputError, ServiceError


_ASK_LOCK = threading.Lock()


def _check_escalation(engine_name, consent, ladder, model=None):
    """An escalation names a ladder tier; one that leaves this PC needs
    consent=True on this very request (on top of the saved per-provider
    consent): the page asks before each send."""
    if not engine_name:
        raise InvalidInputError("An escalation names the engine to ask.")
    if _tier_of(engine_name, ladder)[0] is None:
        raise InvalidInputError("That engine isn't one of the assistant's tiers. Add it in the "
                                "assistant's settings first.")
    if not _is_local_engine(engine_name, model) and consent is not True:
        raise ConflictError(
            f"Asking {engine_name} sends your question, the recent chat and the redacted tool "
            "output to that provider. Confirm it first.",
            details={"reason": "escalation_consent_required", "engine": engine_name})


def _with_evidence(question: str, evidence: str) -> str:
    if not evidence:
        return question
    return (question + "\n\nA smaller model already looked into this. What its read-only tools "
            "returned (redacted; check it rather than trusting it):\n" + evidence)


def ask(question: str, chat_history=None, engine_name: str = None, model: str = None,
        chat=None, escalate: bool = False, consent: bool = False, evidence: str = "") -> dict:
    """One assistant turn. Stateless: the client keeps the history (and
    the evidence it may hand to the next tier). `escalate` re-asks on a
    higher tier with the previous tier's redacted tool output."""
    _require_developer_mode()
    question = _check_question(question)
    history = _check_history(chat_history)
    evidence = _check_evidence(evidence)
    if evidence and not escalate:
        raise InvalidInputError("evidence is only sent when escalating to another tier.")
    ladder = tier_ladder()
    engine_name = _check_engine_name(engine_name)
    if escalate:
        _check_escalation(engine_name, consent, ladder, _check_model(model))
    target = engine_name or get_settings()["engine"] or DEFAULT_ENGINE
    try:
        engine, engine_name, model = build_engine(target, model)
    except (ConflictError, InvalidInputError):
        raise
    except ServiceError as e:
        raise _tier_failure(e, target, ladder) from None
    if not _ASK_LOCK.acquire(blocking=False):
        raise ConflictError("The assistant is already answering a question. Try again when it's done.")
    try:
        try:
            result = run_diagnosis(_with_evidence(question, evidence), history, engine, chat=chat)
        except (ConflictError, InvalidInputError):
            raise
        except ServiceError as e:
            raise _tier_failure(e, engine_name, ladder) from None
        raw = _redact(result["answer"])
        answer = _clean_answer(raw)
        if raw.count("```") % 2:
            answer += ("\n\n[The answer looks cut off (an unclosed code block); any fix in it "
                       "is incomplete. Ask for just the patch.]")
        patches = extract_patches(raw)
        review, review_skipped = None, ""
        settings = get_settings()
        if patches and settings["roles_enabled"]:
            # The escalation dialog lists only the tier being asked; a cloud
            # reviewer would send the question, answer and patches to a
            # provider the user didn't confirm.
            reviewer = settings["review_engine"]
            if escalate and reviewer and not _is_local_engine(reviewer, settings["review_model"]):
                review_skipped = ("The reviewer is a cloud engine, so it wasn't asked as part of "
                                  "this escalation. Ask for a review separately.")
            else:
                review = independent_review(question, answer, patches, engine_name, chat=chat,
                                            tests_already_run=result["tests_run"])
    finally:
        _ASK_LOCK.release()
    tier, next_engine = _tier_of(engine_name, ladder)
    gathered = "\n\n".join(x for x in [evidence] + result["evidence"] if x)
    return {
        "answer": answer,
        "proposed_patches": patches,
        "suggested_backlog": extract_backlog_suggestions(raw),
        "tool_calls": result["tool_calls"],
        "engine": engine_name,
        "model": model,
        "review": review,
        "review_skipped": review_skipped,
        "tier": tier,
        "local": _is_local_engine(engine_name, model),
        "next_engine": next_engine,
        # What the next tier (or a developer report) gets if the user asks
        # for it: redacted, and cut to the size the request accepts.
        "evidence": _clip(_redact(gathered), MAX_EVIDENCE_CHARS - 100),
    }


# ---------------------------------------------------------------------------
# Last tier: a problem report for a developer, copied by hand (no upload)
# ---------------------------------------------------------------------------

def developer_report(chat_history=None, question: str = "", evidence: str = "") -> dict:
    """The chat, what the tools found and the Diagnostics support report
    as one redacted text for the user to copy. Nothing is sent anywhere."""
    _require_developer_mode()
    history = _check_history(chat_history)
    question = _check_question(question) if question else ""
    evidence = _check_evidence(evidence)
    parts = ["Baihe problem report",
             "Prepared on this PC and not sent anywhere. Keys, tokens and this PC's folder "
             "names were removed; read it before you share it.", "", "== Conversation =="]
    for m in history:
        parts.append(("You: " if m["role"] == "user" else "Assistant: ") + m["content"])
    if question:
        parts.append("You (not answered yet): " + question)
    if not history and not question:
        parts.append("(no messages)")
    if evidence:
        parts += ["", "== What the assistant's read-only tools returned ==", evidence]
    try:
        support = diagnostics_gaps_service.build_support_report()
    except Exception as e:  # the report is still useful without it
        support = "The support report couldn't be built: " + _redact(str(e))[:200]
    parts += ["", "== Support report ==", support]
    return {"report": _redact("\n".join(parts))}


# ---------------------------------------------------------------------------
# Independent cross-provider review of a proposed fix
# ---------------------------------------------------------------------------

def build_review_engine():
    """The review role's engine: the saved review_engine only (never a
    fallback to the implement engine). Returns (engine, name, model) or
    raises ServiceError with a user-facing reason."""
    from services import line_ai_service, reader_service, settings_service
    settings = get_settings()
    name = settings["review_engine"]
    if not name:
        raise ConflictError("No review engine is set. Pick one that differs from the "
                            "implementing engine in the assistant's settings.")
    model = settings["review_model"]
    require_cloud_consent(name, model)  # the reviewer reads the same code and logs
    engine = reader_service.llm_engine(name, model)
    line_ai_service.refuse_if_over_monthly_cap(name, settings_service.get("gemini_free_tier"))
    return engine, name, model


def independent_review(question: str, answer: str, patches: list, implement_engine: str,
                       chat=None, tests_already_run: bool = False) -> dict:
    """Runs the review role on a DIFFERENT engine and returns
    {engine, model, verdict, notes, tool_calls}. Never raises: a review
    that can't run comes back as verdict "unavailable" with the reason,
    and the proposed fix is still shown -- nothing is resolved silently."""
    from services import assistant_roles_service as roles
    try:
        engine, name, model = build_review_engine()
        if roles.same_backend(implement_engine, name):
            return {"engine": name, "model": model, "verdict": "unavailable", "tool_calls": [],
                    "notes": (f"The review engine ({name}) is the same as the implementing "
                              "engine. Pick a different engine for an independent review.")}
        result = run_diagnosis(roles.review_request(question, answer, patches), [], engine,
                               chat=chat, system_prompt=roles.review_system_prompt(tools_prompt()),
                               tests_already_run=tests_already_run)
    except ServiceError as e:
        return {"engine": get_settings()["review_engine"], "model": None, "verdict": "unavailable",
                "notes": _redact(e.message)[:500], "tool_calls": []}
    except Exception as e:  # e.g. an engine whose optional package isn't installed
        return {"engine": get_settings()["review_engine"], "model": None, "verdict": "unavailable",
                "notes": "The review engine couldn't run: " + _redact(str(e))[:300],
                "tool_calls": []}
    verdict, notes = roles.parse_verdict(_redact(result["answer"]))
    return {"engine": name, "model": model, "verdict": verdict,
            "notes": _clean_answer(notes), "tool_calls": result["tool_calls"]}


# ---------------------------------------------------------------------------
# Changelog (roadmap item 8)
# ---------------------------------------------------------------------------

MAX_CHANGELOG_COMMITS = 300


def changelog(from_ref: str, to_ref: str = "HEAD", engine_name: str = None, model: str = None,
              chat=None) -> dict:
    """A plain-English summary of the commits in from_ref..to_ref. Uses
    commit subjects (bodies would crowd out older commits); the count
    comes from rev-list, and `truncated` says when the newest
    MAX_CHANGELOG_COMMITS were all the model saw."""
    _require_developer_mode()
    from_ref = _ref_arg({"r": from_ref}, "r")
    to_ref = _ref_arg({"r": to_ref or "HEAD"}, "r", "HEAD")
    if not from_ref:
        raise InvalidInputError("from_ref is required.")
    span = f"{from_ref}..{to_ref}"
    try:
        commit_count = int(_git("rev-list", "--count", span, "--").strip() or 0)
    except ValueError:
        commit_count = 0
    if commit_count == 0:
        return {"changelog": "No commits in that range.", "commit_count": 0,
                "from_ref": from_ref, "to_ref": to_ref, "truncated": False}
    log = _git("log", f"--max-count={MAX_CHANGELOG_COMMITS}", "--date=short",
               "--format=%h %ad %s", span, "--")
    shown = len([ln for ln in log.splitlines() if ln.strip()])
    truncated = shown < commit_count
    engine, _name, _model = build_engine(engine_name, model)
    system_prompt = (
        "Write release notes for the owner of Baihe (a subtitle/translation app) from the "
        "git commit subjects below. Group into short sections (New, Fixed, Changed, Under the "
        "hood), plain English, one line per user-visible change, merge commits that are "
        "parts of one change, skip pure test/doc churn unless it matters. Don't invent "
        "anything that isn't in the commits.")
    text = (chat or _chat)(system_prompt, [{"role": "user", "content": _redact(log)}], engine)
    return {"changelog": _redact(text).strip(), "commit_count": commit_count,
            "from_ref": from_ref, "to_ref": to_ref, "truncated": truncated}
