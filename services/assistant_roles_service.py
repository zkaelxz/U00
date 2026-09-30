"""
services/assistant_roles_service.py -- multi-agent roles for the
maintenance assistant (roadmap Step 60). UI-free; used by
services/maintenance_assistant_service.ask when the "roles" setting is on
(off by default).

v1 is exactly the roadmap's scoped pattern: implement -> independent
review -> show both. The "implement" role is the assistant's normal
diagnosis turn (its engine proposes the fix). When that answer carries a
proposed patch, the "review" role -- a DIFFERENT configured engine
(provider), never the same one called twice -- critiques it with the
same read-only tools, and both perspectives go back to the user. Nothing
is resolved automatically in either direction: a review that raises
concerns is shown next to the fix, the fix is not dropped, and a review
that agrees does not apply anything.

Roles are prompts over the existing engine dispatch (Step 36's capability
registry isn't built; the capability names below are the ones Step 36
would route). No role gets any tool beyond maintenance_assistant_service's
read-only table. Out of scope (roadmap item 4): an autonomous tester
agent and a release agent.
"""

import re

ROLES = {
    "implement": {"capability": "coding.implement",
                  "label": "Diagnose and propose a fix"},
    "review": {"capability": "coding.review",
               "label": "Independently review the proposed fix"},
}

VERDICTS = ("agrees", "concerns", "unclear", "unavailable")
_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(AGREES|CONCERNS)\b[ \t]*\n?", re.IGNORECASE | re.MULTILINE)


def review_system_prompt(tools_prompt: str) -> str:
    return (
        "You are the independent REVIEWER in Baihe's maintenance assistant. Another model "
        "(a different provider) diagnosed a problem and proposed a code fix. Your job is to "
        "check it, not to rubber-stamp it: read the files the patch touches with the tools, "
        "and look for wrong assumptions, missed callers, edge cases (empty input, None, "
        "another drama's data, concurrent jobs), security problems (keys in logs, paths, "
        "unbounded requests) and whether the patch even applies to the current code. "
        "You can only READ; never claim to have changed anything.\n\n"
        + tools_prompt + "\n\n"
        "Your final answer MUST start with exactly one line: 'VERDICT: AGREES' if the fix is "
        "correct and complete as far as you can check, or 'VERDICT: CONCERNS' otherwise. "
        "Then list each concern with the file and line it's about. Don't propose a "
        "different fix in a diff block; describe what's wrong."
    )


def review_request(question: str, answer: str, patches: list) -> str:
    patch_text = "\n\n".join(f"Patch {p['id']} (files: {', '.join(p['files']) or '?'}):\n{p['patch']}"
                             for p in patches)
    return (f"The user asked:\n{question}\n\n"
            f"The implementing model answered:\n{answer}\n\n"
            f"It proposed:\n{patch_text}\n\n"
            "Review it.")


def parse_verdict(text: str) -> tuple:
    """(verdict, notes). No verdict line -> 'unclear' (never silently
    counted as agreement)."""
    match = _VERDICT_RE.search(text or "")
    if not match:
        return "unclear", (text or "").strip()
    verdict = "agrees" if match.group(1).upper() == "AGREES" else "concerns"
    notes = (text[:match.start()] + text[match.end():]).strip()
    return verdict, notes


def same_backend(implement_engine: str, review_engine: str) -> bool:
    """Cross-provider means a different engine name; the same provider
    with another model name doesn't count (roadmap exit 2)."""
    return (implement_engine or "").lower() == (review_engine or "").lower()
