"""Scanlate's page-with-picture translation: what is sent, how the answer is applied, and the
fallback to the text-only request. The provider call is a double."""

import io
import json
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

import db
import scanlate_translate
from engine_backends import vision
from services import scanlate_run_service as run_svc
from tests.saved_settings import patch_setting


class _Engine:
    supports_reference = True
    client = object()
    name = "claude"
    model = "claude-sonnet-5-5"


def _region(text, x=10, y=20, w=100, h=40, **kw):
    return {"x": x, "y": y, "w": w, "h": h, "source_text": text, "translated_text": "", "skip": False,
            "kind": "bubble", **kw}


def _answer(translations, summary="Two people argue."):
    return json.dumps({"translations": translations, "context_summary": summary}), \
        {"input_tokens": 3000, "output_tokens": 200}


def test_the_picture_goes_out_with_the_regions_and_the_answer_is_matched_by_id(monkeypatch):
    sent = {}

    def ask(engine, prompt, image, mime, max_tokens=0, on_done=None):
        sent.update(prompt=prompt, image=image, mime=mime, max_tokens=max_tokens)
        # Reversed on purpose: position must not matter.
        return _answer({"1": "Don't go.", "0": "Why are you avoiding me?"})

    monkeypatch.setattr(vision, "ask_about_image", ask)
    usage = []
    regions = [{"id": "0", "text": "왜 나를 피하는 거야?", "box": {"x": 1, "y": 2, "w": 3, "h": 4}},
               {"id": "1", "text": "가지마", "box": {"x": 5, "y": 6, "w": 7, "h": 8}}]
    got, context = scanlate_translate.translate_regions_with_image(
        regions, b"PNGDATA", "image/png", _Engine(), {"source_language": "ko"},
        previous_context="Before.", usage_cb=lambda i, o: usage.append((i, o)),
        glossary_terms=None, character_notes="", chapter_brief="A prison romance.")
    assert got == {"0": "Why are you avoiding me?", "1": "Don't go."}
    assert context == "Two people argue."
    assert sent["image"] == b"PNGDATA" and sent["mime"] == "image/png"
    assert "[1, 2, 3, 4]" in sent["prompt"] and "Before." in sent["prompt"]
    assert "A prison romance." in sent["prompt"] and "Korean" in sent["prompt"]
    assert usage == [(3000, 200)]


def test_an_answer_that_misses_an_id_applies_nothing(monkeypatch):
    monkeypatch.setattr(vision, "ask_about_image",
                        lambda *a, **k: _answer({"0": "only one"}))
    regions = [{"id": "0", "text": "a", "box": {"x": 0, "y": 0, "w": 1, "h": 1}},
               {"id": "1", "text": "b", "box": {"x": 0, "y": 0, "w": 1, "h": 1}}]
    got, context = scanlate_translate.translate_regions_with_image(
        regions, b"x", "image/png", _Engine(), {"source_language": "zh"}, previous_context="keep")
    assert got is None and context == "keep"


def test_a_page_translation_applies_the_answer_and_scales_the_boxes(monkeypatch):
    seen = {}

    def with_image(regions, data, mime, engine, drama, **kw):
        seen["regions"] = regions
        return {r["id"]: f"EN {r['text']}" for r in regions}, "ctx"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    bubbles = [_region("你好", x=100, y=200, w=40, h=20), _region("世界", skip=True)]
    notes = []
    ok, context = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "zh"}, None,
                                     "", 1, notes, page_image=(b"x", "image/png", 0.5))
    assert ok and context == "ctx" and notes == []
    assert bubbles[0]["translated_text"] == "EN 你好" and bubbles[1]["translated_text"] == ""
    assert seen["regions"] == [{"id": "0", "text": "你好", "box": {"x": 50, "y": 100, "w": 20, "h": 10}}]


def test_a_failed_picture_request_falls_back_to_the_text_request(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("provider said no sk-ant-secretsecretsecretsecret")

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", broken)
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id",
                        lambda keyed, *a, **k: ({k_: "from text" for k_ in keyed}, "text ctx"))
    bubbles = [_region("你好")]
    notes = []
    ok, context = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "zh"}, None,
                                     "", 1, notes, page_image=(b"x", "image/png", 1.0))
    assert ok and context == "text ctx" and bubbles[0]["translated_text"] == "from text"
    assert notes and notes[0][0] == "info" and "text alone" in notes[0][1]
    assert "secretsecret" not in notes[0][1]


def test_a_dense_page_skips_the_picture_request(monkeypatch):
    called = []
    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image",
                        lambda *a, **k: called.append(1))
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id",
                        lambda keyed, *a, **k: ({k_: "t" for k_ in keyed}, "c"))
    bubbles = [_region(f"文{i}") for i in range(scanlate_translate.REGIONS_PER_CALL + 1)]
    ok, _ = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "zh"}, None,
                               "", 1, [], page_image=(b"x", "image/png", 1.0))
    assert ok and not called


def _stored_page(drama_id, width=120, height=80):
    folder = os.path.join(db.drama_dir(drama_id), "pages")
    os.makedirs(folder, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "PNG")
    with open(os.path.join(folder, "page_0000.png"), "wb") as f:
        f.write(buf.getvalue())
    return db.create_page(drama_id, 0, "pages/page_0000.png", width, height)


def test_the_page_picture_needs_the_setting_and_an_engine_that_reads_pictures(isolated_db, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    pid = _stored_page(did)
    patch_setting(monkeypatch, "scanlate_page_image", True)
    data, mime, scale = run_svc._page_image(did, pid, "claude")
    assert data and mime == "image/png" and scale == 1.0
    assert run_svc._page_image(did, pid, "deepseek") is None      # DeepSeek takes no pictures
    patch_setting(monkeypatch, "scanlate_page_image", False)
    assert run_svc._page_image(did, pid, "claude") is None


def test_a_missing_page_file_means_a_text_only_translation(isolated_db, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    pid = db.create_page(did, 0, "pages/gone.png", 120, 80)
    patch_setting(monkeypatch, "scanlate_page_image", True)
    assert run_svc._page_image(did, pid, "claude") is None


def test_a_claude_engine_reads_the_picture_as_haiku_and_logs_that_model(monkeypatch):
    built = {}

    class _Haiku(_Engine):
        model = "claude-haiku-5-5"

    def get_engine(name, key, model=None, **kw):
        built.update(name=name, model=model)
        return _Haiku()

    monkeypatch.setattr(run_svc.engine_registry, "get_engine", get_engine)
    monkeypatch.setattr(run_svc.translate_service, "resolve_api_key", lambda n: "k")
    assert run_svc._picture_engine(_Engine(), "claude").model == "claude-haiku-5-5"
    assert built == {"name": "claude", "model": "claude-haiku-5-5"}
    other = _Engine()
    assert run_svc._picture_engine(other, "gemini") is other


def test_the_chosen_engine_is_kept_when_haiku_cannot_be_built(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("no key")

    monkeypatch.setattr(run_svc.engine_registry, "get_engine", boom)
    engine = _Engine()
    assert run_svc._picture_engine(engine, "claude") is engine


def test_the_prompt_asks_for_an_empty_answer_for_a_watermark_not_a_note(monkeypatch):
    sent = {}

    def ask(engine, prompt, image, mime, max_tokens=0, on_done=None):
        sent["prompt"] = prompt
        return _answer({"0": "", "1": "Hello."})

    monkeypatch.setattr(vision, "ask_about_image", ask)
    regions = [{"id": "0", "text": "구글검색 뉴토끼", "box": {"x": 1, "y": 2, "w": 3, "h": 4}},
               {"id": "1", "text": "안녕", "box": {"x": 5, "y": 6, "w": 7, "h": 8}}]
    got, _ = scanlate_translate.translate_regions_with_image(
        regions, b"x", "image/png", _Engine(), {"source_language": "ko"})
    assert got == {"0": "", "1": "Hello."}
    assert 'answer "" (an empty string)' in sent["prompt"]


def test_both_prompts_ask_for_natural_dialogue_and_only_the_blind_one_forbids_guessing_gender(monkeypatch):
    sent = {}

    def ask(engine, prompt, image, mime, max_tokens=0, on_done=None):
        sent["picture"] = prompt
        return _answer({"0": "Hello."})

    monkeypatch.setattr(vision, "ask_about_image", ask)
    scanlate_translate.translate_regions_with_image(
        [{"id": "0", "text": "안녕", "box": {"x": 1, "y": 2, "w": 3, "h": 4}}], b"x", "image/png",
        _Engine(), {"source_language": "ko"})

    from engine_backends import llm_tasks

    def call(engine, prompt, **kw):
        sent["text_only"] = prompt
        return json.dumps({"translations": {"0": "Hello."}, "context_summary": "x"})

    monkeypatch.setattr(llm_tasks, "call_llm_json", call)
    scanlate_translate.translate_regions_by_id({"0": "안녕"}, _Engine(), {"source_language": "ko"})

    for key in ("picture", "text_only"):
        assert "word-for-word gloss" in sent[key] and "drop nothing" in sent[key]
        assert "romanised" in sent[key]
    assert "never guess he or she" in sent["text_only"]
    assert "never guess he or she" not in sent["picture"]
