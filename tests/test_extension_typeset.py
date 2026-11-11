"""The extension's "Clean and typeset" choice: a page the capture saves gets Scanlate's
render run on it, so Typeset is already on when the title opens. The render is mocked
except in one test that needs OpenCV."""
import pytest

import db
import page_server
from services import scanlate_render_service as render_svc

from tests.test_page_server import _b64, _png_bytes, _post, fake_pipeline  # noqa: F401
from tests.test_page_server import token, _reset_module_state  # noqa: F401


@pytest.fixture
def drama(isolated_db, monkeypatch):
    # Any engine object will do: the translate step is faked by fake_pipeline.
    monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
    return db.create_drama(title_en="Typeset me", media_type="manhua", source_language="zh")


def _send(token, drama_id, **extra):
    return _post(token, {"images": [{"data": _b64(_png_bytes()), "content_type": "image/png"}],
                         "drama_id": drama_id, "store": True, **extra})


def _recording_render(monkeypatch, result=None):
    calls = []

    def fake(drama_id, page_id, notes=None, cancel_check=None):
        calls.append((drama_id, page_id))
        return result or {"rendered": True, "blank": 0}

    monkeypatch.setattr(render_svc, "render_page", fake)
    return calls


def test_a_page_is_not_typeset_unless_asked(token, fake_pipeline, drama, monkeypatch):
    calls = _recording_render(monkeypatch)
    page = _send(token, drama).payload["pages"][0]
    assert calls == [] and "typeset" not in page


def test_a_saved_page_is_typeset_when_asked(token, fake_pipeline, drama, monkeypatch):
    calls = _recording_render(monkeypatch)
    handler = _send(token, drama, typeset=True)
    page = handler.payload["pages"][0]
    assert handler.status == 200
    assert calls == [(drama, page["page_id"])] and page["typeset"] is True


def test_nothing_is_typeset_when_the_page_is_not_saved(token, fake_pipeline, drama, monkeypatch):
    calls = _recording_render(monkeypatch)
    page = _post(token, {"images": [{"data": _b64(_png_bytes()), "content_type": "image/png"}],
                         "drama_id": drama, "store": False, "typeset": True}).payload["pages"][0]
    assert calls == [] and "typeset" not in page


def test_a_failed_render_keeps_the_page_and_its_text(token, fake_pipeline, drama, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("C:\\secret\\model.pt missing")

    monkeypatch.setattr(render_svc, "render_page", broken)
    page = _send(token, drama, typeset=True).payload["pages"][0]
    assert page["typeset"] is False and page["stored"] is True
    assert db.load_bubbles(page["page_id"])[0]["translated_text"] == "the translation"
    assert any("typeset failed" in n[1] for n in page["notes"])


def test_a_page_already_typeset_is_not_drawn_again(token, fake_pipeline, drama, monkeypatch):
    first = _send(token, drama).payload["pages"][0]
    db.update_page(first["page_id"], rendered_filename="pages/typeset_id1.png")
    calls = _recording_render(monkeypatch)
    again = _send(token, drama, typeset=True).payload["pages"][0]
    assert again["page_id"] == first["page_id"] and calls == []


def test_a_page_with_no_text_reports_it_was_not_typeset(token, fake_pipeline, drama, monkeypatch):
    _recording_render(monkeypatch, {"rendered": False, "blank": 0})
    assert _send(token, drama, typeset=True).payload["pages"][0]["typeset"] is False


def test_the_real_render_writes_the_typeset_image(token, fake_pipeline, drama):
    pytest.importorskip("cv2")
    page = _send(token, drama, typeset=True).payload["pages"][0]
    row = db.get_page(page["page_id"])
    assert page["typeset"] is True and row["rendered_filename"]


def test_regions_say_which_boxes_the_preview_must_not_paint_over_and_which_stayed_untranslated():
    import page_capture_checks as checks
    box = {"x": 1, "y": 2, "w": 3, "h": 4, "source_text": "술만", "kind": "bubble"}
    regions = checks.regions_for_response([
        {**box, "translated_text": "Only when drunk"},
        {**box, "translated_text": "술만마시면"},
        {**box, "translated_text": ""},
        {**box, "kind": "sfx", "translated_text": "Thud"},
        {**box, "skip": True, "translated_text": "Hi"},
    ])
    assert [r["untranslated"] for r in regions] == [False, True, True, False, False]
    assert [r["left_alone"] for r in regions] == [False, False, False, True, True]


def test_a_saved_page_with_no_text_keeps_a_note_saying_so(token, drama, monkeypatch):
    import scanlate
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: ([], []))
    page = _send(token, drama).payload["pages"][0]
    notes = db.get_page(page["page_id"])["run_notes"]
    assert "No text was found" in notes


def _writing_render(monkeypatch, size=(300, 500)):
    """A render that leaves a real typeset PNG in the drama folder, as the renderer does."""
    import os
    from PIL import Image

    def fake(drama_id, page_id, notes=None, cancel_check=None):
        folder = os.path.join(db.drama_dir(drama_id), "pages")
        os.makedirs(folder, exist_ok=True)
        Image.new("RGB", size, (200, 30, 30)).save(os.path.join(folder, f"typeset_id{page_id}.png"))
        db.update_page(page_id, rendered_filename=f"pages/typeset_id{page_id}.png")
        return {"rendered": True, "blank": 0}

    monkeypatch.setattr(render_svc, "render_page", fake)


def test_the_typeset_page_comes_back_as_a_jpeg_to_draw_over_the_reader(token, fake_pipeline, drama,
                                                                       monkeypatch):
    import base64
    import io
    from PIL import Image
    _writing_render(monkeypatch)
    page = _send(token, drama, typeset=True).payload["pages"][0]
    head, _, data = page["typeset_image"].partition(",")
    assert head == "data:image/jpeg;base64"
    assert Image.open(io.BytesIO(base64.b64decode(data))).size == (300, 500)


def test_an_oversized_typeset_page_stays_in_the_library_but_is_not_sent_back(
        token, fake_pipeline, drama, monkeypatch):
    from services import extension_bridge_service
    _writing_render(monkeypatch)
    monkeypatch.setattr(extension_bridge_service, "PREVIEW_MAX_BYTES", 10)
    page = _send(token, drama, typeset=True).payload["pages"][0]
    assert page["typeset"] is True and "typeset_image" not in page
    assert db.get_page(page["page_id"])["rendered_filename"]


def test_a_recapture_of_a_typeset_page_still_sends_its_image(token, fake_pipeline, drama, monkeypatch):
    _writing_render(monkeypatch)
    _send(token, drama, typeset=True)
    again = _send(token, drama, typeset=True).payload["pages"][0]
    assert again["already_stored"] is True and again["typeset_image"].startswith("data:image/jpeg")


def test_no_image_is_sent_when_typeset_is_off(token, fake_pipeline, drama, monkeypatch):
    _writing_render(monkeypatch)
    assert "typeset_image" not in _send(token, drama).payload["pages"][0]


def test_lines_the_engine_left_empty_are_asked_for_again_and_the_rest_are_counted(
        token, drama, monkeypatch):
    import scanlate
    import scanlate_translate
    boxes = [{"x": 1, "y": 2, "w": 30, "h": 40, "source_text": text, "translated_text": "",
              "kind": "bubble", "font_category": "regular", "reading_order": i}
             for i, text in enumerate(["잘가라 연다나", "안녕", "뭐라고"])]
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: ([dict(b) for b in boxes], []))
    calls = []

    def translate(bubbles, engine, drama_meta, **kwargs):
        calls.append([b["source_text"] for b in bubbles])
        # The first call answers one line; the retry answers one of the two still missing.
        target = next(b for b in bubbles if not b["translated_text"])
        target["translated_text"] = "Goodbye, Yeon Dana" if len(calls) == 1 else "Hello"
        raise ValueError("2 of 3 regions weren't translated")

    monkeypatch.setattr(scanlate_translate, "translate_page_bubbles", translate)
    page = _send(token, drama).payload["pages"][0]
    assert len(calls) == 2 and len(calls[1]) == 2
    assert page["untranslated_lines"] == 1
    assert any("still had no translation after a retry" in n[1] for n in page["notes"])
    assert [r["translated_text"] for r in page["regions"]].count("") == 1
