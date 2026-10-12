"""Tests for services/line_ai_service.py + /api/line-ai (Migration Slice 50).
Fully mocked: fake engine and stubbed LLM helpers, no network."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import line_tools
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import line_ai_service as svc, translate_service
from services.service_errors import (DependencyUnavailableError, NotFoundError,
                                      ServiceError, UnsupportedOperationError)

SECRET = "sk-ant-SECRET1234567890abcdef"


class FakeEngine:
    model = "fake-model"
    supports_reference = True


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())


@pytest.fixture
def client():
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _forbid_writes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("line AI must not write")
    monkeypatch.setattr(db, "save_lines", boom)
    monkeypatch.setattr(db, "record_edit_sample", boom)


def _seed():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="hello"),
                        Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    return did, [r["id"] for r in db.load_lines(did)]


def test_improve_matches_by_id_and_writes_nothing(client, monkeypatch):
    did, ids = _seed()
    _forbid_writes(monkeypatch)
    seen = {}

    def fake(zh, en, engine, issue="", source_language="zh", style_guidelines=""):
        seen.update(zh=zh, en=en, issue=issue, style=style_guidelines)
        return "farewell"
    monkeypatch.setattr(line_tools, "improve_line", fake)
    r = client.post(f"/api/line-ai/dramas/{did}/lines/{ids[1]}/improve", json={"issue": "stiff"})
    assert r.status_code == 200
    assert r.json() == {"line_id": ids[1], "current_en": "bye", "suggestion": "farewell",
                        "changed": True, "engine": "claude", "model": "fake-model"}
    assert (seen["zh"], seen["en"], seen["issue"]) == ("再见", "bye", "stiff")
    assert seen["style"]
    assert [r["en"] for r in db.load_lines(did)] == ["hello", "bye"]


def test_explain(client, monkeypatch):
    did, ids = _seed()
    _forbid_writes(monkeypatch)
    monkeypatch.setattr(line_tools, "explain_translation", lambda zh, en, eng, **k: f"why {zh}")
    r = client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/explain", json={})
    assert r.status_code == 200 and r.json()["explanation"] == "why 你好"


def test_missing_key_is_503(client, monkeypatch):
    did, ids = _seed()
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
    for verb in ("improve", "explain"):
        r = client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/{verb}", json={})
        assert r.status_code == 503


def test_unknown_drama_line_and_bad_input(client):
    did, ids = _seed()
    assert client.post(f"/api/line-ai/dramas/999/lines/{ids[0]}/improve", json={}).status_code == 404
    assert client.post(f"/api/line-ai/dramas/{did}/lines/99999/explain", json={}).status_code == 404
    assert client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/improve",
                       json={"issue": "x" * 501}).status_code == 422
    assert client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/improve",
                       json={"api_key": "x"}).status_code == 422
    assert client.post(f"/api/line-ai/dramas/{did}/lines/{ids[0]}/improve",
                       json={"engine": "nope"}).status_code == 422


def test_engine_failure_is_redacted(monkeypatch):
    did, ids = _seed()

    def boom(*a, **k):
        raise RuntimeError(f"401 bad key {SECRET}")
    monkeypatch.setattr(line_tools, "explain_translation", boom)
    with pytest.raises(ServiceError) as ei:
        svc.explain_line(did, ids[0])
    assert SECRET not in ei.value.message


def test_empty_explanation_and_non_llm_engine(monkeypatch):
    did, ids = _seed()
    monkeypatch.setattr(line_tools, "explain_translation", lambda *a, **k: "")
    with pytest.raises(ServiceError):
        svc.explain_line(did, ids[0])
    class Plain:
        supports_reference = False
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: Plain())
    with pytest.raises(UnsupportedOperationError):
        svc.improve_line(did, ids[0])


def test_untranslated_line_refused():
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="")])
    lid = db.load_lines(did)[0]["id"]
    with pytest.raises(UnsupportedOperationError):
        svc.explain_line(did, lid)


def test_improve_uses_the_shared_style_context_with_emotion(monkeypatch):
    """B-20: improve_line builds its guidelines with the same builder as a
    translate run, so the line's emotion guidance is included."""
    did, ids = _seed()
    db.save_emotions(did, {1: {"emotion": "angry", "intensity": 0.9, "note": "shouting"}})
    seen = {}

    def fake(zh, en, engine, issue="", source_language="zh", style_guidelines=""):
        seen["style"] = style_guidelines
        return en
    monkeypatch.setattr(line_tools, "improve_line", fake)
    svc.improve_line(did, ids[1])
    assert "angry" in seen["style"] and "shouting" in seen["style"]


def test_line_tools_use_the_lines_own_language(monkeypatch):
    did = db.create_drama(title_zh="D", source_language="ja")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="안녕", en="hi", lang="ko"),
                        Line(idx=1, start=1, end=2, zh="やあ", en="yo"),
                        Line(idx=2, start=2, end=3, zh="hello", en="hello", lang="en")])
    ids = [r["id"] for r in db.load_lines(did)]
    seen = []
    monkeypatch.setattr(line_tools, "grammar_breakdown",
                        lambda zh, engine, source_language="zh": seen.append(source_language)
                        or [{"word": "w", "reading": "", "meaning": "m", "function": "f"}])
    for lid in ids:
        svc.grammar_for_line(did, lid)
    assert seen == ["ko", "ja", "en"]


def test_prompt_names_the_language_including_english():
    class Engine:
        supports_reference = True

    prompts = []
    orig = line_tools.call_llm_json
    line_tools.call_llm_json = lambda eng, prompt, **k: prompts.append(prompt) or "[]"
    try:
        line_tools.grammar_breakdown("hello", Engine(), source_language="en")
        line_tools.grammar_breakdown("你好", Engine())
    finally:
        line_tools.call_llm_json = orig
    assert "English" in prompts[0] and "Chinese" in prompts[1]


# --- Engine and model chosen by the caller -------------------------------

from types import SimpleNamespace  # noqa: E402

from engine_backends import llm_tasks  # noqa: E402
from services import translate_run_service  # noqa: E402

VERBS = ("improve", "explain", "alternatives", "grammar")


def _url(did, lid, verb):
    return f"/api/line-ai/dramas/{did}/lines/{lid}/{verb}"


class MeteredEngine:
    """An engine whose request reports tokens, or fails with a key in the text.
    Takes the engine name and model it was built with, so a test can tell
    whether the caller's pick was used."""
    supports_reference = True

    def __init__(self, name="claude", model=None, error=None):
        self.name, self.model = name, model or "default-model"

        def create(**_kw):
            if error:
                raise RuntimeError(error)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="[]")],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        self.client = SimpleNamespace(messages=SimpleNamespace(create=create))


def _usage_rows():
    import contextlib
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM usage_log ORDER BY id")]


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("body", [{"engine": "nope"}, {"model": "../etc/passwd"},
                                  {"model": "bad model name"}, {"model": "x" * 201},
                                  # matches MODEL_ID_RE, so only the ".." rule refuses it
                                  {"model": "a/../b"}])
def test_bad_engine_or_model_is_rejected_before_any_engine_is_built(client, monkeypatch, verb, body):
    did, ids = _seed()
    built = []
    monkeypatch.setattr(translate_engines, "get_engine",
                        lambda *a, **k: built.append(1) or FakeEngine())
    r = client.post(_url(did, ids[0], verb), json=body)
    assert r.status_code in (400, 422)
    assert built == []


@pytest.mark.parametrize("verb", VERBS)
def test_chosen_engine_and_model_reach_the_engine_and_the_answer(client, monkeypatch, verb):
    did, ids = _seed()
    built = []
    monkeypatch.setattr(translate_engines, "get_engine",
                        lambda name, key, model, **k: built.append((name, model)) or FakeEngine())
    monkeypatch.setattr(line_tools, "improve_line", lambda *a, **k: "better")
    monkeypatch.setattr(line_tools, "explain_translation", lambda *a, **k: "why")
    monkeypatch.setattr(line_tools, "alternative_translations", lambda *a, **k: [{"translation": "x"}])
    monkeypatch.setattr(line_tools, "grammar_breakdown", lambda *a, **k: [{"word": "w"}])
    r = client.post(_url(did, ids[0], verb), json={"engine": "deepseek", "model": "deepseek-chat"})
    assert r.status_code == 200 and r.json()["engine"] == "deepseek"
    assert built == [("deepseek", "deepseek-chat")]


@pytest.mark.parametrize("verb", VERBS)
def test_paid_engine_is_refused_once_the_monthly_cap_is_used_up(client, monkeypatch, verb):
    did, ids = _seed()
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 5.0)
    monkeypatch.setattr(db, "get_month_spend", lambda *a, **k: 5.5)
    built = []
    monkeypatch.setattr(translate_engines, "get_engine",
                        lambda *a, **k: built.append(1) or FakeEngine())
    r = client.post(_url(did, ids[0], verb), json={"engine": "deepseek"})
    assert r.status_code == 400 and "spending cap" in r.json()["error"]["message"]
    assert built == []


@pytest.mark.parametrize("verb,op", [("improve", "line_improve"), ("explain", "line_explain")])
def test_the_call_writes_a_usage_row_for_the_chosen_engine_even_when_it_fails(client, monkeypatch, verb, op):
    did, ids = _seed()
    error = []
    # The title's own engine is claude, so a row naming deepseek proves the pick was used.
    monkeypatch.setattr(translate_engines, "get_engine", lambda name, key, model=None, **k: MeteredEngine(
        name, model, error=error[0] if error else None))
    monkeypatch.setattr(line_tools, "improve_line", lambda zh, en, eng, **k: llm_tasks.call_llm_json(
        eng, "p", fallback="[]", expect="json_array") and "better")
    monkeypatch.setattr(line_tools, "explain_translation", lambda zh, en, eng, **k: llm_tasks.call_llm_json(
        eng, "p", fallback="[]", expect="json_array") and "why")
    body = {"engine": "deepseek", "model": "deepseek-chat"}
    assert client.post(_url(did, ids[0], verb), json=body).status_code == 200
    rows = _usage_rows()
    assert [(r["engine"], r["model"], r["operation"], r["drama_id"]) for r in rows] == [
        ("deepseek", "deepseek-chat", op, did)]
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (10, 5)

    error.append(f"401 bad key {SECRET}")
    r = client.post(_url(did, ids[0], verb), json=body)
    assert r.status_code >= 400 and SECRET not in r.text
    assert [(r["engine"], r["model"]) for r in _usage_rows()] == [("deepseek", "deepseek-chat")] * 2


def test_the_pickers_default_is_the_engine_the_service_will_use(client, monkeypatch):
    # No saved engine: the config's line_ai_engine must be what the call resolves, not
    # the translate default.
    did = db.create_drama(title_zh="D")
    cfg = client.get(f"/api/translate-run/dramas/{did}/config").json()
    assert cfg["line_ai_engine"] == svc.tool_engine_name(did)
    db.update_drama(did, translation_engine="deepseek")
    cfg = client.get(f"/api/translate-run/dramas/{did}/config").json()
    assert cfg["line_ai_engine"] == svc.tool_engine_name(did) == "deepseek"
    # A removed engine falls back the way the service does.
    db.update_drama(did, translation_engine="gone")
    cfg = client.get(f"/api/translate-run/dramas/{did}/config").json()
    assert cfg["line_ai_engine"] == svc.tool_engine_name(did)
    assert cfg["line_ai_engine"] in translate_engines.ENGINES


@pytest.mark.parametrize("tag", ["gemma3:12b", "org/name:tag", "claude-sonnet-5-5", "gpt-5.1-mini"])
def test_usual_model_ids_pass_the_model_check(client, monkeypatch, tag):
    did, ids = _seed()
    monkeypatch.setattr(line_tools, "explain_translation", lambda *a, **k: "why")
    r = client.post(_url(did, ids[0], "explain"), json={"engine": "ollama", "model": tag})
    assert r.status_code == 200


def test_every_model_the_picker_offers_passes_the_model_check():
    for entry in translate_service.list_engines():
        for model in entry["models"] or []:
            assert translate_engines.MODEL_ID_RE.fullmatch(model) and ".." not in model, (
                entry["name"], model)


@pytest.mark.parametrize("verb", VERBS)
def test_missing_key_answer_names_no_key_or_path(client, monkeypatch, verb):
    did, ids = _seed()
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
    r = client.post(_url(did, ids[0], verb), json={"engine": "openai"})
    assert r.status_code == 503
    assert "sk-" not in r.text and ".env" not in r.text
