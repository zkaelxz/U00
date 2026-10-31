"""The Scanlate run job across pages: a caption cut by the page break, the
typeset image after a redo, and the detector note. Detection, rendering and
the engine are doubles, so no OpenCV or model is needed."""

import io
import json
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

import background_jobs
import db
import scanlate
import scanlate_detect
from services import scanlate_run_service as run_svc
from tests.saved_settings import patch_setting

PAGE_H = 80


class _MT:
    """Pure-MT double (one call per region): fixed English per source text."""
    supports_reference = False
    name = "mt"
    model = "mt-test"
    ENGLISH = {"你好": "Hello", "世界": "World", "破裂": "shatters",
               "世界破裂": "The world shatters apart"}

    def __init__(self):
        self.calls = []
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, lines, context):
        self.calls.extend(lines)
        return [self.ENGLISH.get(t, f"EN {t}") for t in lines]


def _region(text, kind="bubble", y=10, h=30, **kw):
    b = {"x": 10, "y": y, "w": 100, "h": h, "source_text": text, "translated_text": "",
         "kind": kind, "font_size": 12, "skip": False, "include_sfx": False, "language": "zh",
         "orientation": "horizontal", "panel_id": None, "kind_confidence": 0.5,
         "confidence": None, "font_category": "regular"}
    b.update(kw)
    return b


def _page(did, idx):
    folder = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(folder, exist_ok=True)
    name = f"page_{idx:04d}.png"
    buf = io.BytesIO()
    Image.new("RGB", (120, PAGE_H), "white").save(buf, "PNG")
    with open(os.path.join(folder, name), "wb") as f:
        f.write(buf.getvalue())
    return db.create_page(did, idx, f"pages/{name}", 120, PAGE_H)


@pytest.fixture
def harness(isolated_db, monkeypatch):
    monkeypatch.setattr(run_svc.render_svc, "check_cancel", lambda jid: None)
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend", lambda lang: "auto")
    monkeypatch.setattr(run_svc.settings_service, "resolve_key", lambda k: None)
    patch_setting(monkeypatch, "tesseract_cmd", None)
    renders = []

    def render(drama_id, page_id, notes=None, cancel_check=None):
        renders.append((page_id, [b["translated_text"] for b in db.load_bubbles(page_id)]))
        db.update_page(page_id, rendered_filename=f"pages/typeset_id{page_id}.png")
        return {"rendered": True, "blank": 0}
    monkeypatch.setattr(run_svc.render_svc, "render_page", render)
    return renders


def _detect_by_page(monkeypatch, regions_by_page):
    monkeypatch.setattr(scanlate, "detect_and_ocr_page",
                        lambda path, lang, page_id=None, **kw: (
                            [dict(r) for r in regions_by_page[page_id]], []))


def _texts(pid):
    return {b["source_text"]: b["translated_text"] for b in db.load_bubbles(pid)}


def test_a_caption_cut_by_the_page_break_is_translated_as_one_line(harness, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    p1, p2 = _page(did, 0), _page(did, 1)
    _detect_by_page(monkeypatch, {
        p1: [_region("你好"), _region("世界", kind="caption", y=PAGE_H - 20, h=20)],
        p2: [_region("破裂", kind="caption", y=0, h=20), _region("你好", y=40)]})
    engine = _MT()
    run_svc._run_job("j", did, "all", [p1, p2], "mt", engine, "auto", art_captions=True)
    assert "世界破裂" in engine.calls
    assert _texts(p1) == {"你好": "Hello", "世界": "The world"}
    assert _texts(p2) == {"破裂": "shatters apart", "你好": "Hello"}
    # Both pages were typeset before the join, so each is typeset again with its half.
    assert [pid for pid, _ in harness] == [p1, p2, p2, p1]
    assert harness[2] == (p2, ["shatters apart", "Hello"])
    assert harness[3] == (p1, ["Hello", "The world"])


def _cut_caption_pages(monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    p1, p2 = _page(did, 0), _page(did, 1)
    _detect_by_page(monkeypatch, {
        p1: [_region("世界", kind="caption", y=PAGE_H - 20, h=20)],
        p2: [_region("破裂", kind="caption", y=0, h=20)]})
    return did, p1, p2


def test_a_timed_out_join_still_leaves_the_page_typeset(harness, monkeypatch):
    from engine_backends.shared import LLMTaskTimeout
    did, p1, p2 = _cut_caption_pages(monkeypatch)
    real = scanlate.translate_page_bubbles

    def hangs_on_the_join(bubbles, *a, **k):
        if bubbles[0]["source_text"] == "世界破裂":
            raise LLMTaskTimeout("did not answer")
        return real(bubbles, *a, **k)
    monkeypatch.setattr(scanlate, "translate_page_bubbles", hangs_on_the_join)
    with pytest.raises(LLMTaskTimeout):
        run_svc._run_job("j", did, "all", [p1, p2], "mt", _MT(), "auto", art_captions=True)
    assert [pid for pid, _ in harness] == [p1, p2]
    assert db.get_page(p2)["rendered_filename"] == f"pages/typeset_id{p2}.png"


def test_a_caption_edited_during_the_join_is_not_left_half_joined(harness, monkeypatch):
    did, p1, p2 = _cut_caption_pages(monkeypatch)
    real = scanlate.translate_page_bubbles

    def edit_meanwhile(bubbles, *a, **k):
        if bubbles[0]["source_text"] == "世界破裂":
            lower = db.load_bubbles(p2)[0]
            db.update_bubble_fields(lower["id"], {"translated_text": "Hand-made"})
        return real(bubbles, *a, **k)
    monkeypatch.setattr(scanlate, "translate_page_bubbles", edit_meanwhile)
    run_svc._run_job("j", did, "all", [p1, p2], "mt", _MT(), "auto", art_captions=True)
    assert _texts(p1) == {"世界": "World"} and _texts(p2) == {"破裂": "Hand-made"}


def test_captions_away_from_the_page_break_are_not_joined(harness, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    p1, p2 = _page(did, 0), _page(did, 1)
    _detect_by_page(monkeypatch, {
        p1: [_region("世界", kind="caption", y=10, h=20)],
        p2: [_region("破裂", kind="caption", y=0, h=20)]})
    engine = _MT()
    run_svc._run_job("j", did, "all", [p1, p2], "mt", engine, "auto", art_captions=True)
    assert "世界破裂" not in engine.calls
    assert _texts(p1) == {"世界": "World"} and _texts(p2) == {"破裂": "shatters"}
    assert [pid for pid, _ in harness] == [p1, p2]


def test_a_page_the_run_skipped_is_not_joined_with_the_next(harness, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    p1, p2 = _page(did, 0), _page(did, 1)
    db.save_bubbles(p1, [_region("世界", kind="caption", y=PAGE_H - 20, h=20,
                                 translated_text="Hand-made")])
    _detect_by_page(monkeypatch, {p2: [_region("破裂", kind="caption", y=0, h=20)]})
    engine = _MT()
    run_svc._run_job("j", did, "missing", [p1, p2], "mt", engine, "auto", art_captions=True)
    assert _texts(p1) == {"世界": "Hand-made"} and _texts(p2) == {"破裂": "shatters"}


def test_a_redo_whose_render_fails_does_not_keep_the_old_typeset_image(harness, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    pid = _page(did, 0)
    db.save_bubbles(pid, [_region("你好", translated_text="Old hello")])
    db.update_page(pid, rendered_filename=f"pages/typeset_id{pid}.png")
    _detect_by_page(monkeypatch, {pid: [_region("你好")]})

    def render_fails(*a, **k):
        raise RuntimeError("out of disk")
    monkeypatch.setattr(run_svc.render_svc, "render_page", render_fails)
    run_svc._run_job("j", did, "page", [pid], "mt", _MT(), "auto")
    page = db.get_page(pid)
    assert _texts(pid) == {"你好": "Hello"}
    assert page["rendered_filename"] is None       # the reader shows the original instead
    assert any("Render failed" in n["message"] for n in json.loads(page["run_notes"]))


def test_detector_note_blames_the_ml_detector_only_for_its_own_fallback():
    no_bubbles = [("warning", "No bubbles found on this page.")]
    assert run_svc._detector_note("ml", no_bubbles) == ("info", "Detector: ML model.")
    fell_back = [("warning", "The ML detector failed: OSError: x\n\n"
                  + scanlate_detect.ML_FALLBACK_SENTENCE)]
    assert run_svc._detector_note("ml", fell_back) == (
        "info", "Detector: OpenCV heuristic (the ML detector could not run).")


@pytest.mark.parametrize("failure", [ImportError("no transformers"), OSError("bad weights")])
def test_every_ml_fallback_message_carries_the_fallback_sentence(monkeypatch, failure):
    def ml(*a, **k):
        raise failure
    monkeypatch.setattr(scanlate_detect, "detect_bubbles_ml", ml)
    monkeypatch.setattr(scanlate_detect, "_ml_failure", None)
    with pytest.raises(scanlate_detect.BubbleModelUnavailable) as caught:
        scanlate_detect.detect_bubbles("page.png", backend="ml")
    assert scanlate_detect.ML_FALLBACK_SENTENCE in str(caught.value)


def test_a_timed_out_join_stops_the_run_instead_of_keeping_quiet(monkeypatch):
    """A timed-out request still holds the job's slot, so swallowing it would
    leave the next page refused and written up as a failure."""
    from engine_backends.shared import LLMTaskTimeout

    def hangs(*a, **k):
        raise LLMTaskTimeout("did not answer")
    monkeypatch.setattr(scanlate, "translate_page_bubbles", hangs)
    upper = _region("世界", kind="caption", y=PAGE_H - 20, h=20, translated_text="World")
    lower = _region("破裂", kind="caption", y=0, h=20, translated_text="shatters")
    with pytest.raises(LLMTaskTimeout):
        scanlate.join_caption_across_pages({"id": 1, "bubbles": [upper], "height": PAGE_H},
                                           [lower], _MT(), {}, "", None, None)
    assert (upper["translated_text"], lower["translated_text"]) == ("World", "shatters")


class TestMergeFragmentBoxes:
    @staticmethod
    def _box(x, y, w, h, label=None, conf=0.5, **kw):
        b = {"x": x, "y": y, "w": w, "h": h, "confidence": conf}
        if label is not None:
            b["label"] = label
        b.update(kw)
        return b

    def test_same_label_heavy_overlap_merges_into_the_union_keeping_best_label(self):
        a = self._box(0, 0, 100, 100, "text_bubble", 0.9)
        b = self._box(10, 0, 100, 100, "text_bubble", 0.6)
        out = scanlate_detect.merge_fragment_boxes([a, b])
        assert len(out) == 1
        assert (out[0]["x"], out[0]["y"], out[0]["w"], out[0]["h"]) == (0, 0, 110, 100)
        assert out[0]["label"] == "text_bubble" and out[0]["confidence"] == 0.9

    def test_five_character_boxes_in_a_column_become_one_tall_box(self):
        col = [self._box(50, 10 + i * 34, 30, 30, "text_free", 0.7) for i in range(5)]
        out = scanlate_detect.merge_fragment_boxes(col)
        assert len(out) == 1
        assert (out[0]["x"], out[0]["y"], out[0]["w"], out[0]["h"]) == (50, 10, 30, 4 * 34 + 30)

    def test_closely_stacked_bubbles_stay_separate(self):
        a = self._box(0, 0, 100, 50, "bubble")
        b = self._box(0, 55, 100, 50, "bubble")
        assert len(scanlate_detect.merge_fragment_boxes([a, b])) == 2

    def test_different_labels_do_not_merge(self):
        a = self._box(0, 0, 100, 100, "bubble")
        b = self._box(0, 0, 100, 100, "text_free")
        assert len(scanlate_detect.merge_fragment_boxes([a, b])) == 2

    def test_unlabeled_boxes_merge_on_overlap_but_not_adjacency(self):
        overlapping = [self._box(0, 0, 100, 100), self._box(20, 0, 100, 100)]
        adjacent = [self._box(0, 0, 30, 30), self._box(0, 34, 30, 30)]
        assert len(scanlate_detect.merge_fragment_boxes(overlapping)) == 1
        assert len(scanlate_detect.merge_fragment_boxes(adjacent)) == 2

    def test_result_order_follows_the_first_member_of_each_group(self):
        a = self._box(0, 0, 100, 100, "bubble", tag="a")
        far = self._box(500, 0, 50, 50, "bubble", tag="far")
        a2 = self._box(5, 0, 100, 100, "bubble", conf=0.9, tag="a2")
        out = scanlate_detect.merge_fragment_boxes([a, far, a2])
        assert [b["tag"] for b in out] == ["a2", "far"]
        assert out[0]["x"] == 0 and out[0]["w"] == 105


def _engines(**usable):
    return [{"name": n, "label": n.title(), "free": n == "ollama", "key_configured": ok}
            for n, ok in usable.items()]


def test_default_engine_is_the_cheapest_usable_when_none_was_saved(isolated_db):
    from services import scanlate_pages_service as svc
    assert svc.default_engine(_engines(claude=True, openai=True, deepseek=True)) == "deepseek"
    assert svc.default_engine(_engines(claude=True, deepseek=True, ollama=True)) == "ollama"


def test_default_engine_honours_a_usable_saved_choice(isolated_db):
    from services import scanlate_pages_service as svc, settings_service
    settings_service.set_settings({"default_engine": "claude"})
    assert svc.default_engine(_engines(claude=True, deepseek=True)) == "claude"


def test_default_engine_ignores_a_saved_choice_that_cannot_run(isolated_db):
    from services import scanlate_pages_service as svc, settings_service
    settings_service.set_settings({"default_engine": "claude"})
    assert svc.default_engine(_engines(claude=False, openai=True, deepseek=True)) == "deepseek"


def test_default_engine_is_empty_when_nothing_can_run(isolated_db):
    from services import scanlate_pages_service as svc
    assert svc.default_engine(_engines(claude=False, deepseek=False)) == "claude"
    assert svc.default_engine([]) == "claude"
