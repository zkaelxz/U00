"""The one-time clearing of saved local Ollama model choices (0.2.0)."""
import contextlib
import json
import sqlite3

import pytest

import applog
from engine_backends import engine_registry
from services import local_model_cleanup_service as svc

LOCAL, HOSTED = "qwen3:14b", "gemma4:31b-cloud"


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, msg, *args):
        self.lines.append(msg % args)

    warning = info


@pytest.fixture
def log(monkeypatch):
    sink = _Log()
    monkeypatch.setattr(applog, "get_logger", lambda: sink)
    return sink


def _preset_model(db, pid):
    return next(p for p in db.list_presets() if p["id"] == pid)["engine_model"]


def _start(isolated_db, did):
    return json.loads(isolated_db.get_drama(did)["translate_start_json"] or "{}")


def _queue(items):
    return {"version": 1, "state": "idle", "next_id": 10, "items": items}


def _item(item_id, did, step, settings):
    return {"id": item_id, "drama_id": did, "step": step, "status": "waiting", "settings": settings}


def _seed_everything(isolated_db, model):
    db = isolated_db
    ids = {"preset": db.save_preset("Ollama one", translation_engine="ollama", engine_model=model),
           "claude_preset": db.save_preset("Claude one", translation_engine="claude",
                                           engine_model="claude-sonnet-5-5"),
           "title": db.create_drama(title_en="Ollama title", translation_engine="ollama"),
           "claude_title": db.create_drama(title_en="Claude title", translation_engine="claude")}
    db.update_drama(ids["title"], translate_start_json=json.dumps({"engine_model": model, "batch_size": 30}))
    # Not an Ollama title, so a tag-shaped model is another engine's and stays.
    db.update_drama(ids["claude_title"], translate_start_json=json.dumps({"engine_model": "qwen3:14b"}))
    db.set_app_setting(engine_registry.MODEL_OVERRIDE_DEFAULTS_KEY, {"ollama": model, "claude": "claude-opus-5-5"})
    db.set_app_setting(engine_registry.MODEL_OVERRIDE_TIERS_KEY, {"standard": "claude-opus-5-5"})
    db.set_app_setting("extension_translation_engine", {"engine": "ollama", "model": model})
    db.set_app_setting("job_queue", _queue([
        _item(1, ids["title"], "translate", {"model": model, "batch_size": 20}),
        _item(2, ids["claude_title"], "translate", {"model": "claude-sonnet-5-5"}),
        _item(3, ids["title"], "names", {"engine": "ollama", "model": model, "source": "lines"}),
        _item(4, ids["title"], "lines", {"engine": "ollama", "model": model, "chapter_from": 1}),
        _item(5, ids["title"], "run_translate", {"engine": "ollama", "model": model, "capped": True}),
    ]))
    db.set_app_setting("model_reeval_production", json.dumps(
        {"translation": {"engine": "ollama", "model": model, "promoted_at": "2026-01-01"}}))
    db.set_app_setting("assistant.model", model)   # no engine saved: the assistant runs on Ollama
    db.set_app_setting("assistant.review_engine", "ollama")
    db.set_app_setting("assistant.review_model", model)
    return ids


def test_every_saved_local_model_is_cleared_once(isolated_db, log):
    db = isolated_db
    ids = _seed_everything(db, LOCAL)

    changed = svc.run_once()

    assert changed == {"presets": 1, "title start values": 1, "model_overrides.defaults": 1,
                       "extension_translation_engine": 1, "job_queue": 4,
                       "model_reeval_production": 1, "assistant": 2}
    assert _preset_model(db, ids["preset"]) is None
    assert _preset_model(db, ids["claude_preset"]) == "claude-sonnet-5-5"
    assert _start(db, ids["title"]) == {"batch_size": 30}
    assert _start(db, ids["claude_title"]) == {"engine_model": "qwen3:14b"}
    assert db.get_app_setting(engine_registry.MODEL_OVERRIDE_DEFAULTS_KEY) == {"claude": "claude-opus-5-5"}
    assert db.get_app_setting(engine_registry.MODEL_OVERRIDE_TIERS_KEY) == {"standard": "claude-opus-5-5"}
    assert db.get_app_setting("extension_translation_engine") == {"engine": "ollama", "model": None}
    settings = [i["settings"] for i in db.get_app_setting("job_queue")["items"]]
    assert settings == [{"batch_size": 20}, {"model": "claude-sonnet-5-5"},
                        {"engine": "ollama", "source": "lines"}, {"engine": "ollama", "chapter_from": 1},
                        {"engine": "ollama", "capped": True}]
    assert json.loads(db.get_app_setting("model_reeval_production")) == {}
    assert db.get_app_setting("assistant.model") is None
    assert db.get_app_setting("assistant.review_model") is None
    assert log.lines == ["Cleared 11 saved local Ollama model choice(s); they now follow the default "
                         "Ollama model (gemma4:31b-cloud): assistant 2, extension_translation_engine 1, "
                         "job_queue 4, model_overrides.defaults 1, model_reeval_production 1, presets 1, "
                         "title start values 1"]
    assert LOCAL not in log.lines[0]

    # Idempotent: a local model saved after the pass is the owner's new choice and stays.
    db.set_app_setting("extension_translation_engine", {"engine": "ollama", "model": LOCAL})
    assert svc.run_once() == {}
    assert db.get_app_setting("extension_translation_engine")["model"] == LOCAL
    assert len(log.lines) == 1


def test_hosted_models_are_left_alone(isolated_db, log):
    db = isolated_db
    ids = _seed_everything(db, HOSTED)
    before = {k: db.get_app_setting(k) for k in (
        engine_registry.MODEL_OVERRIDE_DEFAULTS_KEY, "extension_translation_engine", "job_queue",
        "model_reeval_production", "assistant.model", "assistant.review_model")}

    assert svc.run_once() == {}

    assert _preset_model(db, ids["preset"]) == HOSTED
    assert _start(db, ids["title"]) == {"engine_model": HOSTED, "batch_size": 30}
    assert {k: db.get_app_setting(k) for k in before} == before
    assert log.lines == ["No saved local Ollama model choices to clear."]
    assert db.get_app_setting(svc.MARKER) is True


def test_a_title_with_no_engine_follows_the_routed_default(isolated_db, log, monkeypatch):
    from services import engine_routing_service
    monkeypatch.setattr(engine_routing_service, "resolve_capability", lambda cap: "ollama")
    did = isolated_db.create_drama(title_en="Unset")
    isolated_db.update_drama(did, translation_engine=None, translate_start_json=json.dumps({"engine_model": LOCAL}))

    assert svc.run_once() == {"title start values": 1}
    assert _start(isolated_db, did) == {}


def test_an_empty_library_only_records_the_pass(isolated_db, log):
    assert svc.run_once() == {}
    assert isolated_db.get_app_setting(svc.MARKER) is True
    assert svc.run_once() == {}


class _Conn:
    """Passes everything to a real connection, recording statements and failing the one named."""

    def __init__(self, conn, statements, fail_on=None):
        self._conn, self._statements, self._fail_on = conn, statements, fail_on

    def execute(self, sql, *args):
        self._statements.append(sql)
        if sql == self._fail_on:
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


def _wrap_conns(monkeypatch, fail_on=None):
    statements = []
    real = svc.db.get_conn
    monkeypatch.setattr(svc.db, "get_conn", lambda: _Conn(real(), statements, fail_on))
    return statements


@pytest.mark.parametrize("fail_on", ["BEGIN IMMEDIATE", "COMMIT"])
def test_a_locked_or_unwritable_library_does_not_stop_startup_and_runs_again(
        isolated_db, log, monkeypatch, fail_on):
    db = isolated_db
    pid = db.save_preset("Ollama one", translation_engine="ollama", engine_model=LOCAL)
    _wrap_conns(monkeypatch, fail_on)

    assert svc.run_once() == {}
    assert _preset_model(db, pid) == LOCAL
    assert db.get_app_setting(svc.MARKER) is None
    assert log.lines == ["Saved local Ollama models not cleared: the library could not be written; "
                         "trying again next start."]

    monkeypatch.undo()
    monkeypatch.setattr(applog, "get_logger", lambda: log)
    assert svc.run_once() == {"presets": 1}


def test_a_database_error_in_a_step_changes_nothing_and_runs_again(isolated_db, log, monkeypatch):
    db = isolated_db
    db.set_app_setting("extension_translation_engine", {"engine": "ollama", "model": LOCAL})
    pid = db.save_preset("Ollama one", translation_engine="ollama", engine_model=LOCAL)

    def boom(conn):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(svc, "_clear_queue", lambda conn, engines: boom(conn))
    assert svc.run_once() == {}
    assert _preset_model(db, pid) == LOCAL
    assert db.get_app_setting(svc.MARKER) is None

    monkeypatch.undo()
    monkeypatch.setattr(applog, "get_logger", lambda: log)
    assert svc.run_once() == {"presets": 1, "extension_translation_engine": 1}


def test_a_start_after_the_pass_takes_no_write_lock(isolated_db, log, monkeypatch):
    assert svc.run_once() == {}
    statements = _wrap_conns(monkeypatch)

    assert svc.run_once() == {}
    assert statements and not any(s.startswith("BEGIN") for s in statements)


def test_out_of_range_ids_and_deep_json_skip_only_their_storage(isolated_db, log):
    db = isolated_db
    did = db.create_drama(title_en="Ollama title", translation_engine="ollama")
    pid = db.save_preset("Ollama one", translation_engine="ollama", engine_model=LOCAL)
    queue = json.dumps(_queue([_item(1, did, "translate", {"model": LOCAL})]))
    with contextlib.closing(db.get_conn()) as conn:
        # Hand-edited values: json reads 1e999 as inf, and int(inf) raises OverflowError.
        conn.executemany("INSERT INTO app_settings (key, value) VALUES (?, ?)", [
            ("job_queue", queue.replace(f'"drama_id": {did}', '"drama_id": 1e999')),
            ("extension_translation_engine", "[" * 100000 + "]" * 100000)])
        conn.commit()

    assert svc.run_once() == {"presets": 1}
    assert _preset_model(db, pid) is None
    assert db.get_app_setting(svc.MARKER) is True
    assert log.lines[0] == ("Saved local Ollama models not cleared in unreadable settings: "
                            "extension_translation_engine, job_queue")
    assert "1e999" not in " ".join(log.lines)


def test_queue_items_with_odd_title_ids_are_skipped_or_matched(isolated_db, log):
    did = isolated_db.create_drama(title_en="Ollama title", translation_engine="ollama")
    isolated_db.set_app_setting("job_queue", _queue([
        _item(1, [did], "translate", {"model": LOCAL}),
        _item(2, {"id": did}, "translate", {"model": LOCAL}),
        _item(3, str(did), "translate", {"model": LOCAL}),
    ]))

    assert svc.run_once() == {"job_queue": 1}
    assert [i["settings"] for i in isolated_db.get_app_setting("job_queue")["items"]] == [
        {"model": LOCAL}, {"model": LOCAL}, {}]


def test_a_malformed_setting_is_skipped_and_the_rest_still_cleared(isolated_db, log, monkeypatch):
    db = isolated_db
    db.set_app_setting("extension_translation_engine", {"engine": "ollama", "model": LOCAL})
    monkeypatch.setattr(svc, "_clear_queue", lambda conn, engines: [][1])

    assert svc.run_once() == {"extension_translation_engine": 1}
    assert db.get_app_setting(svc.MARKER) is True
    assert log.lines[0] == "Saved local Ollama models not cleared in unreadable settings: job_queue"


def test_a_cleared_production_model_is_the_default_candidates_compare_with(isolated_db, log):
    from lib.errors import InvalidInputError
    from services import model_reeval_service, settings_service
    settings_service.set_settings({"default_engine": "ollama"})
    isolated_db.set_app_setting("model_reeval_production", json.dumps(
        {"translation": {"engine": "ollama", "model": LOCAL, "promoted_at": "2026-01-01"}}))

    assert svc.run_once() == {"model_reeval_production": 1}

    default = engine_registry.effective_default_model("ollama")
    assert model_reeval_service.get_production("translation") == {
        "engine": "ollama", "model": default, "source": "settings"}
    with pytest.raises(InvalidInputError, match="current production model"):
        model_reeval_service._add_candidate_locked("translation", {"engine": "ollama"}, default, "")
