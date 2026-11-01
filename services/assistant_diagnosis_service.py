"""
services/assistant_diagnosis_service.py -- one diagnosis run of the
maintenance assistant: the system and tool prompt, checks on the question,
chat history and evidence, building a chat engine, the tool loop with
TOOL-line parsing, and the engine-failure messages. UI-free.
"""

import json
import math
import re

from lib import http
from services.assistant_backlog_service import MAX_BACKLOG_TEXT
from services.assistant_repo_access import _redact
from services.assistant_settings_service import (DEFAULT_ENGINE, _check_engine_name, _check_model, _tier_of,
                                                 get_settings, require_cloud_consent)
from services.assistant_tools_service import READ_ONLY_TOOLS, run_tool
from lib.errors import (DependencyUnavailableError, InvalidInputError, ServiceError,
                                     UnsupportedOperationError)


MAX_QUESTION_CHARS = 4000
MAX_CHAT_TURNS = 20
MAX_CHAT_TURN_CHARS = 8000
MAX_CHAT_TOTAL_CHARS = 60000
# Redacted tool output one tier hands to the next when the user escalates.
MAX_EVIDENCE_CHARS = 16000
MAX_ROUNDS = 6              # model turns per question (tool rounds + the final answer)
MAX_TOOL_CALLS_PER_ROUND = 4
MAX_OUTPUT_TOKENS = 4000


_TOOL_LINE_RE = re.compile(r"^\s*TOOL:\s*(\{.*\})\s*$", re.MULTILINE)
_BACKLOG_LINE_RE = re.compile(r"^\s*BACKLOG:\s*(bug|feature|note)\s*\|\s*(.+?)\s*$",
                              re.MULTILINE | re.IGNORECASE)
_PATCH_RE = re.compile(r"```(?:diff|patch)\s*\n(.*?)```", re.DOTALL)
_PATCH_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(\S+)", re.MULTILINE)


def tools_prompt() -> str:
    """How to call the read-only tools; shared by every role."""
    tool_lines = "\n".join(f"- {name}: {desc} Example args: {example}"
                           for name, (_fn, desc, example) in READ_ONLY_TOOLS.items())
    return (
        "To use a tool, reply with one or more lines of exactly this form and nothing "
        "else on those lines:\n"
        'TOOL: {"id": "t1", "name": "<tool>", "args": {...}}\n'
        f"Give each call a new id. At most {MAX_TOOL_CALLS_PER_ROUND} calls per reply. "
        "You will get each result back labelled with its id. When you have enough, "
        "reply with your final answer and no TOOL lines.\n\n"
        f"Tools:\n{tool_lines}"
    )


def _system_prompt() -> str:
    return (
        "You are the maintenance assistant inside Baihe, a subtitle/translation app, "
        "helping its owner diagnose problems in the app itself. You can only READ: "
        "there is no tool that edits files, runs a mutating git command, changes "
        "settings or touches the user's library, and you must not claim to have "
        "changed anything.\n\n"
        + tools_prompt() + "\n\n"
        "Final answer rules: ground every claim in what the tools showed (name the "
        "file and line). If you can't tell, say so plainly instead of guessing. If you "
        "propose a code fix, put it in a ```diff fenced block as a unified diff "
        "(--- a/path, +++ b/path, @@ hunks) against the current files -- the user "
        "reviews and applies it by hand; it is never applied for them. If something "
        "should be tracked for later, add a line 'BACKLOG: bug | <one sentence>' "
        "(or feature/note); the user decides whether to add it."
    )


def _unique_id(wanted: str, used: set) -> str:
    call_id, n = wanted, 1
    while call_id in used:
        n += 1
        call_id = f"{wanted}-{n}"
    used.add(call_id)
    return call_id


def _parse_tool_calls(reply: str, used_ids: set | None = None) -> list:
    """TOOL lines -> calls, each with an id unique across the whole
    question (`used_ids` carries them between rounds), so every RESULT
    and every tool_calls row is matched by id, never by order."""
    used_ids = set() if used_ids is None else used_ids
    calls = []
    for match in _TOOL_LINE_RE.finditer(reply or ""):
        try:
            obj = json.loads(match.group(1))
        except (ValueError, TypeError):
            obj = None
        if not isinstance(obj, dict):
            calls.append({"id": _unique_id("bad", used_ids), "name": None, "args": {},
                          "malformed": True})
            continue
        call_id = obj.get("id")
        if not isinstance(call_id, str) or not re.match(r"^[A-Za-z0-9_-]{1,20}$", call_id):
            call_id = "call"
        args = obj.get("args")
        calls.append({"id": _unique_id(call_id, used_ids), "name": obj.get("name"),
                      "args": args if isinstance(args, dict) else {}, "malformed": False})
    return calls


def extract_patches(answer: str) -> list:
    patches = []
    for n, match in enumerate(_PATCH_RE.finditer(answer or ""), 1):
        patch = match.group(1).rstrip() + "\n"
        files = sorted({f for f in _PATCH_FILE_RE.findall(patch) if f != "/dev/null"})
        patches.append({"id": f"p{n}", "patch": patch, "files": files})
    return patches


def extract_backlog_suggestions(answer: str) -> list:
    out = []
    for kind, text in _BACKLOG_LINE_RE.findall(answer or ""):
        out.append({"kind": kind.lower(), "text": text.strip()[:MAX_BACKLOG_TEXT]})
    return out[:10]


def _clean_answer(answer: str) -> str:
    return _BACKLOG_LINE_RE.sub("", _TOOL_LINE_RE.sub("", answer or "")).strip()


def _check_history(chat_history) -> list:
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
    # An empty turn (an answer that was only BACKLOG lines) is dropped:
    # some providers reject empty message content. The thread is redacted
    # too: it can be escalated to a cloud engine, and a pasted log line
    # may carry a key or this PC's folder names.
    return [{"role": m["role"], "content": _redact(m["content"])} for m in history
            if m["content"].strip()]


def _check_question(question) -> str:
    if not isinstance(question, str) or not question.strip():
        raise InvalidInputError("question is required.")
    if len(question) > MAX_QUESTION_CHARS:
        raise InvalidInputError(f"question must be at most {MAX_QUESTION_CHARS} characters.")
    return _redact(question.strip())


def _check_evidence(evidence) -> str:
    if evidence is None or evidence == "":
        return ""
    if not isinstance(evidence, str) or len(evidence) > MAX_EVIDENCE_CHARS:
        raise InvalidInputError(f"evidence must be text of at most {MAX_EVIDENCE_CHARS} characters.")
    return _redact(evidence).strip()


def build_engine(engine_name=None, model=None):
    """A chat-capable engine with its key resolved server-side (never
    from the request). Same rules as the Reader's Q&A engine."""
    from services import reader_service
    settings = get_settings()
    saved_engine = settings["engine"] or DEFAULT_ENGINE
    engine_name = _check_engine_name(engine_name) or saved_engine
    model = _check_model(model) or (settings["model"] if engine_name == saved_engine else None)
    from services import line_ai_service, settings_service
    require_cloud_consent(engine_name, model)
    engine = reader_service.llm_engine(engine_name, model)
    line_ai_service.refuse_if_over_monthly_cap(engine_name, bool(settings_service.get("gemini_free_tier")))
    return engine, engine_name, model


def _chat(system_prompt: str, messages: list, engine) -> str:
    import qa
    try:
        return qa.dispatch_chat(system_prompt, messages, engine,
                                 max_tokens=MAX_OUTPUT_TOKENS) or ""
    except ServiceError:
        raise
    except Exception as e:  # engine/network failure: never leak a key or path
        raise ServiceError("The engine call failed: " + _redact(str(e))[:300],
                           details={"reason": _failure_reason(e)}) from None


def _failure_reason(exc) -> str:
    """rate_limited / unreachable / failed, so the page can say which in
    plain words and offer the next tier."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc).lower()
    if status == 429 or "429" in text or "rate limit" in text or "resource_exhausted" in text:
        return "rate_limited"
    if any(s in text for s in ("connection refused", "failed to establish", "max retries exceeded",
                               "connecterror", "connection error", "timed out")):
        return "unreachable"
    return "failed"


_FAILURE_TEXT = {
    "rate_limited": "{name} is busy or over its rate limit (429). Wait a minute, or ask the next tier.",
    "unreachable": "{name} didn't answer. If it's Ollama, check it's running on this PC.",
    "unavailable": "{name} isn't set up here (no key, or its package isn't installed).",
    "spend_cap": "This month's spending cap is used up, so {name} wasn't called.",
}


def _tier_failure(error: ServiceError, engine_name: str, ladder: list) -> ServiceError:
    """Adds which tier failed and which one is next to an engine failure.
    Nothing is sent to the next tier: the user decides."""
    details = error.details if isinstance(error.details, dict) else {}
    reason = details.get("reason")
    if reason == "no_key":
        reason = "unavailable"
    if reason is None:
        if isinstance(error, http.FetchError):
            # Ollama that isn't running surfaces as a fetch failure, which is
            # a DependencyUnavailableError and would read as "not set up".
            reason = "unreachable"
        elif isinstance(error, UnsupportedOperationError):
            # The monthly cap refusal, or an engine that can't chat.
            reason = "spend_cap" if "spending cap" in error.message else "unavailable"
        else:
            reason = "unavailable" if isinstance(error, DependencyUnavailableError) else "failed"
    tier, next_engine = _tier_of(engine_name, ladder)
    template = _FAILURE_TEXT.get(reason)
    if template:
        error.message = template.format(name=engine_name.capitalize()) + " (" + _redact(error.message)[:200] + ")"
    error.details = {**details, "reason": reason, "engine": engine_name, "tier": tier,
                     "next_engine": next_engine}
    return error


def _shown_args(args: dict) -> dict:
    """The call's args as shown to the user: scalars only, redacted,
    short."""
    shown = {}
    for key, value in list(args.items())[:8]:
        if isinstance(value, float) and not math.isfinite(value):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            shown[_redact(str(key)[:40])] = _redact(value)[:200] if isinstance(value, str) else value
    return shown


def run_diagnosis(question: str, history: list, engine, chat=None, system_prompt=None,
                  tests_already_run: bool = False) -> dict:
    """The tool loop. `chat(system_prompt, messages, engine) -> str` is
    injectable for tests. Returns the final answer text and the tool
    calls made (id, name, args, ok, summary). `system_prompt` sets the
    role (the reviewer); the tool table is the same read-only one
    for every role."""
    chat = chat or _chat
    system_prompt = system_prompt or _system_prompt()
    messages = list(history) + [{"role": "user", "content": question}]
    calls_made = []
    evidence = []
    used_ids = set()
    tests_run = tests_already_run
    reply = ""
    for round_no in range(MAX_ROUNDS):
        reply = chat(system_prompt, messages, engine)
        calls = _parse_tool_calls(reply, used_ids)
        if not calls:
            break
        if round_no == MAX_ROUNDS - 1:
            reply = ("I ran out of tool rounds before reaching an answer. Here is what I "
                     "looked at so far; ask a narrower question to continue.")
            break
        results = []
        for call in calls[:MAX_TOOL_CALLS_PER_ROUND]:
            if call["malformed"]:
                outcome = {"ok": False, "output": "That TOOL line wasn't valid JSON."}
            elif call["name"] == "run_tests" and tests_run:
                outcome = {"ok": False, "output": "Only one test run per question."}
            else:
                tests_run = tests_run or call["name"] == "run_tests"
                outcome = run_tool(call["name"], call["args"])
            calls_made.append({
                "id": call["id"], "name": _redact(str(call["name"] or "")[:60]),
                "args": _shown_args(call["args"]),
                "ok": outcome["ok"],
                "summary": outcome["output"].splitlines()[0][:200] if outcome["output"] else "",
            })
            results.append(f"RESULT {call['id']} ({call['name']}, "
                           f"{'ok' if outcome['ok'] else 'failed'}):\n{outcome['output']}")
        evidence.extend(results)
        if len(calls) > MAX_TOOL_CALLS_PER_ROUND:
            results.append(f"Only the first {MAX_TOOL_CALLS_PER_ROUND} calls ran.")
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": "\n\n".join(results)})
    return {"answer": reply, "tool_calls": calls_made, "tests_run": tests_run,
            "evidence": evidence}
