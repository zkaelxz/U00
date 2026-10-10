"""The declared settings schema (lib/settings_schema.py) against the code that
reads and writes settings today.

Retire the coverage guard (TestEveryKeyIsDeclared) and the old/new agreement
test when the settings schema's second PR deletes `_PREFERENCES`: from then on
the schema is the only declaration, and an undeclared key fails at its caller.
"""
import json
import math
import os
import re
import subprocess

import pytest

from lib import settings_schema as schema
from services import settings_service
from services.service_errors import InvalidInputError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Stored under a key that is not a user-facing setting, so it has no row.
STATE_KEYS = {
    "auto_backup.state": "run bookkeeping for auto_backup_service, rewritten on every run",
    "auto_backup.identity": "library identity, copied across a restore by workspace_job_service",
    "jellyfin": "Jellyfin card document; its API key is in .env (BAIHE_JELLYFIN_API_KEY)",
}

# Keys built at run time, matched by (file, argument as written) so a new
# dynamic key in another file has to be added here with its reason.
DYNAMIC_KEYS = {
    ("services/settings_service.py", "_PREF_PREFIX + key"):
        "pref.<name>: every name is a row declared with store_key pref.<name>",
    ("services/settings_service.py", "_PREF_PREFIX + name"):
        "pref.<name>: every name is a row declared with store_key pref.<name>",
    ("services/settings_service.py", "setting.store_key"):
        "the schema reader: the store_key of a declared row",
    ("services/settings_service.py", "key"):
        "helper arguments; the callers pass the literals checked below",
    ("services/settings_service.py", "ENGINE_TEST_GENERATION_PREFIX + engine"):
        "engine_test_gen.<engine>: one counter per engine, created on first key write",
    ("services/settings_service.py", "ENGINE_TEST_PREFIX + engine"):
        "engine_test.<engine>: last Test result per engine",
    ("services/engine_routing_service.py", "ENGINE_TEST_PREFIX + engine"):
        "engine_test.<engine>: last Test result per engine",
    ("services/engine_routing_service.py", "_STORE_PREFIX + capability"):
        "capability.<name>: one engine choice per routing capability in its table",
    ("services/language_pack_service.py", "_TITLE_KEY.format(drama_id)"):
        "title-scoped: the language pack chosen for one title",
    ("services/language_pack_service.py", "_DEFAULT_KEY.format(language)"):
        "per language: the default language pack",
    ("services/benchmark_judge_service.py", "_setting_key(sid)"):
        "benchmark_judge.<session>: blind-judge pass state for one benchmark session",
    ("services/benchmark_judge_service.py", "_setting_key(session_id)"):
        "benchmark_judge.<session>: blind-judge pass state for one benchmark session",
    ("services/line_tools_service.py", "_shorten_pass_key(drama_id)"):
        "title-scoped: the auto-shorten pass for one drama",
    ("services/maintenance_assistant_service.py", "_SETTINGS_PREFIX + key"):
        "assistant.<name>: names are checked against the schema below",
    ("services/assistant_github_service.py", "_SETTINGS_PREFIX + key"):
        "assistant.github.<name>: names are checked against the schema below",
    ("services/model_registry_service.py", "setting_key"):
        "argument; callers pass the model_overrides.* constants, declared in the schema",
    ("engine_backends/engine_registry.py", "setting_key"):
        "argument; callers pass the model_overrides.* constants, declared in the schema",
    ("services/model_reeval_service.py", "key"):
        "argument; callers pass the model_reeval_* constants, declared in the schema",
}


def _tracked(*patterns):
    out = subprocess.run(["git", "ls-files", *patterns], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return sorted(out)


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _first_argument(text, open_paren):
    depth, i = 0, open_paren
    while i < len(text):
        c = text[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                break
        elif c == "," and depth == 1:
            break
        i += 1
    return " ".join(text[open_paren + 1:i].split())


def _module_files():
    return {os.path.splitext(os.path.basename(p))[0]: p for p in _tracked("*.py")
            if not p.startswith("tests/")}


def _resolve_constant(rel, name, modules, seen=()):
    """The string literal a module-level `NAME = ...` ends up as, or None."""
    if (rel, name) in seen:
        return None
    match = re.search(rf"^{re.escape(name)}\s*=\s*(.+)$", _read(rel), re.M)
    if not match:
        return None
    return _resolve_expression(rel, match.group(1).strip(), modules, seen + ((rel, name),))


def _resolve_expression(rel, expr, modules, seen=()):
    literal = re.fullmatch(r'"([^"]*)"', expr)
    if literal:
        return literal.group(1)
    if re.fullmatch(r"[A-Za-z_]\w*", expr):
        return _resolve_constant(rel, expr, modules, seen)
    dotted = re.fullmatch(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)", expr)
    if dotted and dotted.group(1) in modules:
        found = _resolve_constant(modules[dotted.group(1)], dotted.group(2), modules, seen)
        if found is not None:
            return found
        # A front door such as translate_engines re-exports the name from elsewhere.
        for rel_other in modules.values():
            found = _resolve_constant(rel_other, dotted.group(2), modules, seen)
            if found is not None:
                return found
    return None


def _store_keys():
    return {s.store_key for s in schema.SETTINGS if s.store == "db"}


def _app_setting_calls():
    modules = _module_files()
    calls = []
    for rel in _tracked("*.py"):
        if rel.startswith("tests/") or rel == "db.py":
            continue
        text = _read(rel)
        for m in re.finditer(r"\b(?:get|set)_app_setting\(", text):
            calls.append((rel, _first_argument(text, m.end() - 1), modules))
    return calls


class TestEveryKeyIsDeclared:
    def test_every_app_settings_key_has_a_row_or_a_reason(self):
        known = _store_keys() | set(STATE_KEYS)
        used_dynamic = set()
        undeclared = []
        for rel, expr, modules in _app_setting_calls():
            if (rel, expr) in DYNAMIC_KEYS:
                used_dynamic.add((rel, expr))
                continue
            key = _resolve_expression(rel, expr, modules)
            if key is None or key not in known:
                undeclared.append(f"{rel}: {expr} -> {key}")
        assert undeclared == [], (
            "declare the key in lib/settings_schema.py (or give it a reason in this "
            f"test's STATE_KEYS / DYNAMIC_KEYS): {undeclared}")
        assert set(DYNAMIC_KEYS) == used_dynamic, "a DYNAMIC_KEYS entry no longer matches a call"

    def test_state_keys_are_not_also_rows(self):
        assert not set(STATE_KEYS) & _store_keys()

    def test_preference_and_writable_keys_are_rows(self):
        pref_rows = {s.key for s in schema.SETTINGS if s.store_key.startswith("pref.")}
        assert set(settings_service._PREFERENCES) == pref_rows
        for name in settings_service._WRITABLE_SETTINGS:
            assert name in schema.BY_KEY, name
        used = set()
        for rel in _tracked("*.py"):
            if not rel.startswith("tests/"):
                used |= set(re.findall(r'get_preference\("([a-z_]+)"\)', _read(rel)))
        assert used <= pref_rows

    def test_bare_literals_and_constants_are_rows(self):
        keys = _store_keys()
        literals = set()
        for rel in _tracked("services/*.py", "*.py"):
            text = _read(rel)
            literals |= set(re.findall(r'\b_get_bool_setting\("([a-z_.]+)"\)', text))
            literals |= set(re.findall(r'\b_set_app_bool\("([a-z_.]+)"', text))
        assert literals <= keys, literals - keys
        import ollama_unload
        assert settings_service.BULK_AUTO_RESUME_KEY in keys
        assert ollama_unload.SETTING_KEY in keys

    @pytest.mark.parametrize("rel,prefix", [
        ("services/maintenance_assistant_service.py", "assistant."),
        ("services/assistant_github_service.py", "assistant.github."),
    ])
    def test_assistant_keys_are_rows(self, rel, prefix):
        names = set(re.findall(r'\b_get\("([a-z_]+)"', _read(rel)))
        assert names, "the pattern should find the assistant reads"
        assert {prefix + n for n in names} <= _store_keys()

    def test_env_rows_name_the_first_env_variable(self):
        env_rows = [s for s in schema.SETTINGS if s.store == "env"]
        assert {s.key for s in env_rows} == set(settings_service.ENV_NAMES) - {"monthly_cap_usd"}
        for s in env_rows:
            assert s.secret and s.store_key == settings_service.ENV_NAMES[s.key][0]
        assert not any(s.secret for s in schema.SETTINGS if s.store == "db")

    def test_rows_are_well_formed(self):
        assert len({s.key for s in schema.SETTINGS}) == len(schema.SETTINGS)
        assert len({s.store_key for s in schema.SETTINGS}) == len(schema.SETTINGS)
        for s in schema.SETTINGS:
            assert s.type in schema.TYPES and s.scope in schema.SCOPES
            assert s.write in schema.WRITERS
            assert (s.type == "choice") == (s.choices is not None), s.key
            assert s.choices is None or s.choices in settings_service._SCHEMA_CHOICES, s.key
            assert s.dev_only == (s.scope == "developer"), s.key


PROBES = [None, True, False, 0, 1, -1, 1.5, 2.25, 7.26, 99, 100, 500, 20480, 1_048_577, 1e9,
          float("nan"), float("inf"), "", " ", "x", "5", "auto", "claude", " claude", "en-US",
          "fr-FR", "ollama", "paddle", "tesseract", "firefox", "netscape", "a\x00b", "line\nbreak",
          "tab\t", "x" * 1025, "x" * 2001, {}, [], {"a": 1}]


class TestAgreesWithPreferences:
    """The old table and the schema coexist until the second PR; they must
    accept and reject exactly the same values."""

    def test_constants_match(self):
        import memory_headroom
        assert schema.UPLOAD_MB_DEFAULT == settings_service.DEFAULT_UPLOAD_MB
        assert schema.UPLOAD_MB_MIN == settings_service.MIN_UPLOAD_MB
        assert schema.UPLOAD_MB_MAX == settings_service.MAX_UPLOAD_MB
        assert schema._PATH_MAX == settings_service._MAX_PATH_LENGTH
        assert schema._STYLE_NOTE_MAX == settings_service._MAX_STYLE_NOTE_LENGTH
        assert schema._NUM_CTX_MAX == settings_service._MAX_NUM_CTX
        assert schema._MONTHLY_CAP_MAX == settings_service._MAX_MONTHLY_CAP
        assert schema._KEEP_FREE_GB_MAX == memory_headroom.MAX_KEEP_FREE_GB

    @pytest.mark.parametrize("name", sorted(settings_service._PREFERENCES))
    def test_default_and_validation(self, name):
        default, check = settings_service._PREFERENCES[name]
        row = schema.BY_KEY[name]
        assert row.default == default
        choices = (settings_service._SCHEMA_CHOICES[row.choices]() if row.choices else None)
        for probe in PROBES:
            try:
                expected = check(probe)
            except InvalidInputError:
                expected = default
            got = schema.coerce(name, probe, choices)
            assert got == expected and type(got) is type(expected), (name, probe, got, expected)


def _valid_and_bad(row):
    """A stored value that is valid and different from the default, what it
    reads as, and a stored value that must read as the default."""
    if row.type == "bool":
        return (not row.default), (not row.default), "yes"
    if row.type in ("int", "float"):
        valid = row.max if row.clamp else (row.min if row.type == "int" else 2.5)
        return valid, valid, "lots"
    if row.type in ("text", "path"):
        return "value", "value", 123
    if row.type == "choice":
        options = settings_service._SCHEMA_CHOICES[row.choices]()
        pick = next(o for o in options if o != row.default)
        return pick, pick, "not-a-choice"
    return {"a": 1}, {"a": 1}, "not json{"


DB_ROWS = [s for s in schema.SETTINGS if s.store == "db"]


@pytest.mark.parametrize("row", DB_ROWS, ids=[s.key for s in DB_ROWS])
def test_reader_defaults_round_trips_and_survives_bad_rows(isolated_db, row):
    import db
    assert settings_service.get(row.key) == row.default
    valid, expected, bad = _valid_and_bad(row)
    db.set_app_setting(row.store_key, valid)
    assert settings_service.get(row.key) == expected
    db.set_app_setting(row.store_key, bad)
    assert settings_service.get(row.key) == row.default
    db.set_app_setting(row.store_key, None)
    assert settings_service.get(row.key) == row.default


def test_saved_preference_reads_back_through_get(isolated_db):
    settings_service.set_settings({"default_locale": "en-GB", "keep_free_ram_gb": 2.26,
                                   "scene_aware_batches": False, "cookies_browser": None})
    assert settings_service.get("default_locale") == "en-GB"
    assert settings_service.get("keep_free_ram_gb") == 2.3
    assert settings_service.get("scene_aware_batches") is False
    assert settings_service.get("cookies_browser") is None


def test_legacy_toggles_read_back_under_their_old_names(isolated_db):
    settings_service.set_settings({"bulk_auto_resume": True, "use_gpu": True})
    assert settings_service.get("bulk_auto_resume") is True
    assert settings_service.get("use_gpu") is True


def test_json_document_stored_as_text_is_decoded(isolated_db):
    import db
    db.set_app_setting("model_reeval_settings", json.dumps({"interval_days": 7}))
    assert settings_service.get("model_reeval_settings") == {"interval_days": 7}


def test_clamped_rows_pull_into_range(isolated_db):
    import db
    db.set_app_setting("gpu_max_parallel", 99)
    db.set_app_setting("qwen_asr_batch_size", "0")
    assert settings_service.get("gpu_max_parallel") == 4
    assert settings_service.get("qwen_asr_batch_size") == 1


def test_returned_default_cannot_edit_the_declaration(isolated_db):
    settings_service.get("notify_categories")["jobs"] = False
    assert settings_service.get("notify_categories") == {}


def test_secret_rows_are_not_readable_through_get(isolated_db):
    with pytest.raises(ValueError):
        settings_service.get("claude")


def test_unknown_key_is_an_error(isolated_db):
    with pytest.raises(KeyError):
        settings_service.get("no_such_setting")


def test_nan_is_never_a_stored_number():
    assert schema.coerce("monthly_cap_usd", math.nan) is None
