"""The transcription advisor: one table of notes, read by the form, the Jobs details and
the "Do not combine" table in docs/asr-experiments.md."""
import os
import re

import pytest

from services import transcribe_options_service as tos

_BASE = dict(language="ja", media_type="audio_drama", backend="whisper", model="large-v3",
             switches={}, gpu_on=True, packages={"faster_whisper": True}, token=True)


def _advice(switches=None, **over):
    args = {**_BASE, **over}
    args["switches"] = {**(switches or {})}
    return tos.asr_advice(**args)


def _ids(notes):
    return [n["id"] for n in notes]


@pytest.mark.parametrize("over, switches, rule, severity, fix", [
    (dict(backend="qwen3_asr_long"), {}, "qwen_long_audio", "warn", {"asr_backend_choice": "whisper"}),
    (dict(backend="qwen3_asr_vad"), {}, "qwen_long_audio", "warn", {"asr_backend_choice": "whisper"}),
    (dict(model="ja-1.5b", gpu_on=False), {}, "ja_15b_needs_gpu", "warn", {"whisper_size": "large-v3"}),
    (dict(model="ja-1.5b"), {}, "ja_15b_scope", "tip", None),
    (dict(backend="anime_whisper"), {}, "anime_whisper_scope", "tip", None),
    (dict(backend="anime_whisper", language="ko"), {}, "anime_whisper_language", "blocked", {"asr_backend_choice": "whisper"}),
    (dict(model="kotoba-v2", language="zh"), {}, "ja_model_language", "blocked", {"whisper_size": "large-v3"}),
    (dict(model="medium"), {}, "weak_whisper_size", "warn", {"whisper_size": "large-v3"}),
    (dict(model="small"), {}, "weak_whisper_size", "warn", {"whisper_size": "large-v3"}),
    (dict(), {"separate_vocals_first": True}, "separate_vocals", "warn", {"separate_vocals_first": False}),
    (dict(), {"whisper_fast_mode": True}, "fast_mode", "warn", {"whisper_fast_mode": False}),
    (dict(), {"alignment_method": "qwen3_forced_align"}, "forced_align_needs_transcript", "warn", None),
    (dict(token=False), {"use_groq": True}, "groq_no_key", "blocked", {"use_groq": False}),
    (dict(packages={"faster_whisper": False}), {}, "whisper_missing", "blocked", None),
])
def test_rule_fires_with_its_severity_and_fix(over, switches, rule, severity, fix):
    notes = _advice(switches, **over)
    note = next(n for n in notes if n["id"] == rule)
    assert note["severity"] == severity and note["fix"] == fix


@pytest.mark.parametrize("over, switches, absent", [
    (dict(), {"alignment_method": "qwen3_forced_align", "has_transcript": True}, "forced_align_needs_transcript"),
    (dict(backend="qwen3_asr"), {}, "qwen_long_audio"),
    (dict(backend="qwen3_asr_long"), {"whisper_fast_mode": True}, "fast_mode"),
    (dict(), {}, "separate_vocals"),
    (dict(model="ja-1.5b", gpu_on=None), {}, "ja_15b_needs_gpu"),
    (dict(token=None), {"use_groq": True}, "groq_no_key"),
    (dict(packages=None, token=None), {}, "whisper_missing"),
])
def test_rule_stays_quiet(over, switches, absent):
    assert absent not in _ids(_advice(switches, **over))


def test_groq_without_whisper_installed_is_not_offered_the_switch_off_fix():
    notes = _advice({"use_groq": True}, token=False, packages={"faster_whisper": False})
    assert next(n for n in notes if n["id"] == "groq_no_key")["fix"] is None


def test_groq_on_hides_the_whisper_missing_note():
    assert "whisper_missing" not in _ids(_advice({"use_groq": True}, packages={"faster_whisper": False}))


def test_notes_are_ordered_blocked_then_warn_then_tip():
    notes = _advice({"separate_vocals_first": True, "use_groq": True}, backend="qwen3_asr_long",
                    model="ja-1.5b", token=False)
    order = [n["severity"] for n in notes]
    assert order == sorted(order, key={"blocked": 0, "warn": 1, "tip": 2}.get)


def test_the_recommended_setup_is_the_only_tip_when_nothing_applies():
    assert _ids(_advice()) == ["recommended"]
    assert _advice()[0]["fix"] is None


def test_no_note_says_to_turn_vocal_separation_on():
    texts = " ".join(n["text"].lower() for r in tos._ADVICE_RULES for n in [{"text": r[3]}])
    assert "turn on" not in texts and "music-backed" not in texts


def test_every_fix_names_real_config_fields():
    fields = {"whisper_size", "asr_backend_choice", "separate_vocals_first", "whisper_fast_mode", "use_groq"}
    for note in (n for args in [
        dict(backend="qwen3_asr_long"), dict(model="medium"), dict(backend="anime_whisper", language="ko"),
        dict(model="ja-1.5b", gpu_on=False), dict(model="kotoba-v2", language="zh"),
    ] for n in _advice(**args)):
        assert set(note["fix"] or {}) <= fields


def test_the_route_returns_notes_and_skips_the_environment_for_a_finished_run(isolated_db):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    client = TestClient(create_app(ApiSettings()), raise_server_exceptions=False, headers={"X-Baihe-Local": "1"})
    body = {"language": "ja", "asr_backend": "qwen3_asr_long", "whisper_size": "large-v3", "use_groq": True,
            "check_environment": False}
    resp = client.post("/api/transcribe/asr-advice", json=body)
    assert resp.status_code == 200
    ids = [n["id"] for n in resp.json()["notes"]]
    assert "qwen_long_audio" in ids and "groq_no_key" not in ids


_DOC = os.path.join(os.path.dirname(__file__), "..", "docs", "asr-experiments.md")


def _doc_rows():
    text = open(_DOC, encoding="utf-8").read()
    section = text.split("## Do not combine", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^\| `([a-z0-9_]+)` \| (\w+) \| (.+?) \|$", section, re.M)


def test_the_do_not_combine_table_in_the_docs_matches_the_rules():
    rows = _doc_rows()
    expected = [(r[0], r[1], r[3].replace("|", "/")) for r in tos._ADVICE_RULES]
    assert rows == expected, "update the Do not combine table in docs/asr-experiments.md to match _ADVICE_RULES"
