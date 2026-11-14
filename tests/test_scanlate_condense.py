"""Condensing a sign's English when it is too long for the band it sits on."""
import cv2
import numpy as np
import pytest

import scanlate_condense
import scanlate_render


def _signs():
    return [{"kind": "sign", "translated_text": "Traffic is jammed. All AB line trains have stopped running."},
            {"kind": "sign", "translated_text": "Closed"},
            {"kind": "bubble", "translated_text": "A long line of dialogue that is not on a sign at all."},
            {"kind": "sign", "translated_text": "Skipped sign text that is far too long", "skip": True}]


def test_only_a_sign_set_below_the_readable_size_is_shortened():
    bubbles = _signs()
    sizes = {id(bubbles[0]): 9, id(bubbles[1]): 16}
    asked = {}

    def ask(wanted):
        asked.update(wanted)
        return {k: "Trains stopped" for k in wanted}

    changed = scanlate_condense.condense_long_signs(bubbles, lambda b: sizes.get(id(b)), ask)
    assert changed == [bubbles[0]]
    assert list(asked) == ["0"]
    assert bubbles[0]["translated_text"] == "Trains stopped"
    assert bubbles[1]["translated_text"] == "Closed"
    assert bubbles[2]["translated_text"].startswith("A long line")
    assert bubbles[3]["translated_text"].startswith("Skipped")


def test_the_budget_follows_the_size_the_text_was_set_at():
    bubbles = _signs()[:1]
    asked = {}
    scanlate_condense.condense_long_signs(bubbles, lambda b: 9, lambda wanted: asked.update(wanted) or {})
    text, limit = asked["0"]
    assert limit == int(len(text) * (9 / 13) ** 2)


def test_a_short_sound_word_on_a_small_band_is_not_sent_for_shortening():
    bubbles = [{"kind": "sign", "translated_text": "Grrr..."}]
    asked = []
    scanlate_condense.condense_long_signs(bubbles, lambda b: 9, lambda wanted: asked.append(wanted) or {})
    assert asked == []


def test_an_answer_that_is_longer_empty_or_for_another_key_is_ignored():
    for answer in ({"0": "x" * 200}, {"0": "  "}, {"5": "Short"}, {"0": 7}, None):
        bubbles = _signs()[:1]
        original = bubbles[0]["translated_text"]
        scanlate_condense.condense_long_signs(bubbles, lambda b: 9, lambda wanted: answer)
        assert bubbles[0]["translated_text"] == original


def test_a_sign_with_no_band_to_fit_is_left_alone():
    bubbles = _signs()[:1]
    asked = []
    scanlate_condense.condense_long_signs(bubbles, lambda b: None, lambda wanted: asked.append(wanted) or {})
    assert asked == []


def test_the_answer_is_matched_by_key_from_the_engine_reply(monkeypatch):
    reply = '```json\n{"shortened": {"1": "Trains stopped", "0": "Closed"}}\n```'
    monkeypatch.setattr("engine_backends.llm_tasks.call_llm_json", lambda *a, **k: reply)
    out = scanlate_condense.ask_for_shorter(object(), "Chinese", {"0": ("Closed today", 8), "1": ("Trains are stopped", 12)})
    assert out == {"0": "Closed", "1": "Trains stopped"}


def test_an_unreadable_engine_reply_gives_no_answer(monkeypatch):
    for reply in (None, "sorry", '{"other": 1}', '{"shortened": ["a"]}'):
        monkeypatch.setattr("engine_backends.llm_tasks.call_llm_json", lambda *a, reply=reply, **k: reply)
        assert scanlate_condense.ask_for_shorter(object(), "Chinese", {"0": ("Closed today", 8)}) == {}


def test_a_long_text_on_a_one_line_band_is_set_smaller_than_a_short_one():
    from tests.test_scanlate_layout_fixes import _band_over_art_page
    page, _band, _glyphs = _band_over_art_page()
    bgr = cv2.cvtColor(page, cv2.COLOR_GRAY2BGR)
    box = {"x": 0, "y": 40, "w": 400, "h": 140, "kind": "sign"}
    short = scanlate_render.sign_fitted_size(bgr, box, "Closed")
    long = scanlate_render.sign_fitted_size(
        bgr, box, "Traffic congestion on both lines, service suspended until further notice, so please plan another route")
    assert short is not None and long is not None
    assert long < short


def test_a_sign_without_a_band_has_no_fitted_size():
    page = np.full((100, 200, 3), 255, np.uint8)
    assert scanlate_render.sign_fitted_size(page, {"x": 0, "y": 0, "w": 200, "h": 100}, "Closed") is None


def test_the_run_shortens_a_long_sign_and_says_so(monkeypatch):
    import scanlate_detect
    from services import scanlate_run_service as run
    monkeypatch.setattr(run.db, "get_page", lambda *a, **k: {})
    monkeypatch.setattr(run.render_svc, "original_path", lambda *a, **k: "page.png")
    monkeypatch.setattr(run.render_svc, "_custom_fonts", lambda drama_id: {})
    monkeypatch.setattr(scanlate_detect, "decode_page", lambda path: object())
    monkeypatch.setattr(scanlate_render, "sign_fitted_size", lambda page, b, text, fonts: 9)
    monkeypatch.setattr(scanlate_condense, "ask_for_shorter", lambda *a, **k: {"0": "Trains stopped"})
    bubbles = [{"kind": "sign", "translated_text": "Traffic is jammed. All AB line trains have stopped."}]
    notes = []
    run._condense_signs(1, 2, bubbles, object(), "claude", {"source_language": "zh"}, notes)
    assert bubbles[0]["translated_text"] == "Trains stopped"
    assert "shortened" in notes[0][1]


def test_a_page_that_cannot_be_read_keeps_the_full_translation(monkeypatch):
    from services import scanlate_run_service as run
    monkeypatch.setattr(run.db, "get_page", lambda *a, **k: {})
    monkeypatch.setattr(run.render_svc, "original_path", lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))
    bubbles = [{"kind": "sign", "translated_text": "Traffic is jammed."}]
    notes = []
    run._condense_signs(1, 2, bubbles, object(), "claude", {}, notes)
    assert bubbles[0]["translated_text"] == "Traffic is jammed." and notes == []


def test_the_reply_is_allowed_room_for_the_whole_json_even_for_one_sign(monkeypatch):
    seen = {}

    def fake(engine, prompt, max_tokens, **kwargs):
        seen["max_tokens"] = max_tokens
        return '{"shortened": {"0": "Closed"}}'

    monkeypatch.setattr("engine_backends.llm_tasks.call_llm_json", fake)
    scanlate_condense.ask_for_shorter(object(), "Chinese", {"0": ("Closed today", 8)})
    assert seen["max_tokens"] >= 1500        # a 400-token cap cut one sign's answer off on a real engine


def test_only_the_english_a_translation_call_wrote_is_up_for_shortening():
    from services import scanlate_run_service as run
    typed_by_hand = {"kind": "sign", "translated_text": "Typed by hand, kept from an earlier run"}
    translated = {"kind": "sign", "source_text": "交通阻塞", "translated_text": None}
    before = [b.get("translated_text") for b in (typed_by_hand, translated)]
    translated["translated_text"] = "Traffic is jammed."
    assert run._translated_since([typed_by_hand, translated], before) == [translated]
