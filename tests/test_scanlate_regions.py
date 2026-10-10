"""
Tests for Step 12d: structured text regions, editable OCR text before
translation, shape-aware text fitting, whole-chapter batch processing,
glossary/honorifics in Scanlate's translate call, and SFX skip-by-default.

Every image here is a synthetic shape drawn in the test -- no real comic
pages. OCR, detection models and translation engines are all faked.
"""
import json
import os

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")  # requirements-media.txt, not core

import scanlate
import scanlate_detect
import translate_engines


def _write(path, img):
    cv2.imwrite(str(path), img)
    return str(path)


@pytest.fixture
def bubble_page(tmp_path):
    """Dark page, one white rectangular bubble with horizontal scribble lines."""
    img = np.full((400, 600, 3), 50, dtype=np.uint8)
    cv2.rectangle(img, (100, 80), (400, 220), (255, 255, 255), -1)
    cv2.rectangle(img, (100, 80), (400, 220), (0, 0, 0), 3)
    for i in range(6):
        cv2.line(img, (130, 110 + i * 15), (350, 110 + i * 15), (10, 10, 10), 4)
    return _write(tmp_path / "page.png", img)


@pytest.fixture
def oval_page(tmp_path):
    """White page, one white oval bubble with a black outline, no text."""
    img = np.full((400, 600, 3), 255, dtype=np.uint8)
    img[:, :] = (90, 90, 90)
    cv2.ellipse(img, (300, 200), (200, 110), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(img, (300, 200), (200, 110), 0, 0, 360, (0, 0, 0), 3)
    return _write(tmp_path / "oval.png", img)


class _RecordingMT:
    """Pure-MT engine double: records exactly what it was asked to translate."""
    supports_reference = False

    def __init__(self):
        self.calls = []

    def translate_batch(self, texts, context):
        self.calls.append((list(texts), context))
        return [f"EN<{t}>" for t in texts]


# ---------------------------------------------------------------------------
# Item 1: one structured object per region
# ---------------------------------------------------------------------------

class TestStructuredRegions:
    def test_region_carries_every_field_as_one_object(self, bubble_page):
        boxes = [{"x": 100, "y": 80, "w": 300, "h": 140, "confidence": 0.87, "label": "bubble"}]
        regions = scanlate.analyze_page_regions(bubble_page, boxes, "zh", page_id=7)
        assert len(regions) == 1
        r = regions[0]
        assert isinstance(r, scanlate.TextRegion)
        assert r.bbox == (100, 80, 300, 140)
        assert r.reading_order == 0
        assert r.language == "zh"
        assert r.confidence == pytest.approx(0.87)
        assert r.orientation in ("horizontal", "vertical")
        assert r.panel_id is None or isinstance(r.panel_id, int)
        assert r.kind in scanlate_detect.TEXT_REGION_KINDS
        assert r.page_id == 7

    def test_to_bubble_is_the_flat_shape_save_bubbles_takes(self, bubble_page):
        r = scanlate.analyze_page_regions(
            bubble_page, [{"x": 100, "y": 80, "w": 300, "h": 140}], "ja")[0]
        d = r.to_bubble()
        for key in ("x", "y", "w", "h", "reading_order", "language", "confidence",
                    "orientation", "panel_id", "kind", "kind_confidence"):
            assert key in d
        assert "page_id" not in d

    def test_heuristic_detector_has_no_invented_confidence(self, bubble_page):
        r = scanlate.analyze_page_regions(
            bubble_page, [{"x": 100, "y": 80, "w": 300, "h": 140}], "zh")[0]
        assert r.confidence is None

    def test_horizontal_lines_read_as_horizontal(self, bubble_page):
        r = scanlate.analyze_page_regions(
            bubble_page, [{"x": 100, "y": 80, "w": 300, "h": 140}], "ja")[0]
        assert r.orientation == "horizontal"

    def test_vertical_columns_read_as_vertical_for_cjk(self, tmp_path):
        img = np.full((400, 400, 3), 255, dtype=np.uint8)
        for i in range(5):
            x = 120 + i * 30
            cv2.line(img, (x, 80), (x, 320), (0, 0, 0), 6)
        path = _write(tmp_path / "vert.png", img)
        gray = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        box = {"x": 90, "y": 60, "w": 200, "h": 280}
        assert scanlate.estimate_text_orientation(gray, box, "ja") == "vertical"
        # Latin-script languages are never laid out vertically.
        assert scanlate.estimate_text_orientation(gray, box, "en") == "horizontal"

    def test_panel_id_and_reading_order_follow_panels(self, tmp_path):
        # Two side-by-side panels; manga order reads the RIGHT one first.
        img = np.full((600, 800, 3), 255, dtype=np.uint8)
        cv2.rectangle(img, (20, 20), (380, 580), (0, 0, 0), 4)
        cv2.rectangle(img, (420, 20), (780, 580), (0, 0, 0), 4)
        path = _write(tmp_path / "panels.png", img)
        left_box = {"x": 60, "y": 60, "w": 100, "h": 80}
        right_box = {"x": 460, "y": 300, "w": 100, "h": 80}
        panels = scanlate_detect.detect_panels(path)
        assert len(panels) >= 2
        regions = scanlate.analyze_page_regions(path, [left_box, right_box], "ja")
        by_x = {r.x: r for r in regions}
        assert by_x[460].panel_id is not None and by_x[60].panel_id is not None
        assert by_x[460].panel_id != by_x[60].panel_id
        assert by_x[460].reading_order < by_x[60].reading_order

    @pytest.mark.parametrize("text,fallback,expected", [
        ("こんにちは", "zh", "ja"),
        ("안녕하세요", "zh", "ko"),
        ("你好", "zh", "zh"),
        ("先輩", "ja", "ja"),       # all-kanji Japanese keeps the drama's language
        ("BOOM", "ja", "en"),
        ("", "ko", "ko"),
    ])
    def test_detect_script_language(self, text, fallback, expected):
        assert scanlate.detect_script_language(text, fallback) == expected

    def test_detect_and_ocr_page_returns_structured_bubbles(self, bubble_page, monkeypatch):
        monkeypatch.setattr(scanlate, "detect_bubbles",
                            lambda path, backend="auto", **kw: [{"x": 100, "y": 80, "w": 300, "h": 140}])
        monkeypatch.setattr(scanlate, "ocr_box_region", lambda *a, **kw: "こんにちは")
        bubbles, notes = scanlate.detect_and_ocr_page(bubble_page, "zh", page_id=3)
        assert notes == []
        b = bubbles[0]
        assert b["source_text"] == "こんにちは"
        assert b["language"] == "ja"  # refined from the OCR'd script
        assert b["translated_text"] == "" and b["skip"] is False and b["include_sfx"] is False
        assert {"kind", "orientation", "panel_id", "confidence", "reading_order"} <= set(b)


class TestRegionFieldsPersist:
    def test_save_and_load_round_trip_every_region_field(self, isolated_db):
        did = isolated_db.create_drama(title_en="Comic")
        pid = isolated_db.create_page(did, 0, "pages/p.png", 600, 400)
        isolated_db.save_bubbles(pid, [{
            "x": 1, "y": 2, "w": 30, "h": 40, "source_text": "ドン", "translated_text": "",
            "kind": "sfx", "kind_confidence": 0.5, "confidence": 0.9, "language": "ja",
            "orientation": "vertical", "panel_id": 2, "include_sfx": True,
        }, {"x": 5, "y": 6, "w": 30, "h": 40}])
        first, second = isolated_db.load_bubbles(pid)
        assert first["kind"] == "sfx" and first["include_sfx"] == 1
        assert first["confidence"] == pytest.approx(0.9)
        assert first["language"] == "ja" and first["orientation"] == "vertical"
        assert first["panel_id"] == 2 and first["idx"] == 0
        # A bubble saved without region fields defaults to a plain speech bubble.
        assert second["kind"] == "bubble" and second["include_sfx"] == 0


# ---------------------------------------------------------------------------
# Item 2: edited OCR text is what gets translated
# ---------------------------------------------------------------------------

class TestEditedSourceTextIsTranslated:
    def test_edit_changes_what_is_sent_to_the_engine(self):
        bubbles = [{"source_text": "你妤", "translated_text": ""},
                   {"source_text": "再见", "translated_text": ""}]
        bubbles[0]["source_text"] = "你好"  # the person fixes an OCR mistake
        engine = _RecordingMT()
        scanlate.translate_page_bubbles(bubbles, engine, {})
        assert [c[0] for c in engine.calls] == [["你好"], ["再见"]]      # one region per call
        assert bubbles[0]["translated_text"] == "EN<你好>"

    def test_skipped_and_empty_regions_are_not_sent_and_keep_their_text(self):
        bubbles = [{"source_text": "一", "translated_text": "keep", "skip": True},
                   {"source_text": "  ", "translated_text": ""},
                   {"source_text": "二", "translated_text": ""}]
        engine = _RecordingMT()
        scanlate.translate_page_bubbles(bubbles, engine, {})
        assert engine.calls[0][0] == ["二"]
        assert bubbles[0]["translated_text"] == "keep"

    def test_mismatched_result_count_is_rejected_not_assigned_by_position(self, monkeypatch):
        monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **kw: json.dumps(
            {"translations": ["only one"], "context_summary": "s"}))
        bubbles = [{"source_text": "一"}, {"source_text": "二"}]
        with pytest.raises(ValueError):
            scanlate.translate_page_bubbles(bubbles, _FakeLLM(), {})
        assert "translated_text" not in bubbles[0]

    @pytest.mark.parametrize("answer", [
        {"translations": {"1": "B", "0": "A", "2": "extra"}},       # padded: unknown id
        {"translations": {"0": "A"}},                                # short
        {"translations": ["A", "B"]},                                # positional list
    ])
    def test_padded_short_or_positional_answer_applies_nothing(self, monkeypatch, answer):
        monkeypatch.setattr(translate_engines, "call_llm_json",
                            lambda *a, **kw: json.dumps(answer))
        bubbles = [{"source_text": "一"}, {"source_text": "二"}]
        with pytest.raises(ValueError):
            scanlate.translate_page_bubbles(bubbles, _FakeLLM(), {})
        assert all("translated_text" not in b for b in bubbles)

    def test_reordered_answer_is_matched_by_id(self, monkeypatch):
        monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **kw: json.dumps(
            {"translations": {"1": "Two", "0": "One"}, "context_summary": "s"}))
        bubbles = [{"source_text": "一"}, {"source_text": "二"}]
        assert scanlate.translate_page_bubbles(bubbles, _FakeLLM(), {}) == "s"
        assert [b["translated_text"] for b in bubbles] == ["One", "Two"]


# ---------------------------------------------------------------------------
# Step 25n: a failed page translation must not silently blank every bubble
# ---------------------------------------------------------------------------

class TestFailedTranslationDoesNotBlankBubbles:
    def _bubbles_with_a_hand_edit(self):
        return [{"source_text": "师姐", "translated_text": "Senior Sister (hand-edited)"}]

    def test_unparseable_json_is_not_applied(self, monkeypatch):
        monkeypatch.setattr(translate_engines, "call_llm_json",
                            lambda *a, **kw: "not json at all, truncated by max_tok")
        bubbles = self._bubbles_with_a_hand_edit()
        with pytest.raises(ValueError):
            scanlate.translate_page_bubbles(bubbles, _FakeLLM(), {})
        assert bubbles[0]["translated_text"] == "Senior Sister (hand-edited)"

    def test_response_missing_the_translations_key_is_not_applied(self, monkeypatch):
        monkeypatch.setattr(translate_engines, "call_llm_json",
                            lambda *a, **kw: json.dumps({"context_summary": "s"}))
        bubbles = self._bubbles_with_a_hand_edit()
        with pytest.raises(ValueError):
            scanlate.translate_page_bubbles(bubbles, _FakeLLM(), {})
        assert bubbles[0]["translated_text"] == "Senior Sister (hand-edited)"

    def test_no_response_at_all_is_not_applied(self, monkeypatch):
        monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **kw: None)
        bubbles = self._bubbles_with_a_hand_edit()
        with pytest.raises(ValueError):
            scanlate.translate_page_bubbles(bubbles, _FakeLLM(), {})
        assert bubbles[0]["translated_text"] == "Senior Sister (hand-edited)"

    def test_genuinely_empty_translations_list_is_distinct_from_a_parse_failure(self, monkeypatch):
        # A well-formed response the model legitimately returned with nothing
        # to translate is not a failure -- translate_page_with_context()
        # returns it as-is, unlike the None sentinel the failure cases above
        # return. (It still won't be silently applied here: the length
        # mismatch against the one bubble that was actually sent is caught
        # by the existing, separate length check.)
        monkeypatch.setattr(translate_engines, "call_llm_json",
                            lambda *a, **kw: json.dumps({"translations": []}))
        translations, _ = scanlate.translate_page_with_context(["师姐"], _FakeLLM(), {})
        assert translations == []


# ---------------------------------------------------------------------------
# Item 3: text fitted to the bubble's real shape
# ---------------------------------------------------------------------------

class TestShapeAwareFitting:
    BOX = {"x": 100, "y": 90, "w": 400, "h": 220}  # the oval's bounding box

    def _ink_outside(self, oval_page, mask):
        from PIL import Image
        canvas = Image.new("RGB", (600, 400), (255, 255, 255))
        text = " ".join(["word"] * 40)
        scanlate.render_text_in_box(canvas, self.BOX, text, font_size=22, mask=mask)
        ink = np.array(canvas.convert("L")) < 128
        oval = np.zeros((400, 600), dtype=np.uint8)
        cv2.ellipse(oval, (300, 200), (200, 110), 0, 0, 360, 1, -1)
        assert ink.any()
        return int((ink & (oval == 0)).sum())

    def test_mask_follows_the_oval_not_the_rectangle(self, oval_page):
        mask = scanlate_detect.bubble_shape_mask(oval_page, self.BOX)
        assert mask is not None and mask.shape == (220, 400)
        assert not mask[0, 0] and not mask[-1, -1]   # rectangle corners are outside
        assert mask[110, 200]                         # centre is inside

    def test_text_stays_inside_an_oval_bubble(self, oval_page):
        mask = scanlate_detect.bubble_shape_mask(oval_page, self.BOX)
        # Rectangle layout spills into the corners outside the oval...
        assert self._ink_outside(oval_page, None) > 0
        # ...the shape-aware layout doesn't.
        assert self._ink_outside(oval_page, mask) == 0

    def test_all_words_are_still_drawn_with_a_mask(self, oval_page):
        from PIL import Image, ImageDraw
        mask = scanlate_detect.bubble_shape_mask(oval_page, self.BOX)
        font = scanlate._find_font(None)
        from PIL import ImageFont
        draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        text = "one two three four five six seven eight nine ten"
        laid = scanlate._layout_in_mask(
            draw, text, mask, 22,
            lambda sz: ImageFont.truetype(font, sz) if font else ImageFont.load_default())
        assert laid is not None
        assert " ".join(line for line, *_ in laid[1]) == text

    def test_no_light_region_means_no_mask(self, tmp_path):
        dark = _write(tmp_path / "dark.png", np.full((200, 200, 3), 20, dtype=np.uint8))
        assert scanlate_detect.bubble_shape_mask(dark, {"x": 20, "y": 20, "w": 100, "h": 100}) is None


# ---------------------------------------------------------------------------
# Item 4: batch detect + OCR + translate over every page
# ---------------------------------------------------------------------------

class TestBatchProcessPages:
    def _fake_pipeline(self, monkeypatch, detected):
        def fake_detect(path, backend="auto", **kw):
            detected.append(path)
            if not os.path.exists(path):
                raise ValueError(f"Could not read image: {path}")  # as detect_bubbles_cv does
            return [{"x": 100, "y": 80, "w": 300, "h": 140}]
        monkeypatch.setattr(scanlate, "detect_bubbles", fake_detect)
        monkeypatch.setattr(scanlate, "ocr_box_region",
                            lambda path, *a, **kw: f"text of {os.path.basename(path)}")

    def test_every_page_gets_detect_ocr_translate(self, bubble_page, tmp_path, monkeypatch):
        import shutil
        paths = []
        for i in range(3):
            p = str(tmp_path / f"p{i}.png")
            shutil.copy(bubble_page, p)
            paths.append(p)
        detected, saved = [], {}
        self._fake_pipeline(monkeypatch, detected)
        engine = _RecordingMT()
        report = scanlate.batch_process_pages(
            [{"id": i, "image_path": p} for i, p in enumerate(paths)], "zh",
            lambda pid, bubbles: saved.__setitem__(pid, bubbles), engine=engine)
        assert detected == paths
        assert sorted(saved) == [0, 1, 2]
        for i in range(3):
            assert saved[i][0]["source_text"] == f"text of p{i}.png"
            assert saved[i][0]["translated_text"] == f"EN<text of p{i}.png>"
        assert len(engine.calls) == 3
        assert len(report["processed"]) == 3 and report["errors"] == []

    def test_one_failing_page_does_not_stop_the_rest(self, bubble_page, tmp_path, monkeypatch):
        detected, saved = [], {}
        self._fake_pipeline(monkeypatch, detected)
        report = scanlate.batch_process_pages(
            [{"id": 1, "image_path": str(tmp_path / "missing.png")},
             {"id": 2, "image_path": bubble_page}], "zh",
            lambda pid, bubbles: saved.__setitem__(pid, bubbles))
        assert list(saved) == [2]
        assert [e["page_id"] for e in report["errors"]] == [1]

    def test_rolling_context_is_carried_page_to_page(self, bubble_page, monkeypatch):
        detected, seen_contexts = [], []
        self._fake_pipeline(monkeypatch, detected)

        def fake_translate(texts_by_id, engine, meta, previous_context="", **kw):
            seen_contexts.append(previous_context)
            return {k: f"t{len(seen_contexts)}" for k in texts_by_id}, f"ctx{len(seen_contexts)}"
        monkeypatch.setattr(scanlate, "translate_regions_by_id", fake_translate)
        report = scanlate.batch_process_pages(
            [{"id": i, "image_path": bubble_page} for i in range(3)], "zh",
            lambda *a: None, engine=object(), previous_context="start")
        assert seen_contexts == ["start", "ctx1", "ctx2"]
        assert report["context"] == "ctx3"

    def test_failed_translation_still_saves_the_ocr_text(self, bubble_page, monkeypatch):
        detected, saved = [], {}
        self._fake_pipeline(monkeypatch, detected)

        def boom(*a, **kw):
            raise RuntimeError("API down")
        monkeypatch.setattr(scanlate, "translate_regions_by_id", boom)
        report = scanlate.batch_process_pages(
            [{"id": 1, "image_path": bubble_page}], "zh",
            lambda pid, bubbles: saved.__setitem__(pid, bubbles), engine=object())
        assert saved[1][0]["source_text"] == "text of page.png"
        assert saved[1][0]["translated_text"] == ""
        assert report["processed"][0]["notes"][0][0] == "warning"


# ---------------------------------------------------------------------------
# Item 5: glossary / honorifics reach Scanlate's translation
# ---------------------------------------------------------------------------

HONORIFIC = [{"term_original": "师姐", "term_translation": "Senior Sister",
              "category": "honorific", "policy": "translate", "notes": ""}]


class _FakeLLM:
    supports_reference = True


class TestGlossaryInScanlateTranslation:
    def _fake_llm_that_follows_the_glossary(self, monkeypatch, prompts):
        def fake_call(engine, prompt, max_tokens=2000, fallback="[]", usage_cb=None):
            prompts.append(prompt)
            use = "Senior Sister" if "师姐 → Senior Sister" in prompt else "Shijie"
            return json.dumps({"translations": [f"Wait, {use}!"], "context_summary": "s"})
        monkeypatch.setattr(translate_engines, "call_llm_json", fake_call)

    def test_honorific_term_is_applied_in_the_output(self, monkeypatch):
        prompts = []
        self._fake_llm_that_follows_the_glossary(monkeypatch, prompts)
        translations, _ = scanlate.translate_page_with_context(
            ["等等，师姐！"], _FakeLLM(), {}, glossary_terms=HONORIFIC)
        assert translations == ["Wait, Senior Sister!"]
        assert "TERM GLOSSARY" in prompts[0]

    def test_without_the_glossary_the_term_is_not_applied(self, monkeypatch):
        prompts = []
        self._fake_llm_that_follows_the_glossary(monkeypatch, prompts)
        translations, _ = scanlate.translate_page_with_context(["等等，师姐！"], _FakeLLM(), {})
        assert translations == ["Wait, Shijie!"]
        assert "TERM GLOSSARY" not in prompts[0]

    def test_pure_mt_engine_is_handed_the_glossary_too(self):
        engine = _RecordingMT()
        scanlate.translate_page_bubbles([{"source_text": "师姐"}], engine, {},
                                        glossary_terms=HONORIFIC)
        assert engine.calls[0][1]["glossary_terms"] == HONORIFIC

    def test_glossary_block_matches_workspace_style_guidelines(self):
        import translation_guide
        block = translation_guide.build_glossary_block(HONORIFIC)
        assert block in translation_guide.build_style_guidelines(glossary_terms=HONORIFIC)
        assert translation_guide.build_glossary_block([]) == ""


# ---------------------------------------------------------------------------
# Item 6: SFX left alone by default, per-region override
# ---------------------------------------------------------------------------

class TestSfxSkipByDefault:
    SFX = {"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "BANG",
           "source_text": "ドン", "kind": "sfx"}

    def _render(self, bubble_page, tmp_path, monkeypatch, bubbles):
        inpainted, drawn = [], []
        monkeypatch.setattr(scanlate, "inpaint_region",
                            lambda path, box, out_path=None, **kw: inpainted.append(box) or out_path)
        monkeypatch.setattr(scanlate, "render_text_in_box",
                            lambda img, box, text, **kw: drawn.append(text) or img)
        _, skipped_blank = scanlate.process_page(bubble_page, bubbles, str(tmp_path / "out.png"))
        return inpainted, drawn, skipped_blank

    def test_sfx_is_not_inpainted_or_replaced_by_default(self, bubble_page, tmp_path, monkeypatch):
        dialogue = dict(self.SFX, kind="bubble", translated_text="Hello")
        inpainted, drawn, skipped_blank = self._render(
            bubble_page, tmp_path, monkeypatch, [dict(self.SFX), dialogue])
        assert drawn == ["Hello"]
        assert len(inpainted) == 1 and inpainted[0]["kind"] == "bubble"
        assert skipped_blank == []

    def test_override_includes_the_sfx(self, bubble_page, tmp_path, monkeypatch):
        inpainted, drawn, _ = self._render(
            bubble_page, tmp_path, monkeypatch, [dict(self.SFX, include_sfx=True)])
        assert drawn == ["BANG"] and len(inpainted) == 1

    def test_blank_sfx_is_not_reported_as_a_missing_translation(self, bubble_page, tmp_path,
                                                                monkeypatch):
        _, _, skipped_blank = self._render(
            bubble_page, tmp_path, monkeypatch, [dict(self.SFX, translated_text="")])
        assert skipped_blank == []

    def test_sfx_is_not_sent_for_translation_unless_included(self):
        engine = _RecordingMT()
        bubbles = [{"source_text": "ドン", "kind": "sfx"}, {"source_text": "やめて", "kind": "bubble"}]
        scanlate.translate_page_bubbles(bubbles, engine, {})
        assert engine.calls[0][0] == ["やめて"]
        assert "translated_text" not in bubbles[0]

        engine = _RecordingMT()
        bubbles[0]["include_sfx"] = True
        scanlate.translate_page_bubbles(bubbles, engine, {})
        assert [c[0] for c in engine.calls] == [["ドン"], ["やめて"]]

    def test_region_excluded_from_auto(self):
        assert scanlate.region_excluded_from_auto({"kind": "sfx"})
        assert not scanlate.region_excluded_from_auto({"kind": "sfx", "include_sfx": 1})
        assert not scanlate.region_excluded_from_auto({"kind": "bubble"})
        assert not scanlate.region_excluded_from_auto({})
