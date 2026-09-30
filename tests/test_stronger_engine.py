"""Step 99: suggest a stronger engine for a hard line (suggest only, never
switch or write). Mocked throughout: no network, no real keys."""

import pytest

import db
import translate_engines
from core import Line
from services import engine_routing_service as routing
from services import settings_service
from services import stronger_engine_service as svc
from services.service_errors import (DependencyUnavailableError, NotFoundError, ServiceError,
                                      UnsupportedOperationError)


class FakeEngine:
    name = "claude"
    model = "claude-sonnet-4-6"
    supports_reference = True
    free_tier = False

    def __init__(self, answer=("Lin Wan smiled.",), boom=None):
        self.answer, self.boom, self.contexts = list(answer), boom, []
        self.last_usage = {"input_tokens": 1000, "output_tokens": 100}

    def translate_batch(self, zh_lines, context):
        self.contexts.append((list(zh_lines), context))
        if self.boom:
            raise self.boom
        return list(self.answer)


@pytest.fixture
def drama(isolated_db):
    sid = db.create_series("S")
    did = db.create_drama(title_zh="D", translation_engine="ollama", series_id=sid)
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello"),
        Line(idx=1, start=1.0, end=2.0, zh="林婉笑了", en="She smiled.", flag="uncertain_translation"),
        Line(idx=2, start=2.0, end=3.0, zh="林婉走了", en="Lin Wan left."),
        Line(idx=3, start=3.0, end=4.0, zh="师姐来了", en="Senior sister came."),
    ])
    db.upsert_glossary_term(sid, "林婉", "Lin Wan")
    ids = [ln.id for ln in db.load_line_objects(did)]
    settings_service.set_settings({"default_engine": "ollama"})
    routing.set_capability_engine("translation.high_quality", "claude")
    return did, ids, sid


def _glossary(terms):
    return [{"term_original": o, "term_translation": t, "aliases": ""} for o, t in terms]


class TestReasons:
    def test_qc_flag(self):
        ln = Line(idx=0, start=0, end=1, zh="你好", en="Hi", flag="slang_idiom")
        assert svc.line_reasons(ln, []) == ["qc_flag"]

    def test_blocked_lines_have_their_own_retry(self):
        ln = Line(idx=0, start=0, end=1, zh="你好", en="Hi", flag="content_blocked")
        assert svc.line_reasons(ln, []) == []

    def test_glossary_conflict(self):
        g = _glossary([("林婉", "Lin Wan")])
        assert svc.line_reasons(Line(idx=0, start=0, end=1, zh="林婉笑了", en="She smiled."),
                                g) == ["glossary_conflict"]
        assert svc.line_reasons(Line(idx=0, start=0, end=1, zh="林婉笑了", en="lin wan smiled."),
                                g) == []

    @pytest.mark.parametrize("flag", ["ambiguous_reference", "name_uncertain"])
    def test_ambiguous_term(self, flag):
        assert svc.line_reasons(
            Line(idx=0, start=0, end=1, zh="师姐来了", en="She came.", flag=flag),
            []) == ["ambiguous_term"]

    def test_untranslated_line_is_not_suggested(self):
        assert svc.line_reasons(Line(idx=0, start=0, end=1, zh="你好", en="", flag="x"), []) == []


class TestSuggestions:
    def test_lists_hard_lines_with_an_estimate(self, drama):
        did, ids, _ = drama
        out = svc.get_suggestions(did)
        assert out["engine"] == "claude" and out["current_engine"] == "ollama"
        assert out["available"] is True
        by_id = {s["line_id"]: s for s in out["lines"]}
        assert by_id[ids[1]]["reasons"][0] == "qc_flag"
        assert ids[0] not in by_id
        assert by_id[ids[1]]["estimate_usd"] > 0

    def test_nothing_when_the_stronger_engine_is_already_used(self, drama):
        did, _, _ = drama
        routing.set_capability_engine("translation.high_quality", "ollama")
        out = svc.get_suggestions(did)
        assert out["available"] is False and out["lines"] == []

    def test_nothing_until_a_stronger_engine_is_picked(self, drama):
        did, _, _ = drama
        routing.set_capability_engine("translation.high_quality", None)
        settings_service.set_settings({"default_engine": "claude"})  # unset default != ollama
        out = svc.get_suggestions(did)
        assert out["available"] is False and out["lines"] == []

    def test_glossary_conflict_from_real_rows(self, drama):
        did, ids, _ = drama
        by_id = {s["line_id"]: s["reasons"] for s in svc.get_suggestions(did)["lines"]}
        assert "glossary_conflict" in by_id[ids[1]]
        assert ids[2] not in by_id  # "Lin Wan left." uses the term

    def test_estimate_counts_the_whole_prompt(self, drama):
        did, ids, _ = drama
        est = {s["line_id"]: s["estimate_usd"] for s in svc.get_suggestions(did)["lines"]}[ids[1]]
        bare = svc._estimate("claude", "林婉笑了", 0)
        assert est > bare * 3

    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            svc.get_suggestions(999)


class TestTryLine:
    @pytest.fixture
    def engine(self, monkeypatch):
        fake = FakeEngine()
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: fake)
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: "sk-test-123")
        return fake

    def test_returns_a_suggestion_and_writes_nothing(self, drama, engine):
        did, ids, _ = drama
        before = [(ln.id, ln.en, ln.flag) for ln in db.load_line_objects(did)]
        out = svc.try_line(did, ids[1], "claude")
        assert out["text"] == "Lin Wan smiled." and out["engine"] == "claude"
        assert out["based_on_en"] == "She smiled."
        assert [(ln.id, ln.en, ln.flag) for ln in db.load_line_objects(did)] == before
        zh, ctx = engine.contexts[0]
        assert zh == ["林婉笑了"] and ctx["line_ids"] == [ids[1]]
        assert ctx["recent_context"] == [("你好", "Hello")]
        assert "Lin Wan" in repr(ctx["glossary_terms"]) and ctx["style_guidelines"]

    def test_character_hints_reach_the_prompt(self, drama, engine, monkeypatch):
        did, ids, _ = drama
        from services import workspace_job_service
        monkeypatch.setattr(workspace_job_service, "build_run_style_context",
                            lambda *a, **k: (None, "Xiaoling (she/her)", {}))
        svc.try_line(did, ids[1], "claude")
        assert "Xiaoling (she/her)" in engine.contexts[0][1]["style_guidelines"]

    def test_a_changed_engine_is_refused(self, drama, engine):
        did, ids, _ = drama
        from services.service_errors import ConflictError
        with pytest.raises(ConflictError):
            svc.try_line(did, ids[1], "gemini")
        assert engine.contexts == []

    def test_spend_is_logged(self, drama, engine):
        did, ids, _ = drama
        before = db.get_month_spend()
        out = svc.try_line(did, ids[1], "claude")
        assert out["cost_usd"] > 0
        assert db.get_month_spend() == pytest.approx(before + out["cost_usd"])

    def test_more_than_one_answer_is_refused(self, drama, engine):
        did, ids, _ = drama
        engine.answer = ["a", "b"]
        before = db.get_month_spend()
        with pytest.raises(ServiceError):
            svc.try_line(did, ids[1], "claude")
        assert db.get_month_spend() > before  # a rejected answer was still billed

    def test_a_failed_call_still_logs_its_spend(self, drama, engine):
        did, ids, _ = drama
        engine.boom = TimeoutError("slow")
        before = db.get_month_spend()
        with pytest.raises(ServiceError):
            svc.try_line(did, ids[1], "claude")
        assert db.get_month_spend() > before

    def test_engine_error_is_fixed_text(self, drama, engine):
        did, ids, _ = drama
        engine.boom = RuntimeError("401 bad key sk-ant-api03-" + "B" * 40)
        with pytest.raises(ServiceError) as ei:
            svc.try_line(did, ids[1], "claude")
        assert "sk-ant" not in str(ei.value)

    def test_refused_when_the_monthly_cap_is_used_up(self, drama, engine):
        did, ids, _ = drama
        settings_service.set_settings({"monthly_cap_usd": 1.0})
        db.log_usage(did, "claude", "m", "translate", 0, 0, 1.5)
        with pytest.raises(UnsupportedOperationError):
            svc.try_line(did, ids[1], "claude")
        assert engine.contexts == []

    def test_refused_when_the_estimate_passes_the_cap(self, drama, engine, monkeypatch):
        did, ids, _ = drama
        settings_service.set_settings({"monthly_cap_usd": 1.0})
        db.log_usage(did, "claude", "m", "translate", 0, 0, 0.99)
        monkeypatch.setattr(svc, "_estimate", lambda *a: 0.05)
        with pytest.raises(UnsupportedOperationError):
            svc.try_line(did, ids[1], "claude")

    def test_no_key(self, drama, monkeypatch):
        did, ids, _ = drama
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: None)
        with pytest.raises(DependencyUnavailableError):
            svc.try_line(did, ids[1], "claude")

    def test_same_engine_is_refused(self, drama, engine):
        did, ids, _ = drama
        routing.set_capability_engine("translation.high_quality", "ollama")
        with pytest.raises(UnsupportedOperationError):
            svc.try_line(did, ids[1], "claude")


class TestRoutes:
    @pytest.fixture
    def client(self, drama):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        from fastapi.testclient import TestClient
        from api.api_config import ApiSettings
        from api.server import create_app
        return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)

    def test_get_and_try(self, client, drama, monkeypatch):
        did, ids, _ = drama
        body = client.get(f"/api/stronger-engine/dramas/{did}").json()
        assert body["engine"] == "claude" and body["lines"]
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
        monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: "sk-test-123")
        r = client.post(f"/api/stronger-engine/dramas/{did}/lines/{ids[1]}/try", json={})
        assert r.status_code == 200 and r.json()["text"] == "Lin Wan smiled."

    def test_try_takes_no_engine_from_the_caller(self, client, drama):
        did, ids, _ = drama
        r = client.post(f"/api/stronger-engine/dramas/{did}/lines/{ids[1]}/try",
                        json={"engine": "claude"})
        assert r.status_code == 422
