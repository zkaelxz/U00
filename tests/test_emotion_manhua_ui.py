"""
tests/test_emotion_manhua_ui.py -- emotion tagging, manhua/webtoon
handling, and the UI design primitives.
"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np
cv2 = pytest.importorskip("cv2")  # requirements-media.txt, not core -- skip cleanly without it
import emotion as em
import scanlate
import ui_theme as ui
from core import Line


class PureMT:
    supports_reference = False


class TestEmotionGuidance:
    def test_neutral_lines_excluded(self):
        g = em.build_emotion_guidance({0: {"emotion": "neutral", "intensity": 0.9, "note": ""}}, [0])
        assert g == ""

    def test_low_intensity_excluded(self):
        g = em.build_emotion_guidance({0: {"emotion": "sarcastic", "intensity": 0.1, "note": ""}}, [0])
        assert g == ""

    def test_charged_line_included_with_register_guidance(self):
        g = em.build_emotion_guidance(
            {0: {"emotion": "suppressed_anger", "intensity": 0.8, "note": ""}}, [0])
        assert "Line 1" in g and "suppressed_anger" in g
        assert "clipped" in g

    def test_sarcasm_guidance_warns_against_softening(self):
        g = em.build_emotion_guidance(
            {0: {"emotion": "sarcastic", "intensity": 0.9, "note": ""}}, [0])
        assert "soften" in g.lower() or "opposite" in g.lower()

    def test_empty_map_and_empty_indices(self):
        assert em.build_emotion_guidance({}, [0]) == ""
        assert em.build_emotion_guidance({0: {"emotion": "angry", "intensity": 1.0}}, []) == ""

    def test_note_is_included(self):
        g = em.build_emotion_guidance(
            {0: {"emotion": "angry", "intensity": 0.9, "note": "shouting"}}, [0])
        assert "shouting" in g


class TestEmotionSummary:
    def test_counts_and_high_risk(self):
        s = em.emotion_summary({
            0: {"emotion": "sarcastic", "intensity": 0.9},
            1: {"emotion": "neutral", "intensity": 0.5},
            2: {"emotion": "flirtatious", "intensity": 0.8},
        })
        assert s["total"] == 3 and s["high_risk"] == 2

    def test_empty(self):
        assert em.emotion_summary({})["total"] == 0

    def test_pure_mt_returns_no_tags(self):
        assert em.detect_emotions([Line(idx=0, start=0, end=1, zh="a")], PureMT()) == {}


class _FakeTextBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class FakeClaudeLikeEngine:
    """A Claude-shaped fake (client.messages.create), matching what
    _call_llm dispatches on -- returns one tag per line in the batch, all
    'neutral', regardless of prompt content."""
    supports_reference = True
    model = "fake-model"

    def __init__(self):
        self.client = self
        self.messages = self  # so hasattr(engine.client, "messages") is True
        self.call_count = 0

    def create(self, model, max_tokens, messages):
        self.call_count += 1
        prompt = messages[0]["content"]
        import re as _re
        idxs = [int(m) for m in _re.findall(r"\[(\d+)\]", prompt)]
        tagged = [{"line_idx": i, "emotion": "neutral", "intensity": 0.5} for i in idxs]
        import json as _json
        return type("Resp", (), {"content": [_FakeTextBlock(_json.dumps(tagged))]})()


class TestDetectEmotionsProgress:
    def test_progress_cb_called_once_per_batch(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(10)]
        seen = []
        em.detect_emotions(lines, FakeClaudeLikeEngine(), batch_size=3, progress_cb=seen.append)
        # 10 lines at batch_size=3 -> 4 batches (3,3,3,1)
        assert seen == [0.25, 0.5, 0.75, 1.0]

    def test_progress_cb_is_optional(self):
        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(3)]
        result = em.detect_emotions(lines, FakeClaudeLikeEngine(), batch_size=10)
        assert len(result) == 3

    def test_progress_still_advances_when_a_batch_fails_to_parse(self):
        class BrokenEngine(FakeClaudeLikeEngine):
            def create(self, model, max_tokens, messages):
                self.call_count += 1
                return type("Resp", (), {"content": [_FakeTextBlock("not valid json")]})()

        lines = [Line(idx=i, start=0, end=1, zh=f"line{i}") for i in range(6)]
        seen = []
        result = em.detect_emotions(lines, BrokenEngine(), batch_size=3, progress_cb=seen.append)
        assert seen == [0.5, 1.0]  # both batches counted as attempted
        assert result == {}  # neither batch's garbage response parsed


class TestEmotionTagDescriptions:
    def test_all_tags_have_descriptions(self):
        for k, v in em.EMOTION_TAGS.items():
            assert isinstance(v, str) and v


class TestManhuaPanels:
    def _page(self, d):
        page = np.full((800, 600, 3), 255, dtype=np.uint8)
        cv2.rectangle(page, (20, 20), (580, 380), (0, 0, 0), 4)
        cv2.rectangle(page, (20, 420), (580, 780), (0, 0, 0), 4)
        p = os.path.join(d, "page.png")
        cv2.imwrite(p, page)
        return p

    def test_detects_panels(self):
        d = tempfile.mkdtemp()
        try:
            assert len(scanlate.detect_panels(self._page(d))) >= 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_file_raises(self):
        try:
            scanlate.detect_panels("/nonexistent/x.png")
            assert False, "should raise"
        except ValueError:
            pass


class TestWebtoonSlicing:
    def _strip(self, d, h=5000):
        strip = np.full((h, 800, 3), 255, dtype=np.uint8)
        for band in range(0, h, 900):
            cv2.rectangle(strip, (50, band + 50), (750, min(band + 700, h - 1)), (120, 120, 120), -1)
        p = os.path.join(d, "strip.png")
        cv2.imwrite(p, strip)
        return p

    def test_tall_strip_is_sliced(self):
        d = tempfile.mkdtemp()
        try:
            assert len(scanlate.split_webtoon_strip(self._strip(d), target_height=1600)) > 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_slices_cover_entire_strip(self):
        d = tempfile.mkdtemp()
        try:
            sl = scanlate.split_webtoon_strip(self._strip(d, 5000), target_height=1600)
            assert sl[0]["y_start"] == 0
            assert sl[-1]["y_end"] == 5000
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_all_slices_have_positive_height(self):
        d = tempfile.mkdtemp()
        try:
            for s in scanlate.split_webtoon_strip(self._strip(d), target_height=1600):
                assert s["y_end"] > s["y_start"]
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_short_image_not_sliced(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "s.png")
            cv2.imwrite(p, np.full((500, 400, 3), 255, dtype=np.uint8))
            assert len(scanlate.split_webtoon_strip(p, target_height=1600)) == 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

class TestTextRegionClassification:
    def test_returns_valid_kind(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "p.png")
            img = np.full((400, 400, 3), 255, dtype=np.uint8)
            cv2.rectangle(img, (50, 50), (350, 150), (255, 255, 255), -1)
            cv2.rectangle(img, (50, 50), (350, 150), (0, 0, 0), 3)
            cv2.imwrite(p, img)
            out = scanlate.classify_text_regions(p, [{"x": 50, "y": 50, "w": 300, "h": 100}])
            assert out[0]["kind"] in scanlate.TEXT_REGION_KINDS
            assert 0.0 <= out[0]["kind_confidence"] <= 1.0
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_missing_file_defaults_to_bubble(self):
        out = scanlate.classify_text_regions("/nope/x.png", [{"x": 0, "y": 0, "w": 10, "h": 10}])
        assert out[0]["kind"] == "bubble"

    def test_all_kinds_documented(self):
        for k, v in scanlate.TEXT_REGION_KINDS.items():
            assert isinstance(v, str) and v

    def test_font_style_sampling_returns_suggestion(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "p.png")
            cv2.imwrite(p, np.full((200, 200, 3), 255, dtype=np.uint8))
            s = scanlate.sample_text_style(p, {"x": 10, "y": 10, "w": 100, "h": 50})
            assert s["suggested_style"] in ("handwritten", "bold", "regular")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_font_sampling_missing_file_fallback(self):
        s = scanlate.sample_text_style("/nope/x.png", {"x": 0, "y": 0, "w": 5, "h": 5})
        assert s["weight"] == "regular"


class TestUiPrimitives:
    def test_status_pill_uses_status_colour(self):
        h = ui.status_pill("translated")
        assert "bh-pill" in h and ui.STATUS_COLORS["translated"][0] in h

    def test_unknown_status_keeps_label_neutral_colour(self):
        h = ui.status_pill("bogus")
        assert "bogus" in h and ui.STATUS_COLORS["not started"][0] in h

    def test_all_statuses_have_colours(self):
        for k, (fg, bg) in ui.STATUS_COLORS.items():
            assert fg.startswith("#") and bg.startswith("#")

    def test_stage_mapping(self):
        assert ui.stage_for_drama({"status": "not started"}, False) == 0
        assert ui.stage_for_drama({"status": "not started", "audio_filename": "a"}, False) == 1
        assert ui.stage_for_drama({"status": "aligned"}, True) == 2
        assert ui.stage_for_drama({"status": "translated"}, True) == 3
        assert ui.stage_for_drama({"status": "dubbed"}, True) == 4

    def test_stage_handles_none_drama(self):
        assert ui.stage_for_drama(None, False) == 0


class TestReaderFollowAlong:
    """Highlighting the line that's currently playing has to happen in the
    page itself -- Streamlit can't observe an <audio> element's position.
    These check the generated markup carries what the JS needs."""

    def _stub_segment(self, monkeypatch):
        # Patches the real segment module's function (reverted automatically
        # by the monkeypatch fixture after each test) rather than replacing
        # sys.modules["segment"] wholesale -- that used to leak a fake,
        # incomplete module into every OTHER test file that imports segment
        # for the rest of the pytest session, since a raw sys.modules
        # assignment isn't undone by anything.
        import segment
        monkeypatch.setattr(segment, "segment_and_annotate",
                             lambda text, lang, chinese_script="simplified": [(text, "pinyin")])

    def _lines(self, start=0.0):
        from core import Line
        return [Line(idx=i, start=start + i * 3, end=start + i * 3 + 3,
                     zh=f"第{i}句", en=f"line {i}") for i in range(4)]

    def test_rows_carry_timing_data(self, monkeypatch):
        import reader
        self._stub_segment(monkeypatch)
        html = reader.build_reader_html(self._lines(), "zh", {})
        assert 'data-start=' in html and 'data-end=' in html

    def test_no_follow_bar_without_audio(self, monkeypatch):
        import reader
        self._stub_segment(monkeypatch)
        html = reader.build_reader_html(self._lines(), "zh", {})
        assert '<div id="followbar"' not in html

    def test_follow_bar_and_listener_present_with_audio(self, monkeypatch):
        import reader
        self._stub_segment(monkeypatch)
        html = reader.build_reader_html(self._lines(), "zh", {},
                                         audio_data_uri="data:audio/mp3;base64,AAA")
        assert '<div id="followbar"' in html
        assert "timeupdate" in html
        assert "scrollIntoView" in html

    def test_offset_is_zero_for_the_first_page(self, monkeypatch):
        import reader
        self._stub_segment(monkeypatch)
        html = reader.build_reader_html(self._lines(0.0), "zh", {},
                                         audio_data_uri="data:audio/mp3;base64,AAA")
        assert "PAGE_START_OFFSET = 0.0" in html

    def test_offset_matches_a_mid_drama_page(self, monkeypatch):
        # The embedded clip covers only this page, so its t=0 is the page's
        # first line -- absolute timestamps need that offset added back.
        import reader
        self._stub_segment(monkeypatch)
        html = reader.build_reader_html(self._lines(600.0), "zh", {},
                                         audio_data_uri="data:audio/mp3;base64,AAA")
        assert "PAGE_START_OFFSET = 600.0" in html

    def test_active_line_styling_in_every_theme(self, monkeypatch):
        import reader
        self._stub_segment(monkeypatch)
        for theme in ("light", "sepia", "dark"):
            html = reader.build_reader_html(self._lines(), "zh", {}, theme=theme,
                                             audio_data_uri="data:audio/mp3;base64,A")
            assert ".line-row.playing" in html


class TestDarkModeCoverage:
    """The dark toggle only covered a subset of what the app actually uses
    -- checkboxes, radios, the toggle itself, sliders, file uploaders, and
    critically the success/info/warning/error alert boxes (used constantly
    throughout the app) had no dark styling at all, leaving them white
    against a dark background."""

    def test_covers_every_widget_type_the_app_actually_uses(self):
        import inspect
        import ui_theme as ui
        src = inspect.getsource(ui.inject_dark_css)
        required = [
            "stAlert", "stCheckbox", "stRadio", "stToggle", "stSlider",
            "stFileUploaderDropzone", "stProgress", "popover",
            "stChatMessage", "stChatInput", "stDataEditor",
        ]
        missing = [r for r in required if r not in src]
        assert missing == [], f"still uncovered: {missing}"

    def test_previously_covered_selectors_still_present(self):
        # Regression guard: expanding the sheet must not have dropped
        # anything that was already working.
        import inspect
        import ui_theme as ui
        src = inspect.getsource(ui.inject_dark_css)
        for required in ("stExpander", "stMetric", "stTextInput", "stButton",
                         "stTabs", "stDataFrame"):
            assert required in src, f"regression: {required} was dropped"

    def test_dark_palette_has_all_expected_keys(self):
        import ui_theme as ui
        for key in ("bg", "surface", "ink", "muted", "border", "accent", "accent_soft"):
            assert key in ui.DARK
            assert ui.DARK[key].startswith("#")


class TestHfTokenInScanlateMlDetector:
    """The Whisper model download was fixed to use an HF token earlier,
    but the Scanlate ML bubble detector's own huggingface_hub call was a
    separate code path that never got the same fix -- same warning,
    different function, easy to miss without checking both.

    Step 11 real fix: detect_bubbles_ml() used to load its checkpoint
    through ultralytics.YOLO, but the model is RT-DETR-v2, which
    ultralytics can never load -- these tests stub `torch` and
    `transformers` instead, matching the corrected implementation."""

    def _stub_torch_and_transformers(self, monkeypatch, tmp_path):
        # monkeypatch.setitem, not a raw sys.modules[...] = assignment --
        # a permanent replacement here would leak into every later test in
        # the same process. monkeypatch restores the real module
        # automatically once this test ends.
        import sys, types
        import contextlib
        calls = {}

        fake_torch = types.ModuleType("torch")
        fake_torch.no_grad = lambda: contextlib.nullcontext()
        fake_torch.tensor = lambda x: x
        monkeypatch.setitem(sys.modules, "torch", fake_torch)

        class FakeProcessor:
            def __call__(self, images, return_tensors):
                return {}

            def post_process_object_detection(self, outputs, threshold, target_sizes):
                return [{"scores": [], "labels": [], "boxes": []}]

        class FakeModel:
            config = types.SimpleNamespace(id2label={})

            def eval(self):
                pass

            def __call__(self, **kwargs):
                return None

        fake_transformers = types.ModuleType("transformers")

        class FakeAutoImageProcessor:
            @staticmethod
            def from_pretrained(repo_id, token=None):
                calls["processor_token"] = token
                return FakeProcessor()

        class FakeAutoModelForObjectDetection:
            @staticmethod
            def from_pretrained(repo_id, token=None):
                calls["model_token"] = token
                return FakeModel()

        fake_transformers.AutoImageProcessor = FakeAutoImageProcessor
        fake_transformers.AutoModelForObjectDetection = FakeAutoModelForObjectDetection
        monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

        # A real (tiny) image -- unlike the old ultralytics path, this one
        # genuinely opens the file with PIL before doing anything else.
        from PIL import Image
        img_path = tmp_path / "fake.png"
        Image.new("RGB", (10, 10)).save(img_path)
        calls["img_path"] = str(img_path)
        return calls

    def test_explicit_token_argument_reaches_the_download(self, monkeypatch, tmp_path):
        import os
        calls = self._stub_torch_and_transformers(monkeypatch, tmp_path)
        import scanlate
        os.environ.pop("HF_TOKEN", None)
        scanlate.__dict__.pop("_bubble_ml_model", None)
        scanlate.__dict__.pop("_bubble_ml_processor", None)
        scanlate.detect_bubbles_ml(calls["img_path"], hf_token="explicit-token")
        assert calls["processor_token"] == "explicit-token"
        assert calls["model_token"] == "explicit-token"

    def test_falls_back_to_an_already_set_environment_token(self, monkeypatch, tmp_path):
        import os
        calls = self._stub_torch_and_transformers(monkeypatch, tmp_path)
        import scanlate
        scanlate.__dict__.pop("_bubble_ml_model", None)
        scanlate.__dict__.pop("_bubble_ml_processor", None)
        os.environ["HF_TOKEN"] = "env-token"
        scanlate.detect_bubbles_ml(calls["img_path"])
        assert calls["processor_token"] == "env-token"
        assert calls["model_token"] == "env-token"
        os.environ.pop("HF_TOKEN", None)

    def test_no_token_available_does_not_crash(self, monkeypatch, tmp_path):
        calls = self._stub_torch_and_transformers(monkeypatch, tmp_path)
        import scanlate
        import os
        os.environ.pop("HF_TOKEN", None)
        scanlate.__dict__.pop("_bubble_ml_model", None)
        scanlate.__dict__.pop("_bubble_ml_processor", None)
        scanlate.detect_bubbles_ml(calls["img_path"])  # must not raise
        assert calls["processor_token"] is None
        assert calls["model_token"] is None
