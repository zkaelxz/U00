"""
tests/test_ocr.py -- ocr.py's resolve_tesseract_lang(), the Simplified/
Traditional Chinese language-pack selection.

Whisper transcription and LLM translation don't care which Chinese script
the source uses, but Tesseract needs the right language pack (chi_sim vs
chi_tra) or it OCRs Traditional text badly -- these lock in that the
selection logic picks the right one and leaves Japanese/Korean untouched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import ocr


class TestResolveTesseractLang:
    def test_simplified_chinese_default(self):
        assert ocr.resolve_tesseract_lang("zh") == "chi_sim"

    def test_simplified_explicit(self):
        assert ocr.resolve_tesseract_lang("zh", chinese_script="simplified") == "chi_sim"

    def test_traditional_chinese(self):
        assert ocr.resolve_tesseract_lang("zh", chinese_script="traditional") == "chi_tra"

    def test_japanese_ignores_chinese_script(self):
        assert ocr.resolve_tesseract_lang("ja", chinese_script="traditional") == "jpn"

    def test_korean_ignores_chinese_script(self):
        assert ocr.resolve_tesseract_lang("ko", chinese_script="traditional") == "kor"

    def test_unknown_language_falls_back_to_chi_sim(self):
        assert ocr.resolve_tesseract_lang("xx") == "chi_sim"


class TestExtractTextTesseractUsesExplicitPSM:
    """Regression test for a real bug found via direct testing: with no
    explicit config=, pytesseract.image_to_string() falls back to
    Tesseract's own default page-segmentation mode (PSM 3, "fully
    automatic page segmentation"), which is tuned for a whole scanned
    page -- not a small, pre-cropped single region like a hardsub caption
    band or a page-scan chunk. Confirmed by direct testing that PSM 3 can
    garble short single-line CJK text that PSM 6 ("single uniform block
    of text") reads correctly. Locks in that the explicit PSM is actually
    sent to Tesseract, and can still be overridden by a caller that knows
    better (e.g. PSM 7 for a caption region known to be a single line).
    """
    def _blank_image(self, tmp_path):
        from PIL import Image
        path = tmp_path / "blank.png"
        Image.new("L", (10, 10), color=255).save(path)
        return str(path)

    def test_default_psm_is_six(self, monkeypatch, tmp_path):
        pytesseract = pytest.importorskip("pytesseract")
        captured = {}

        def fake_image_to_string(image, lang=None, config=None):
            captured["lang"] = lang
            captured["config"] = config
            return "text"

        monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
        ocr.extract_text_tesseract(self._blank_image(tmp_path), lang="jpn")

        assert captured["lang"] == "jpn"
        assert captured["config"] == "--psm 6"

    def test_psm_can_be_overridden(self, monkeypatch, tmp_path):
        pytesseract = pytest.importorskip("pytesseract")
        captured = {}

        def fake_image_to_string(image, lang=None, config=None):
            captured["config"] = config
            return "text"

        monkeypatch.setattr(pytesseract, "image_to_string", fake_image_to_string)
        ocr.extract_text_tesseract(self._blank_image(tmp_path), psm=7)

        assert captured["config"] == "--psm 7"


class TestExtractTextTesseractCustomBinaryPath:
    """On Windows, the Tesseract installer doesn't always add itself to
    PATH, and pytesseract has no way to tell that apart from Tesseract
    genuinely not being installed -- both raise the same
    TesseractNotFoundError. tesseract_cmd lets a person point at the
    binary directly (Settings -> OCR) instead of editing a system PATH
    variable by hand."""

    def _blank_image(self, tmp_path):
        from PIL import Image
        path = tmp_path / "blank.png"
        Image.new("L", (10, 10), color=255).save(path)
        return str(path)

    def test_uses_tesseract_cmd_for_the_call_then_restores_it(self, monkeypatch, tmp_path):
        """Security review (PR #439): the path is set for this call only, so
        one run's binary never stays process-wide for later runs."""
        pytesseract = pytest.importorskip("pytesseract")
        seen = []
        monkeypatch.setattr(pytesseract, "image_to_string",
                            lambda *a, **k: seen.append(pytesseract.pytesseract.tesseract_cmd) or "text")
        monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract", raising=False)

        ocr.extract_text_tesseract(self._blank_image(tmp_path),
                                    tesseract_cmd=r"C:\Program Files\Tesseract-OCR\tesseract.exe")

        assert seen == [r"C:\Program Files\Tesseract-OCR\tesseract.exe"]
        assert pytesseract.pytesseract.tesseract_cmd == "tesseract"

    def test_restores_tesseract_cmd_when_ocr_raises(self, monkeypatch, tmp_path):
        pytesseract = pytest.importorskip("pytesseract")

        def boom(*a, **k):
            raise RuntimeError("x")
        monkeypatch.setattr(pytesseract, "image_to_string", boom)
        monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract", raising=False)

        with pytest.raises(RuntimeError):
            ocr.extract_text_tesseract(self._blank_image(tmp_path), tesseract_cmd="/evil")

        assert pytesseract.pytesseract.tesseract_cmd == "tesseract"

    def test_leaves_default_tesseract_cmd_alone_when_not_given(self, monkeypatch, tmp_path):
        pytesseract = pytest.importorskip("pytesseract")
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "text")
        monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract", raising=False)

        ocr.extract_text_tesseract(self._blank_image(tmp_path))

        assert pytesseract.pytesseract.tesseract_cmd == "tesseract"


class TestExtractTextPaddleUsesCurrentAPI:
    """Regression test for a real bug found via direct testing against an
    actually-installed paddleocr: `pip install paddleocr` today gives 3.x,
    which dropped use_angle_cls and the .ocr(path, cls=True) call entirely
    (TypeError: PaddleOCR.predict() got an unexpected keyword argument
    'cls') and changed the result shape from
    [[(box, (text, confidence)), ...]] per page to a list of dict-like
    result objects with a rec_texts list. Locks in the 3.x call shape so
    this doesn't silently rot again the next time the package updates.
    """
    @pytest.fixture(autouse=True)
    def reset_paddle_instance(self):
        ocr.__dict__.pop("_paddle_instances", None)
        yield
        ocr.__dict__.pop("_paddle_instances", None)

    def test_calls_predict_not_the_removed_ocr_cls_api(self, monkeypatch):
        try:
            paddleocr = pytest.importorskip("paddleocr")  # optional, heavy dependency
        except RuntimeError as exc:
            # A real, previously-masked environment conflict: paddleocr's
            # own import chain (paddlex -> modelscope) imports torch, and
            # if some earlier import already triggered torch's own
            # `_TritonLibrary` global (native, process-wide) registration,
            # a second registration attempt inside THIS import raises
            # RuntimeError instead of the ImportError pytest.importorskip
            # actually catches -- an installed-package version conflict in
            # this environment, not a regression in this file's own
            # `.predict()` call shape (what this test actually checks).
            if "TORCH_LIBRARY" in str(exc):
                pytest.skip(f"paddleocr's import chain hit a torch library conflict "
                           f"in this environment: {exc}")
            raise
        captured = {}

        class FakePaddleOCR:
            def __init__(self, **kwargs):
                captured["init_kwargs"] = kwargs

            def predict(self, image_path):
                captured["predict_path"] = image_path
                return [{"rec_texts": ["你好", "世界"]}]

        monkeypatch.setattr(paddleocr, "PaddleOCR", FakePaddleOCR)

        result = ocr.extract_text_paddle("/fake/page.png")

        assert result == "你好\n世界"
        assert captured["predict_path"] == "/fake/page.png"
        assert "use_angle_cls" not in captured["init_kwargs"]


class TestExtractTextFromImagesUsesResolvedLang:
    def test_traditional_script_selects_chi_tra_backend(self, monkeypatch):
        captured = {}

        def fake_tesseract(image_path, lang="chi_sim", tesseract_cmd=None):
            captured["lang"] = lang
            return "extracted text"

        monkeypatch.setattr(ocr, "extract_text_tesseract", fake_tesseract)
        result = ocr.extract_text_from_images(
            ["/fake/page1.png"], backend="tesseract", source_language="zh",
            chinese_script="traditional")

        assert captured["lang"] == "chi_tra"
        assert result == "extracted text"

    def test_simplified_is_the_default(self, monkeypatch):
        captured = {}

        def fake_tesseract(image_path, lang="chi_sim", tesseract_cmd=None):
            captured["lang"] = lang
            return "x"

        monkeypatch.setattr(ocr, "extract_text_tesseract", fake_tesseract)
        ocr.extract_text_from_images(["/fake/page1.png"], source_language="zh")

        assert captured["lang"] == "chi_sim"

    def test_paddle_backend_ignores_chinese_script(self, monkeypatch):
        # PaddleOCR's "ch" model handles both scripts itself -- chinese_script
        # shouldn't need to (and doesn't) affect which function runs.
        called = []
        monkeypatch.setattr(ocr, "extract_text_paddle",
                             lambda p, lang="ch": called.append((p, lang)) or "x")
        ocr.extract_text_from_images(["/fake/page1.png"], backend="paddle",
                                       source_language="zh", chinese_script="traditional")
        assert called == [("/fake/page1.png", "ch")]

    def test_paddle_backend_uses_korean_model_for_korean_source(self, monkeypatch):
        # Step 11 item 4: Korean's own default backend is paddle, not
        # Tesseract -- but it needs PaddleOCR's Korean-language model,
        # not the Chinese one "paddle" used to mean unconditionally.
        called = []
        monkeypatch.setattr(ocr, "extract_text_paddle",
                             lambda p, lang="ch": called.append((p, lang)) or "x")
        ocr.extract_text_from_images(["/fake/page1.png"], backend="paddle",
                                       source_language="ko")
        assert called == [("/fake/page1.png", "korean")]

    def test_paddle_vl_manga_backend_is_dispatched(self, monkeypatch):
        called = []
        monkeypatch.setattr(ocr, "extract_text_paddle_vl_manga",
                             lambda p: called.append(p) or "x")
        result = ocr.extract_text_from_images(["/fake/page1.png"], backend="paddle_vl_manga",
                                                source_language="ja")
        assert called == ["/fake/page1.png"]
        assert result == "x"


class _FakeTensor:
    def __init__(self, rows):
        self.rows = rows
        self.shape = (len(rows), len(rows[0]))

    def __getitem__(self, key):
        _, cols = key
        return _FakeTensor([r[cols] for r in self.rows])


class _FakeInputs(dict):
    def to(self, device, dtype=None):
        self.moved_to = (device, dtype)
        return self


def _install_fake_transformers(monkeypatch, version="5.19.0"):
    import types
    calls = {}
    inputs = _FakeInputs(input_ids=_FakeTensor([[1, 2, 3]]))

    class FakeModel:
        device, dtype = "cpu", "float32"

        def eval(self):
            calls["eval"] = True

        def generate(self, **kwargs):
            calls["generate"] = kwargs
            return _FakeTensor([[1, 2, 3, 40, 41]])

    class FakeProcessor:
        def apply_chat_template(self, messages, **kwargs):
            calls["messages"], calls["template_kwargs"] = messages, kwargs
            return inputs

        def batch_decode(self, ids, skip_special_tokens):
            calls["decoded"] = ids.rows
            return ["  今日はいい天気ですね \n"]

    def load_processor(repo, **kwargs):
        calls["processor"] = (repo, kwargs)
        return FakeProcessor()

    def load_model(repo, **kwargs):
        calls["model"] = (repo, kwargs)
        return FakeModel()

    fake_tf = types.SimpleNamespace(
        AutoProcessor=types.SimpleNamespace(from_pretrained=load_processor),
        AutoModelForImageTextToText=types.SimpleNamespace(from_pretrained=load_model))
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False),
        bfloat16="bf16", float32="float32")
    monkeypatch.setitem(sys.modules, "transformers", fake_tf)
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setattr("importlib.metadata.version", lambda name: version)
    for name in ("_paddle_vl_manga_model", "_paddle_vl_manga_processor"):
        monkeypatch.delitem(vars(ocr), name, raising=False)
    return calls


class TestPaddleVlMangaNative:
    @pytest.fixture
    def page(self, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "bubble.png"
        Image.new("RGB", (8, 8), "white").save(path)
        return str(path)

    def test_loads_native_classes_without_remote_code(self, monkeypatch, page):
        calls = _install_fake_transformers(monkeypatch)
        ocr.extract_text_paddle_vl_manga(page)
        assert calls["processor"] == ("PaddlePaddle/PaddleOCR-VL", {"trust_remote_code": False})
        repo, kwargs = calls["model"]
        assert repo == "jzhang533/PaddleOCR-VL-For-Manga"
        assert kwargs["trust_remote_code"] is False
        assert calls["eval"]

    def test_builds_chat_template_input_and_decodes_only_new_tokens(self, monkeypatch, page):
        calls = _install_fake_transformers(monkeypatch)
        text = ocr.extract_text_paddle_vl_manga(page)
        content = calls["messages"][0]["content"]
        assert [part["type"] for part in content] == ["image", "text"]
        assert content[1]["text"] == "OCR:"
        assert calls["template_kwargs"]["add_generation_prompt"] is True
        assert calls["generate"]["max_new_tokens"] == 256
        assert calls["decoded"] == [[40, 41]]
        assert text == "今日はいい天気ですね"

    def test_model_is_cached_between_calls(self, monkeypatch, page):
        calls = _install_fake_transformers(monkeypatch)
        ocr.extract_text_paddle_vl_manga(page)
        calls.pop("model")
        ocr.extract_text_paddle_vl_manga(page)
        assert "model" not in calls

    def test_old_transformers_raises_plain_dependency_error(self, monkeypatch, page):
        from services.service_errors import DependencyUnavailableError
        _install_fake_transformers(monkeypatch, version="4.57.1")
        with pytest.raises(DependencyUnavailableError, match="transformers 5 or newer") as exc:
            ocr.extract_text_paddle_vl_manga(page)
        assert str(exc.value) == ocr.paddle_vl_manga_problem()

    def test_native_model_is_registered_in_installed_transformers(self):
        transformers = pytest.importorskip("transformers")
        if int(transformers.__version__.split(".")[0]) < 5:
            pytest.skip("transformers 5 ships paddleocr_vl; this install is older")
        from transformers.models.auto.modeling_auto import MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING_NAMES
        assert "paddleocr_vl" in MODEL_FOR_IMAGE_TEXT_TO_TEXT_MAPPING_NAMES
        assert hasattr(transformers, "PaddleOCRVLProcessor")
