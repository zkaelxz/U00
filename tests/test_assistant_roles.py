"""Step 60: the maintenance assistant's implement -> independent review
roles (services/assistant_roles_service.py and the review path in
services/maintenance_assistant_service.ask). Engines are fakes that
record which one answered: no network, no real model or key."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import action_tiers
from api.api_config import ApiSettings
from api.server import create_app
from services import assistant_roles_service as roles, maintenance_assistant_service as svc

PATCH = ("The fix:\n```diff\n--- a/services/dub_service.py\n+++ b/services/dub_service.py\n"
         "@@ -10,1 +10,1 @@\n-    if not line.speaker:\n+    if not line.speaker and not default_voice:\n```")


class FakeEngine:
    def __init__(self, name):
        self.name = name


class EngineChat:
    """Replies per engine; records (engine name, system prompt) per call."""

    def __init__(self, **replies):
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls = []

    def __call__(self, system_prompt, messages, engine):
        self.calls.append((engine.name, system_prompt, messages))
        return self.replies[engine.name].pop(0)


@pytest.fixture
def engines(isolated_db, monkeypatch):
    svc.set_settings({"developer_mode": True, "roles_enabled": True, "engine": "claude",
                      "review_engine": "gemini"})
    monkeypatch.setattr(svc, "build_engine",
                        lambda name=None, model=None: (FakeEngine("claude"), "claude", None))

    def review_engine():
        s = svc.get_settings()
        return FakeEngine(s["review_engine"]), s["review_engine"], s["review_model"]

    monkeypatch.setattr(svc, "build_review_engine", review_engine)


def test_flagged_fix_is_shown_with_both_perspectives(engines):
    chat = EngineChat(
        claude=[PATCH],
        gemini=['TOOL: {"id": "r1", "name": "search_code", "args": {"query": "default_voice"}}',
                "VERDICT: CONCERNS\n`default_voice` isn't defined in dub_service.py (search found "
                "no match), so this patch raises NameError."],
    )
    out = svc.ask("Why does dub skip lines?", chat=chat)
    # The implement role's fix is still there, unchanged...
    assert out["proposed_patches"][0]["files"] == ["services/dub_service.py"]
    assert "default_voice" in out["proposed_patches"][0]["patch"]
    assert out["answer"].startswith("The fix:")
    # ...next to the reviewer's concern, neither silently resolved.
    review = out["review"]
    assert review["verdict"] == "concerns" and review["engine"] == "gemini"
    assert "NameError" in review["notes"] and "VERDICT" not in review["notes"]
    assert review["tool_calls"][0]["name"] == "search_code"
    # The reviewer saw the implementer's patch and used the reviewer role prompt.
    reviewer_calls = [c for c in chat.calls if c[0] == "gemini"]
    assert "REVIEWER" in reviewer_calls[0][1] and "default_voice" in reviewer_calls[0][2][0]["content"]


def test_review_really_uses_a_different_backend(engines):
    chat = EngineChat(claude=[PATCH], gemini=["VERDICT: AGREES\nLooks right."])
    out = svc.ask("x", chat=chat)
    assert [c[0] for c in chat.calls] == ["claude", "gemini"]
    assert out["engine"] == "claude" and out["review"]["engine"] == "gemini"
    assert out["review"]["verdict"] == "agrees"


def test_same_engine_is_never_used_as_its_own_reviewer(engines):
    svc.set_settings({"review_engine": "claude", "review_model": "another-model"})
    chat = EngineChat(claude=[PATCH])
    out = svc.ask("x", chat=chat)
    assert len(chat.calls) == 1  # no second call to the same provider
    assert out["review"]["verdict"] == "unavailable" and "same" in out["review"]["notes"]
    assert out["proposed_patches"]  # the fix is still shown


def test_missing_review_engine_is_reported_not_hidden(engines, monkeypatch):
    svc.set_settings({"review_engine": None})
    monkeypatch.undo()  # real build_review_engine; build_engine patched again below
    monkeypatch.setattr(svc, "build_engine",
                        lambda name=None, model=None: (FakeEngine("claude"), "claude", None))
    out = svc.ask("x", chat=EngineChat(claude=[PATCH]))
    assert out["review"]["verdict"] == "unavailable" and "review engine" in out["review"]["notes"]


def test_no_review_without_a_patch_or_when_off(engines):
    chat = EngineChat(claude=["Nothing to fix; the log shows a network timeout."])
    assert svc.ask("x", chat=chat)["review"] is None
    svc.set_settings({"roles_enabled": False})
    chat = EngineChat(claude=[PATCH])
    assert svc.ask("x", chat=chat)["review"] is None and len(chat.calls) == 1


def test_roles_off_by_default(isolated_db):
    s = svc.get_settings()
    assert s["roles_enabled"] is False and s["review_engine"] is None


def test_missing_verdict_is_unclear_not_agreement():
    assert roles.parse_verdict("Seems fine I guess")[0] == "unclear"
    assert roles.parse_verdict("verdict: agrees\nok") == ("agrees", "ok")
    assert roles.same_backend("Claude", "claude") and not roles.same_backend("claude", "ollama")


def test_reviewer_has_only_the_read_only_tools():
    prompt = roles.review_system_prompt(svc.tools_prompt())
    for name in svc.READ_ONLY_TOOLS:
        assert name in prompt
    assert svc.list_tools()["write_tools"] == []


def test_roles_module_is_protected():
    assert action_tiers.classify_action(
        "modify_prompt", touches_paths=["services/assistant_roles_service.py"]) \
        is action_tiers.ActionTier.RED


def test_api_settings_and_answer_carry_the_review(engines, monkeypatch):
    c = TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                   client=("127.0.0.1", 50000), raise_server_exceptions=False)
    r = c.post("/api/assistant/settings", json={"roles_enabled": True, "review_engine": "ollama"})
    assert r.status_code == 200 and r.json()["review_engine"] == "ollama"
    monkeypatch.setattr(svc, "_chat", EngineChat(claude=[PATCH], ollama=["VERDICT: AGREES\nfine"]))
    r = c.post("/api/assistant/ask", json={"question": "x"})
    assert r.status_code == 200 and r.json()["review"]["verdict"] == "agrees"
    assert c.post("/api/assistant/settings", json={"review_engine": "deepl"}).status_code == 422
