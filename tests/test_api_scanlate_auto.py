"""Automatic Scanlate path (docs/archive/scanlate-api-spec.md S1, S2, S5, S6,
S8): config and page detail, page import with its limits, the one-job-per-
drama detect/OCR/translate/render run, re-render and ZIP/PDF export.

Mocked: detection/OCR is a fake, translation is the fake engine or a stub
engine with a patched call_llm_json. Rendering and slicing run for real on
tiny synthetic images (OpenCV + Pillow, no models, no network)."""

import importlib.util
import io
import json
import os
import threading
import time
import zipfile

import pytest
from tests.saved_settings import patch_setting

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
PIL = pytest.importorskip("PIL")

# Only the tests that render or slice pages need OpenCV, which CI does not
# install; the id-matching tests must keep running there.
needs_cv2 = pytest.mark.skipif(importlib.util.find_spec("cv2") is None,
                               reason="OpenCV is not installed")

from fastapi.testclient import TestClient
from PIL import Image

import background_jobs
from jobs import job_table
import db
import ocr
import scanlate
import scanlate_render
import scanlate_translate
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import page_import_limits as limits
from services import scanlate_pages_service as pages_svc
from services import scanlate_run_service as run_svc
from lib.errors import ConflictError, InvalidInputError
from tests.job_helpers import wait_for_job

LOCAL = {"X-Baihe-Local": "1"}
_REAL_REQUIRE_BACKEND = ocr.require_backend


@pytest.fixture
def client(isolated_db, monkeypatch):
    # Dependency refusals have their own tests (test_scanlate_ocr_deps.py);
    # here the OCR backend is a fake that can always run.
    monkeypatch.setattr(ocr, "require_backend", lambda backend, cmd=None: None)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _png(w=60, h=40, color=(255, 255, 255), mode="RGB", fmt="PNG", **save):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, fmt, **save)
    return buf.getvalue()


def _drama(**kw):
    return db.create_drama(title_en="Comic", source_language="zh", **kw)


def _page(did, idx=0, w=120, h=80):
    folder = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(folder, exist_ok=True)
    name = f"page_{idx:04d}.png"
    with open(os.path.join(folder, name), "wb") as f:
        f.write(_png(w, h))
    return db.create_page(did, idx, f"pages/{name}", w, h)


def _put_job(job_id, status="running"):
    with job_table._lock:
        job_table._jobs[job_id] = {
            "status": status, "progress": 0.0, "message": "", "result": None,
            "error": None, "started_at": time.time(), "finished_at": None}


def _no_leak(resp):
    text = resp.text
    assert db.LIBRARY_DIR not in text and "pages/page_" not in text, text


def _region(x=0, text="你好", kind="bubble", **kw):
    b = {"x": x, "y": 10, "w": 40, "h": 30, "source_text": text, "translated_text": "",
         "kind": kind, "font_size": 12, "skip": False, "include_sfx": False,
         "language": "zh", "orientation": "horizontal", "panel_id": None,
         "kind_confidence": 0.5, "confidence": None, "font_category": "regular"}
    b.update(kw)
    return b


@pytest.fixture
def fake_detect(monkeypatch):
    """detect_and_ocr_page returns two speech regions and one SFX region."""
    calls = []

    def detect(image_path, lang, page_id=None, **kw):
        calls.append({"page_id": page_id, **kw})
        return [_region(5, "你好"), _region(50, "再见"), _region(80, "砰", kind="sfx")], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    return calls


# --- S1 ---------------------------------------------------------------------

def test_config_has_key_booleans_only(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secretvalue1234567890")
    did = _drama()
    _page(did)
    r = client.get(f"/api/scanlate/dramas/{did}/config")
    assert r.status_code == 200
    body = r.json()
    assert "sk-ant" not in r.text
    names = {e["name"]: e for e in body["engines"]}
    assert names["fake"]["key_configured"] is True
    assert set(names["claude"]) == {"name", "label", "free", "key_configured"}
    assert body["page_count"] == 1 and body["job_id"] == f"scanlate_{did}"
    assert body["upload_limits"]["max_image_mb"] == 30
    assert body["upload_limits"]["max_image_megapixels"] == 100
    assert body["upload_limits"]["max_files"] == 300
    assert body["default_engine"]
    _no_leak(r)


def test_config_names_the_auto_backend_and_whether_it_is_installed(client, monkeypatch):
    """Scanlate never swaps backends silently: a zh page under "auto" reports
    paddle even when it's missing, so the UI can say "(not installed)"."""
    did = _drama()
    present = {"paddleocr"}
    monkeypatch.setattr(pages_svc.importlib.util, "find_spec",
                        lambda name: object() if name in present else None)
    body = client.get(f"/api/scanlate/dramas/{did}/config").json()
    # paddleocr 3.x alone isn't enough: the paddlepaddle module is separate.
    assert body["ocr_backend"] == "paddle" and body["ocr_backend_installed"] is False
    present.add("paddle")
    body = client.get(f"/api/scanlate/dramas/{did}/config").json()
    assert body["ocr_backend"] == "paddle" and body["ocr_backend_installed"] is True


def test_page_detail_keyed_by_id_and_scoped(client):
    did, other = _drama(), _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, translated_text="Hi"), _region(2, kind="sfx")])
    db.update_page(pid, run_notes=json.dumps([{"level": "warning", "message": "m"}]))
    r = client.get(f"/api/scanlate/dramas/{did}/pages/{pid}")
    assert r.status_code == 200
    body = r.json()
    ids = [b["id"] for b in db.load_bubbles(pid)]
    assert [g["id"] for g in body["regions"]] == ids
    assert body["regions"][1]["kind"] == "sfx"      # the editor view keeps SFX
    assert body["rev"] == 0 and body["run_notes"] == [{"level": "warning", "message": "m"}]
    _no_leak(r)
    assert client.get(f"/api/scanlate/dramas/{other}/pages/{pid}").status_code == 404
    r404 = client.get(f"/api/scanlate/dramas/{did}/pages/99999")
    assert r404.status_code == 404, r404.text
    notes = client.get(f"/api/scanlate/dramas/{did}/run-notes").json()
    assert notes["pages"] == [{"page_id": pid, "ordinal": 1,
                               "notes": [{"level": "warning", "message": "m"}]}]


def test_clean_note_drops_url_queries():
    note = pages_svc.clean_note("Could not download for url: "
                                "https://cdn.example.com/a/config.json?token=abc123&x=1 (403)")
    assert "https://cdn.example.com/a/config.json" in note
    assert "token" not in note and "abc123" not in note
    assert "(403)" in note


def test_clean_note_strips_keys_and_paths():
    note = pages_svc.clean_note(
        "boom sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123 at /home/kae/library/dramas/1/x.png "
        r"and C:\Users\Kae\lib\p.png; see https://huggingface.co/x")
    assert "abcdefghijklmnop" not in note and "/home/kae" not in note and "Users" not in note
    assert "<path>" in note and "https://huggingface.co/x" in note


# --- S2 ---------------------------------------------------------------------

def _upload(client, did, files, slice_strips=None, headers=LOCAL):
    data = {} if slice_strips is None else {"slice_strips": "true" if slice_strips else "false"}
    return client.post(f"/api/scanlate/dramas/{did}/pages", headers=headers,
                       files=[("files", f) for f in files], data=data)


def test_upload_images_reencoded_and_named_by_idx(client):
    did = _drama()
    exif = Image.Exif()
    exif[0x0112] = 6                                    # rotate 90 on display
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(buf, "JPEG", exif=exif.tobytes())
    r = _upload(client, did, [("../../evil name.png", _png(30, 20), "image/png"),
                              ("photo.JPG", buf.getvalue(), "image/jpeg"),
                              ("w.webp", _png(10, 10, fmt="WEBP"), "image/webp")])
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 3
    pages = db.list_pages(did)
    assert [p["filename"] for p in pages] == ["pages/page_0000.png", "pages/page_0001.png",
                                              "pages/page_0002.png"]      # rotated JPEG -> PNG
    assert (pages[1]["width"], pages[1]["height"]) == (20, 40)     # EXIF applied
    with Image.open(os.path.join(db.drama_dir(did), pages[1]["filename"])) as im:
        assert im.size == (20, 40) and not im.getexif().get(0x0112)
    assert not any("evil" in n for n in os.listdir(os.path.join(db.drama_dir(did), "pages")))
    _no_leak(r)


def test_upload_rejects_bad_types_and_adds_nothing(client):
    did = _drama()
    for files in ([("a.gif", b"GIF89a....", "image/gif")],
                  [("a.png", b"%PDF-1.4 not an image", "image/png")],
                  [("a.png", b"\x89PNG\r\n\x1a\n garbage", "image/png")],
                  [("ok.png", _png(), "image/png"), ("bad.bmp", b"BM..", "image/bmp")],
                  [("empty.png", b"", "image/png")]):
        r = _upload(client, did, files)
        assert r.status_code == 422, (files[0][0], r.text)
    assert db.list_pages(did) == []


def test_pixel_cap_checked_before_decode(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(limits, "MAX_IMAGE_PIXELS", 1000)
    monkeypatch.setattr(limits, "_prepare",
                        lambda *a: (_ for _ in ()).throw(AssertionError("decoded")))
    r = _upload(client, did, [("big.png", _png(50, 50), "image/png")])
    assert r.status_code == 422 and "megapixel" in r.json()["error"]["message"]


def test_pages_are_written_through_the_pipeline_writer(isolated_db, monkeypatch):
    from sources import pipeline
    did = _drama()
    calls = []
    real = pipeline.add_page_images

    def spy(drama_id, images, ids_out=None):
        calls.append(drama_id)
        return real(drama_id, images, ids_out=ids_out)
    monkeypatch.setattr(pipeline, "add_page_images", spy)
    out = pages_svc.add_page_images(did, [("a.png", io.BytesIO(_png())),
                                          ("b.png", io.BytesIO(_png()))])
    assert calls == [did]
    assert out["page_ids"] == [p["id"] for p in db.list_pages(did)]
    assert pages_svc.upload_limits()["max_files"] == limits.MAX_FILES_PER_IMPORT


def test_size_count_and_total_caps(client, monkeypatch):
    did = _drama()
    monkeypatch.setattr(limits, "MAX_IMAGE_BYTES", 50)
    assert _upload(client, did, [("a.png", _png(200, 200, (1, 2, 3)), "image/png")]).status_code == 422
    monkeypatch.setattr(limits, "MAX_IMAGE_BYTES", 30 * 1024 * 1024)
    monkeypatch.setattr(limits, "MAX_FILES_PER_IMPORT", 2)
    three = [(f"{i}.png", _png(), "image/png") for i in range(3)]
    assert _upload(client, did, three).status_code == 422
    monkeypatch.setattr(limits, "MAX_FILES_PER_IMPORT", 300)
    monkeypatch.setattr(limits, "MAX_IMPORT_BYTES", 10)
    r = _upload(client, did, [("a.png", _png(), "image/png")])
    assert r.status_code in (413, 422)
    assert db.list_pages(did) == []


@needs_cv2
def test_tall_strip_sliced_by_default_and_optional(client):
    did = _drama()
    strip = _png(200, 2400)
    r = _upload(client, did, [("strip.png", strip, "image/png")])
    assert r.status_code == 200 and r.json()["strips_sliced"] == 1
    assert r.json()["added"] >= 2
    n = len(db.list_pages(did))
    r = _upload(client, did, [("strip.png", strip, "image/png")], slice_strips=False)
    assert r.json()["added"] == 1 and len(db.list_pages(did)) == n + 1


@needs_cv2
def test_a_strip_over_the_slice_cap_is_rejected_not_left_whole(monkeypatch):
    import scanlate_detect
    monkeypatch.setattr(scanlate_detect, "_MAX_WEBTOON_SLICES", 2)
    with pytest.raises(limits.ImageRejected, match="slice limit"):
        limits.prepare_page(_png(200, 6000))


def test_a_memory_error_while_slicing_is_not_swallowed(monkeypatch):
    def boom(*a, **k):
        raise MemoryError
    monkeypatch.setattr(scanlate, "slice_webtoon_to_files", boom)
    with pytest.raises(limits.ImageRejected, match="memory"):
        limits.prepare_page(_png(200, 2400))


def test_pdf_import(client, monkeypatch):
    pytest.importorskip("pypdf")
    did = _drama()
    buf = io.BytesIO()
    a, b = Image.new("RGB", (60, 80), "white"), Image.new("RGB", (60, 80), "gray")
    a.save(buf, "PDF", save_all=True, append_images=[b])
    r = _upload(client, did, [("book.pdf", buf.getvalue(), "application/pdf")])
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 2
    monkeypatch.setattr(limits, "MAX_PDF_PAGES", 1)
    assert _upload(client, did, [("book.pdf", buf.getvalue(),
                                  "application/pdf")]).status_code == 422
    monkeypatch.setattr(limits, "MAX_PDF_PAGES", 500)
    monkeypatch.setattr(limits, "MAX_IMAGE_PIXELS", 100)       # embedded image too big
    assert _upload(client, did, [("book.pdf", buf.getvalue(),
                                  "application/pdf")]).status_code == 422
    assert len(db.list_pages(did)) == 2


def test_idx_is_max_plus_one_and_concurrent_adds_are_safe(isolated_db):
    did = _drama()
    _page(did, idx=4)
    pages_svc.add_page_images(did, [("a.png", io.BytesIO(_png()))])
    assert [p["idx"] for p in db.list_pages(did)] == [4, 5]
    results, errors = [], []

    def add():
        try:
            results.append(pages_svc.add_page_images(did, [("a.png", io.BytesIO(_png()))]))
        except ConflictError as e:
            errors.append(e)
    threads = [threading.Thread(target=add) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    idxs = [p["idx"] for p in db.list_pages(did)]
    assert len(idxs) == len(set(idxs)) == 2 + len(results)


def test_upload_409_while_job_runs_and_job_409_while_uploading(client):
    did = _drama()
    _put_job(f"scanlate_{did}")
    assert _upload(client, did, [("a.png", _png(), "image/png")]).status_code == 409
    with job_table._lock:
        job_table._jobs.clear()
    with pages_svc.upload_claim(did):
        with pytest.raises(ConflictError):
            pages_svc.start_drama_job(did, lambda *a: None, description="x")


def test_upload_is_pc_only(isolated_db):
    did = _drama()
    remote = TestClient(create_app(ApiSettings(auth_mode="on")),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    r = remote.post(f"/api/scanlate/dramas/{did}/pages",
                    files={"files": ("a.png", _png(), "image/png")})
    assert r.status_code in (401, 403)
    # off mode: a multipart POST needs the local header (no-cors simple request)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    assert c.post(f"/api/scanlate/dramas/{did}/pages",
                  files={"files": ("a.png", _png(), "image/png")}).status_code == 403


def test_add_page_images_is_the_shared_entry_point(isolated_db):
    did = _drama()
    with pytest.raises(InvalidInputError):
        pages_svc.add_page_images(did, [])
    out = pages_svc.add_page_images(did, [("x.webp", io.BytesIO(_png(fmt="WEBP")))],
                                    slice_strips=False)
    assert out["added"] == 1 and db.list_pages(did)[0]["filename"].endswith(".png")


# --- S5: id-keyed translation -------------------------------------------------

class _LLM:
    name = "claude"
    model = "claude-test"
    supports_reference = True


@pytest.mark.parametrize("answer", [
    '{"translations": ["A", "B"], "context_summary": "x"}',          # positional
    '{"translations": {"0": "A"}, "context_summary": "x"}',           # short
    '{"translations": {"0": "A", "1": "B", "7": "C"}}',               # extra id
    '{"translations": {"0": "A", "1": null}}',                        # not a string
    "not json at all",
    None,
])
def test_untrusted_answers_apply_nothing(monkeypatch, answer):
    monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **k: answer)
    got, ctx = scanlate_translate.translate_regions_by_id({"0": "你好", "1": "再见"}, _LLM(), {},
                                                previous_context="before")
    assert got is None and ctx == "before"


def test_reordered_answer_is_matched_by_id(monkeypatch):
    seen = {}

    def fake(engine, prompt, **kw):
        seen["prompt"] = prompt
        return '```json\n{"translations": {"1": "Bye", "0": "Hello"}, "context_summary": "ctx2"}\n```'
    monkeypatch.setattr(translate_engines, "call_llm_json", fake)
    got, ctx = scanlate_translate.translate_regions_by_id({"0": "你好", "1": "再见"}, _LLM(), {},
                                                previous_context="ctx1")
    assert got == {"0": "Hello", "1": "Bye"} and ctx == "ctx2"
    assert "ctx1" in seen["prompt"] and '"id": "0"' in seen["prompt"]


# --- S5: the job ----------------------------------------------------------------

def _run(client, did, **body):
    return client.post(f"/api/scanlate/dramas/{did}/run", json=body)


def test_run_refused_when_paddle_vl_manga_needs_newer_transformers(client, fake_detect, monkeypatch):
    monkeypatch.setattr(ocr, "require_backend", _REAL_REQUIRE_BACKEND)
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: object())
    did = _drama()
    _page(did, 0)
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend",
                        lambda lang: "paddle_vl_manga")
    monkeypatch.setattr(ocr, "paddle_vl_manga_problem", lambda: "update transformers")
    r = _run(client, did, engine="fake")
    assert r.status_code == 503, r.text
    assert "update transformers" in r.text
    assert fake_detect == []


@needs_cv2
def test_art_captions_are_off_unless_asked_for(client, fake_detect):
    did = _drama()
    _page(did)
    assert _run(client, did, engine="fake").status_code == 200
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    assert fake_detect[0]["art_captions"] is False


@needs_cv2
def test_art_captions_run_translates_the_detected_caption_text(client, monkeypatch):
    seen = []

    def detect(image_path, lang, page_id=None, **kw):
        seen.append(kw["art_captions"])
        return [_region(5, "你好"), _region(50, "世界破裂", kind="caption")], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    did = _drama()
    pid = _page(did)
    assert _run(client, did, engine="fake", art_captions=True).status_code == 200
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    assert seen == [True]
    kinds = {b["kind"]: b for b in db.load_bubbles(pid)}
    assert kinds["caption"]["translated_text"].strip()


@needs_cv2
def test_art_captions_run_is_refused_without_paddle(client, fake_detect, monkeypatch):
    def require(backend, cmd=None):
        if backend == "paddle":
            from lib.errors import DependencyUnavailableError
            raise DependencyUnavailableError("PaddleOCR is not installed.")
    monkeypatch.setattr(ocr, "require_backend", require)
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend", lambda lang: "tesseract")
    did = _drama()
    _page(did)
    assert _run(client, did, engine="fake").status_code == 200
    wait_for_job(f"scanlate_{did}", timeout=20)
    r = _run(client, did, engine="fake", mode="all", confirm=True, art_captions=True)
    assert r.status_code == 503 and "PaddleOCR" in r.text


@needs_cv2
def test_a_redo_that_loses_the_captions_keeps_the_translated_page(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(5, "你好", translated_text="Hello"),
                          _region(50, "砰", kind="caption", translated_text="Bang")])
    monkeypatch.setattr(scanlate, "detect_and_ocr_page",
                        lambda *a, **k: ([_region(5, "你好")], []))   # the caption detector failed
    r = _run(client, did, engine="fake", mode="page", page_id=pid, art_captions=True)
    assert r.status_code == 200, r.text
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    kept = db.load_bubbles(pid)
    assert [(b["kind"], b["translated_text"]) for b in kept] == [("bubble", "Hello"),
                                                                 ("caption", "Bang")]


@needs_cv2
def test_translate_all_skips_pages_with_regions(client, fake_detect):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    db.save_bubbles(p1, [_region(1, translated_text="kept")])
    r = _run(client, did, engine="fake")
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == f"scanlate_{did}"
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["status"] == "done", st
    assert [c["page_id"] for c in fake_detect] == [p2]
    assert db.load_bubbles(p1)[0]["translated_text"] == "kept"
    regions = db.load_bubbles(p2)
    assert [b["source_text"] for b in regions] == ["你好", "再见", "砰"]
    assert regions[0]["translated_text"] and regions[1]["translated_text"]
    assert regions[2]["translated_text"] == ""          # SFX not sent
    for p in (p1, p2):                                  # both typeset (p1's was missing)
        page = db.get_page(p)
        assert page["rendered_filename"] == f"pages/typeset_id{p}.png"
        assert os.path.isfile(os.path.join(db.drama_dir(did), page["rendered_filename"]))
    notes = json.loads(db.get_page(p2)["run_notes"])
    assert any("Detector" in n["message"] for n in notes)
    # the reader can now serve the typeset image
    img = client.get(f"/api/scanlate/dramas/{did}/pages/{p2}/image?variant=rendered")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png"


@needs_cv2
def test_uses_saved_ocr_backend_and_tesseract(client, fake_detect, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_ocr_backend", lambda lang: "paddle")
    patch_setting(monkeypatch, "tesseract_cmd", "C:/saved/tesseract.exe")
    did = _drama()
    _page(did)
    _run(client, did, engine="fake", detect_backend="cv")
    wait_for_job(f"scanlate_{did}", timeout=20)
    assert fake_detect[0]["ocr_backend"] == "paddle"
    assert fake_detect[0]["tesseract_cmd"] == "C:/saved/tesseract.exe"
    assert fake_detect[0]["detect_backend"] == "cv"


@needs_cv2
def test_redo_page_replaces_and_redo_all_needs_confirm(client, fake_detect):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    db.save_bubbles(p1, [_region(1, translated_text="old")])
    db.save_bubbles(p2, [_region(1, translated_text="old2")])
    r = _run(client, did, mode="all", engine="fake")
    assert r.status_code == 409
    assert _run(client, did, mode="page", engine="fake").status_code == 422
    r = _run(client, did, mode="page", page_id=p1, engine="fake")
    assert r.status_code == 200
    wait_for_job(f"scanlate_{did}", timeout=20)
    assert len(db.load_bubbles(p1)) == 3
    assert db.load_bubbles(p2)[0]["translated_text"] == "old2"
    r = _run(client, did, mode="all", confirm=True, engine="fake")
    assert r.status_code == 200
    wait_for_job(f"scanlate_{did}", timeout=20)
    assert len(db.load_bubbles(p2)) == 3


@needs_cv2
def test_page_edited_mid_run_is_not_wiped(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, translated_text="before")])
    bid = db.load_bubbles(pid)[0]["id"]

    def detect(image_path, lang, page_id=None, **kw):
        # the user fixes a translation while the job is detecting
        assert db.update_bubble_fields(bid, {"translated_text": "my edit"})
        return [_region(5)], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    _run(client, did, mode="page", page_id=pid, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "edited meanwhile" in st["message"]
    rows = db.load_bubbles(pid)
    assert [(b["id"], b["translated_text"]) for b in rows] == [(bid, "my edit")]
    assert "edited while the job ran" in db.get_page(pid)["run_notes"]


@needs_cv2
def test_context_from_predecessor_and_usage_logged(client, fake_detect, monkeypatch):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    db.save_bubbles(p1, [_region(1, translated_text="x")])
    db.update_page(p1, context_summary="THE-PREVIOUS-PAGE")
    prompts = []

    def fake(engine, prompt, usage_cb=None, **kw):
        prompts.append(prompt)
        usage_cb(100, 20)
        return json.dumps({"translations": {"0": "Hello", "1": "Bye"},
                           "context_summary": "AFTER-P2"})
    monkeypatch.setattr(translate_engines, "call_llm_json", fake)
    monkeypatch.setattr(run_svc, "_build_engine", lambda name: _LLM())
    r = _run(client, did, mode="page", page_id=p2, engine="claude")
    assert r.status_code == 200, r.text
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    assert "THE-PREVIOUS-PAGE" in prompts[0]
    assert "砰" not in prompts[0]                      # SFX left out
    assert [b["translated_text"] for b in db.load_bubbles(p2)] == ["Hello", "Bye", ""]
    assert db.get_page(p2)["context_summary"] == "AFTER-P2"
    with db.get_conn() as conn:
        rows = conn.execute("SELECT operation, input_tokens FROM usage_log WHERE drama_id = ?",
                            (did,)).fetchall()
    assert [tuple(r) for r in rows] == [("scanlate_translate", 100)]


def test_context_skips_a_hidden_page(client, isolated_db):
    did = _drama()
    p1, p2, p3 = _page(did, 0), _page(did, 1), _page(did, 2)
    db.update_page(p1, context_summary="FROM-VISIBLE-PAGE")
    db.update_page(p2, context_summary="FROM-HIDDEN-PAGE")
    import comic_chapters
    comic_chapters.set_hidden(did, [db.get_page(p2)["filename"]], True)
    assert run_svc._predecessor_context(did, p3) == "FROM-VISIBLE-PAGE"
    assert run_svc._predecessor_context(did, p2) == "FROM-VISIBLE-PAGE"


@needs_cv2
def test_failed_redo_keeps_the_translated_regions(client, fake_detect, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, text="旧", translated_text="good one"),
                          _region(50, text="旧2", translated_text="good two")])
    before = [b["id"] for b in db.load_bubbles(pid)]
    monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **k: "not json")
    monkeypatch.setattr(run_svc, "_build_engine", lambda name: _LLM())
    _run(client, did, mode="page", page_id=pid, engine="claude")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "kept unchanged" in st["message"]
    rows = db.load_bubbles(pid)
    assert [b["id"] for b in rows] == before
    assert [b["translated_text"] for b in rows] == ["good one", "good two"]
    assert "kept unchanged" in db.get_page(pid)["run_notes"]


@needs_cv2
def test_redo_that_detects_nothing_keeps_the_regions(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, translated_text="good")])
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: ([], []))
    _run(client, did, mode="page", page_id=pid, engine="fake")
    wait_for_job(f"scanlate_{did}", timeout=20)
    assert [b["translated_text"] for b in db.load_bubbles(pid)] == ["good"]


@needs_cv2
def test_redo_with_partial_ocr_failure_keeps_the_page(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, translated_text="good one"),
                          _region(50, translated_text="good two")])
    before = [b["id"] for b in db.load_bubbles(pid)]

    def detect(image_path, lang, page_id=None, **kw):
        # one region read, one lost to an OCR error
        return [_region(5, "你好"), _region(50, "")], [
            ("error", "OCR failed on 1 of 2 regions. Check that the OCR backend is installed.")]
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    _run(client, did, mode="page", page_id=pid, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "kept unchanged" in st["message"]
    rows = db.load_bubbles(pid)
    assert [b["id"] for b in rows] == before
    assert [b["translated_text"] for b in rows] == ["good one", "good two"]
    assert "OCR" in db.get_page(pid)["run_notes"]


@needs_cv2
def test_redo_where_nothing_gets_translated_keeps_the_page(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, translated_text="good")])
    # only an SFX region has text, so nothing is sent for translation
    monkeypatch.setattr(scanlate, "detect_and_ocr_page",
                        lambda *a, **k: ([_region(5, "砰", kind="sfx")], []))
    _run(client, did, mode="page", page_id=pid, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "kept unchanged" in st["message"]
    assert [b["translated_text"] for b in db.load_bubbles(pid)] == ["good"]


@needs_cv2
def test_redo_keeps_the_page_when_one_chunk_of_a_dense_page_is_rejected(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    old = [_region(i, text=f"旧{i}", translated_text=f"good {i}") for i in range(45)]
    db.save_bubbles(pid, old)
    before = [(b["id"], b["translated_text"]) for b in db.load_bubbles(pid)]
    new = [_region(i, text=f"新{i}") for i in range(45)]
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: (new, []))
    replies = iter([
        json.dumps({"translations": {str(i): f"new {i}" for i in range(40)},
                    "context_summary": "s"}),
        "not json"])                                    # the second chunk (5 regions)
    monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **k: next(replies))
    monkeypatch.setattr(run_svc, "_build_engine", lambda name: _LLM())
    _run(client, did, mode="page", page_id=pid, engine="claude")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "kept unchanged" in st["message"]
    assert [(b["id"], b["translated_text"]) for b in db.load_bubbles(pid)] == before
    assert "weren't translated" in db.get_page(pid)["run_notes"]


@needs_cv2
def test_a_partly_translated_first_run_still_saves_what_was_translated(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    new = [_region(i, text=f"新{i}") for i in range(45)]
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: (new, []))
    replies = iter([json.dumps({"translations": {str(i): f"new {i}" for i in range(40)},
                                "context_summary": "s"}), "not json"])
    monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **k: next(replies))
    monkeypatch.setattr(run_svc, "_build_engine", lambda name: _LLM())
    _run(client, did, mode="page", page_id=pid, engine="claude")
    wait_for_job(f"scanlate_{did}", timeout=20)
    done = [b for b in db.load_bubbles(pid) if b["translated_text"]]
    assert len(done) == 40 and "weren't translated" in db.get_page(pid)["run_notes"]


@needs_cv2
def test_redo_with_ocr_error_keeps_hand_corrected_source_text(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, text="手改", translated_text=""),
                          _region(50, text="另一个", translated_text="")])
    before = [(b["id"], b["source_text"]) for b in db.load_bubbles(pid)]
    paid = []
    monkeypatch.setattr(translate_engines, "call_llm_json",
                        lambda *a, **k: paid.append(1) or "{}")
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: (
        [_region(5, "你好"), _region(50, "")], [("error", "OCR failed on 1 of 2 regions.")]))
    _run(client, did, mode="page", page_id=pid, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "kept unchanged" in st["message"]
    assert [(b["id"], b["source_text"]) for b in db.load_bubbles(pid)] == before
    assert paid == []


@needs_cv2
def test_a_redo_that_will_be_thrown_away_is_not_translated_or_billed(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(1, translated_text="good")])
    paid = []
    monkeypatch.setattr(translate_engines, "call_llm_json",
                        lambda *a, **k: paid.append(1) or "{}")
    monkeypatch.setattr(run_svc, "_build_engine", lambda name: _LLM())
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: (
        [_region(5, "你好"), _region(50, "")], [("error", "OCR failed on 1 of 2 regions.")]))
    _run(client, did, mode="page", page_id=pid, engine="claude")
    wait_for_job(f"scanlate_{did}", timeout=20)
    assert paid == []
    assert [b["translated_text"] for b in db.load_bubbles(pid)] == ["good"]


def test_answers_are_applied_by_key_not_position(monkeypatch):
    import services.scanlate_run_service as rs
    monkeypatch.setattr(translate_engines, "call_llm_json", lambda *a, **k: json.dumps(
        {"translations": {"2": "C", "0": "A"}, "context_summary": "s"}))
    regions = [_region(1, text="甲"), _region(2, text="乙", skip=True), _region(3, text="丙")]
    ok, ctx = rs._translate(regions, _LLM(), "claude", {"source_language": "ja"}, None, "", 1, [])
    assert ok and ctx == "s"
    assert [b["translated_text"] for b in regions] == ["A", "", "C"]


def test_source_language_and_usage_reach_machine_translation(isolated_db):
    class MT:
        name = "fake_mt"
        model = "fake_mt"
        supports_reference = False
        last_usage = {}

        def __init__(self):
            self.contexts = []

        def translate_batch(self, lines, context):
            self.contexts.append(context)
            self.last_usage = {"input_tokens": len(lines[0])}
            return ["T" + lines[0]]
    spent, engine = [], MT()
    result, _ = scanlate_translate.translate_regions_by_id(
        {"0": "こんにちは", "1": "さよなら"}, engine, {"source_language": "ja"},
        usage_cb=lambda i, o: spent.append(i))
    assert result == {"0": "Tこんにちは", "1": "Tさよなら"}
    assert [c["source_language"] for c in engine.contexts] == ["ja", "ja"]
    assert spent == [5, 4]


def test_llm_prompt_names_the_source_language(monkeypatch):
    prompts = []

    def fake(engine, prompt, **kw):
        prompts.append(prompt)
        return json.dumps({"translations": {"0": "A"}})
    monkeypatch.setattr(translate_engines, "call_llm_json", fake)
    scanlate_translate.translate_regions_by_id({"0": "あ"}, _LLM(), {"source_language": "ja"})
    assert "Japanese" in prompts[0]


@needs_cv2
def test_unmatched_answer_keeps_ocr_and_notes_it(client, fake_detect, monkeypatch):
    did = _drama()
    pid = _page(did)
    monkeypatch.setattr(translate_engines, "call_llm_json",
                        lambda *a, **k: '{"translations": ["Hello", "Bye"]}')
    monkeypatch.setattr(run_svc, "_build_engine", lambda name: _LLM())
    _run(client, did, engine="claude")
    wait_for_job(f"scanlate_{did}", timeout=20)
    rows = db.load_bubbles(pid)
    assert [b["source_text"] for b in rows][:2] == ["你好", "再见"]
    assert all(b["translated_text"] == "" for b in rows)
    assert "could not be matched" in db.get_page(pid)["run_notes"]
    assert db.get_page(pid)["rendered_filename"] is None      # nothing to typeset


@needs_cv2
def test_secret_and_path_in_error_are_redacted(client, monkeypatch):
    did = _drama()
    pid = _page(did)

    def detect(*a, **k):
        raise RuntimeError("401 for key sk-ant-api03-SECRETSECRETSECRET1234 reading "
                           "/home/kae/baihe/library/dramas/1/pages/page_0000.png")
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    _run(client, did, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["status"] == "done" and "1 failed" in st["message"]
    notes = db.get_page(pid)["run_notes"]
    assert "SECRETSECRET" not in notes and "/home/kae" not in notes and "<path>" in notes
    r = client.get(f"/api/scanlate/dramas/{did}/pages/{pid}")
    assert "SECRETSECRET" not in r.text and "/home/kae" not in r.text


@needs_cv2
def test_cancel_between_pages(client, monkeypatch):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    seen = []
    # Page 1 is detected ahead on a worker thread: the cancel must come once
    # the run has started on page 1, as a person's cancel does, not before
    # the run's first between-pages check.
    on_page_1 = threading.Event()
    real_progress = background_jobs.update_progress

    def progress(job_id, value, message=""):
        real_progress(job_id, value, message)
        if message == "Page 1 of 2":
            on_page_1.set()
    monkeypatch.setattr(run_svc.background_jobs, "update_progress", progress)

    def detect(image_path, lang, page_id=None, **kw):
        seen.append(page_id)
        assert on_page_1.wait(10)
        background_jobs.request_cancel(f"scanlate_{did}")
        return [_region(5)], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    _run(client, did, engine="fake")
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "cancelled"
    assert seen == [p1] and len(db.load_bubbles(p1)) == 1 and db.load_bubbles(p2) == []


def _cancel_while_lock_held(jid, call):
    """Runs `call` in a thread while the pipeline lock is held elsewhere, then
    cancels the job; returns what the thread raised and how long it took."""
    _put_job(jid)
    raised, lock = [], pages_svc.pipeline_lock()

    def worker():
        try:
            call()
        except BaseException as exc:
            raised.append(exc)
    with lock:
        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.3)
        assert t.is_alive()
        background_jobs.request_cancel(jid)
        started = time.monotonic()
        t.join(3)
        assert not t.is_alive()
    return raised, time.monotonic() - started


def test_cancel_ends_the_wait_for_the_pipeline_lock_in_a_run(isolated_db, monkeypatch):
    did = _drama()
    pid = _page(did)
    monkeypatch.setattr(scanlate, "detect_and_ocr_page",
                        lambda *a, **k: pytest.fail("detect ran without the lock"))
    drama = db.get_drama(did)
    raised, took = _cancel_while_lock_held(
        f"scanlate_{did}", lambda: run_svc._process_page(
            did, drama, pid, "all", None, "fake",
            {"detect_backend": "auto", "ocr_backend": None}, None, f"scanlate_{did}"))
    assert [type(e) for e in raised] == [background_jobs.JobCancelled] and took < 1


def test_cancel_ends_the_wait_for_the_pipeline_lock_in_a_render(isolated_db):
    from services import scanlate_render_service as render_svc
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(5, translated_text="hi")])
    jid = f"scanlate_{did}"
    raised, took = _cancel_while_lock_held(
        jid, lambda: render_svc.render_page(did, pid, cancel_check=lambda: render_svc.check_cancel(jid)))
    assert [type(e) for e in raised] == [background_jobs.JobCancelled] and took < 1


def test_cancel_while_waiting_to_render_ends_the_job_cancelled_with_no_page_note(isolated_db):
    from services import scanlate_render_service as render_svc
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(5, translated_text="hi")])
    jid = f"scanlate_{did}"
    raised, took = _cancel_while_lock_held(jid, lambda: render_svc._render_job(jid, did, [pid]))
    assert [type(e) for e in raised] == [background_jobs.JobCancelled] and took < 1
    assert not (db.get_page(pid).get("run_notes") or "").strip("[] ")


def test_a_cancel_raised_by_the_render_after_a_translate_is_not_a_page_note(isolated_db, monkeypatch):
    did = _drama()
    pid = _page(did)
    jid = f"scanlate_{did}"
    _put_job(jid)

    def cancelled(*a, **k):
        raise background_jobs.JobCancelled(jid)
    monkeypatch.setattr(scanlate, "detect_and_ocr_page",
                        lambda *a, **k: ([_region(5, translated_text="hi")], []))
    monkeypatch.setattr(run_svc, "_translate", lambda *a, **k: (True, "ctx"))
    monkeypatch.setattr(run_svc.render_svc, "render_page", cancelled)
    with pytest.raises(background_jobs.JobCancelled):
        run_svc._process_page(did, db.get_drama(did), pid, "all", None, "fake",
                              {"detect_backend": "auto", "ocr_backend": None}, None, jid)
    assert "Render failed" not in (db.get_page(pid).get("run_notes") or "")


@needs_cv2
def test_run_refusals(client, monkeypatch):
    from services import translate_service
    did = _drama()
    assert _run(client, did, engine="fake").status_code == 400      # no pages
    _page(did)
    assert _run(client, did, engine="nope").status_code == 422
    assert _run(client, did, engine="fake", detect_backend="gpu").status_code == 422
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name: None)
    assert _run(client, did, engine="claude").status_code == 503
    monkeypatch.undo()
    monkeypatch.setattr(ocr, "require_backend", lambda backend, cmd=None: None)
    db.save_bubbles(db.list_pages(did)[0]["id"], [_region(1, translated_text="x")])
    _put_job(f"scanlate_{did}")
    assert _run(client, did, engine="fake").status_code == 409
    assert client.post(f"/api/scanlate/dramas/{did}/render", json={}).status_code == 409
    assert client.post(f"/api/scanlate/dramas/{did}/export", json={}).status_code == 409
    assert _run(client, 9999, engine="fake").status_code == 404


def test_scanlate_job_blocks_drama_delete(isolated_db):
    from services import drama_service
    did = _drama()
    _put_job(f"scanlate_{did}")
    assert drama_service.job_running_for_drama(did)


def test_engine_gate_for_household_user(isolated_db, monkeypatch):
    from services import model_readiness_service
    # On disk, so the remote run is refused only by the rule under test.
    monkeypatch.setattr(model_readiness_service, "ocr_ready", lambda *a: True)
    app = create_app(ApiSettings(auth_mode="on"))
    c = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)
    u = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    did = _drama()
    url = f"/api/scanlate/dramas/{did}/run"
    assert c.post(url, json={"engine": "claude"}, headers=h).status_code == 403
    # no pages yet, so a free engine gets past the permission check to a 400
    assert c.post(url, json={"engine": "fake"}, headers=h).status_code == 400


# --- S6 / S8 ------------------------------------------------------------------------

@needs_cv2
def test_render_uses_db_regions_and_leaves_blank_untouched(client):
    did = _drama()
    pid = _page(did, w=200, h=120)
    db.save_bubbles(pid, [_region(x=10, translated_text="Hello", y=10, w=80, h=40),
                          _region(x=110, translated_text="", y=10, w=60, h=40),
                          _region(x=10, translated_text="BANG", kind="sfx", y=70)])
    r = client.post(f"/api/scanlate/dramas/{did}/render", json={"page_id": pid})
    assert r.status_code == 200
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    page = db.get_page(pid)
    out = os.path.join(db.drama_dir(did), page["rendered_filename"])
    with Image.open(out) as im:
        px = im.convert("RGB")
        # the blank region and the SFX region are untouched (still plain white)
        assert px.getpixel((140, 30)) == (255, 255, 255)
        assert px.getpixel((50, 90)) == (255, 255, 255)
    assert "no translation" in page["run_notes"]
    leftovers = [n for n in os.listdir(os.path.dirname(out)) if n.startswith(".typeset_")]
    assert leftovers == []


@needs_cv2
def test_concurrent_renders_do_not_collide(tmp_path):
    src = tmp_path / "p.png"
    src.write_bytes(_png(120, 80))
    out = str(tmp_path / "out.png")
    errors = []

    def go():
        try:
            scanlate_render.process_page(str(src), [_region(5, translated_text="Hi")], out)
        except Exception as e:  # pragma: no cover - the failure being guarded against
            errors.append(e)
    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(os.listdir(tmp_path)) == ["out.png", "p.png"]


@needs_cv2
def test_export_zip_and_pdf(client):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    db.save_bubbles(p1, [_region(5, translated_text="Hello")])
    r = client.post(f"/api/scanlate/dramas/{did}/export", json={"formats": ["zip", "pdf"]})
    assert r.status_code == 200, r.text
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["status"] == "done" and "1 page(s) have no translated text" in st["message"]
    z = client.get(f"/api/artifacts/dramas/{did}/scanlate_zip")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert names == ["page_0001.png", "page_0002.png"]
    pdf = client.get(f"/api/artifacts/dramas/{did}/scanlate_pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert db.get_page(p1)["rendered_filename"]          # rendered on the way
    assert client.post(f"/api/scanlate/dramas/{did}/export",
                       json={"formats": ["docx"]}).status_code == 422


def test_export_one_bad_page_does_not_stop_the_rest(client):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    os.remove(os.path.join(db.drama_dir(did), "pages", "page_0000.png"))
    client.post(f"/api/scanlate/dramas/{did}/export", json={"formats": ["zip"]})
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["status"] == "done" and "1 failed" in st["message"]
    assert "Export skipped" in db.get_page(p1)["run_notes"]
    z = client.get(f"/api/artifacts/dramas/{did}/scanlate_zip")
    assert zipfile.ZipFile(io.BytesIO(z.content)).namelist() == ["page_0002.png"]


def test_export_temp_files_stay_out_of_the_artifact_folder(client, monkeypatch):
    from services import artifact_service, scanlate_render_service as render_svc
    did = _drama()
    _page(did, 0)
    seen = []
    real = render_svc._tmp_beside

    def spy(dest):
        tmp = real(dest)
        seen.append((os.path.dirname(tmp), os.path.dirname(dest)))
        return tmp
    monkeypatch.setattr(render_svc, "_tmp_beside", spy)
    client.post(f"/api/scanlate/dramas/{did}/export", json={"formats": ["zip", "pdf"]})
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    assert seen and all(tmp_dir != art_dir for tmp_dir, art_dir in seen)
    # and an unfinished file is never served as the artifact
    folder = os.path.dirname(artifact_service.output_path(did, "scanlate_zip", "x.zip"))
    part = os.path.join(folder, ".export_new.part")
    with open(part, "wb") as f:
        f.write(b"half")
    os.utime(part, (time.time() + 100, time.time() + 100))
    assert artifact_service.get_artifact(did, "scanlate_zip")["name"] != ".export_new.part"


def test_pdf_export_is_capped_and_zip_is_not(client, monkeypatch):
    from services import scanlate_render_service as render_svc
    monkeypatch.setattr(render_svc, "MAX_PDF_EXPORT_PAGES", 1)
    did = _drama()
    _page(did, 0)
    _page(did, 1)
    assert client.post(f"/api/scanlate/dramas/{did}/export",
                       json={"formats": ["pdf"]}).status_code == 422
    assert client.post(f"/api/scanlate/dramas/{did}/export",
                       json={"formats": ["zip"]}).status_code == 200
    wait_for_job(f"scanlate_{did}", timeout=20)


@needs_cv2
def test_render_appends_to_the_run_notes(client):
    did = _drama()
    pid = _page(did)
    db.save_bubbles(pid, [_region(5, translated_text="Hi"), _region(60, translated_text="")])
    db.update_page(pid, run_notes=pages_svc.notes_to_json([("info", "Detector: OpenCV.")]))
    for _ in range(2):                                  # the second render adds no duplicate
        client.post(f"/api/scanlate/dramas/{did}/render", json={"page_id": pid})
        wait_for_job(f"scanlate_{did}", timeout=20)
    notes = json.loads(db.get_page(pid)["run_notes"])
    assert notes[0]["message"] == "Detector: OpenCV."
    assert sum("no translation" in n["message"] for n in notes) == 1


def test_clean_note_handles_spaces_in_paths():
    note = pages_svc.clean_note(r"failed at C:\Users\Alex Example\Baihe\p.png; url https://x.io/a")
    assert "Example" not in note and "Users" not in note and "https://x.io/a" in note
    assert "Alex Example" not in pages_svc.clean_note("bad /home/Alex Example/lib/p.png")


def test_prepared_page_over_50mb_is_refused(client, monkeypatch):
    from services import comic_view_service
    did = _drama()
    monkeypatch.setattr(comic_view_service, "MAX_IMAGE_BYTES", 100)
    r = _upload(client, did, [("a.png", _png(200, 200, (1, 2, 3)), "image/png")])
    assert r.status_code == 422 and "too large once prepared" in r.json()["error"]["message"]
    assert db.list_pages(did) == []


def test_pdf_inflation_ceiling_is_applied_when_pypdf_has_one(monkeypatch):
    filters = pytest.importorskip("pypdf.filters")
    monkeypatch.setattr(filters, "ZLIB_MAX_OUTPUT_LENGTH", 10 * pages_svc._PDF_INFLATE_CAP,
                        raising=False)
    pages_svc._limit_pdf_inflation()
    assert filters.ZLIB_MAX_OUTPUT_LENGTH == pages_svc._PDF_INFLATE_CAP


def test_failed_error_note_write_is_logged(isolated_db, monkeypatch):
    import applog
    import background_jobs
    seen = []

    class Log:
        def warning(self, msg, *args):
            seen.append(msg % args)
    monkeypatch.setattr(applog, "get_logger", lambda: Log())
    monkeypatch.setattr(run_svc.render_svc, "check_cancel", lambda jid: None)
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend", lambda lang: "auto")
    monkeypatch.setattr(run_svc.settings_service, "resolve_key", lambda k: None)
    patch_setting(monkeypatch, "tesseract_cmd", None)

    def page_fails(*a, **k):
        raise RuntimeError("page broke")

    def note_fails(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(run_svc, "_process_page", page_fails)
    monkeypatch.setattr(run_svc.db, "update_page", note_fails)
    monkeypatch.setattr(run_svc.db, "get_drama", lambda did: {})
    run_svc._run_job("j", 1, "all", [7], "claude", object(), "auto")
    assert len(seen) == 1 and "page 7" in seen[0] and "disk full" in seen[0]


def _job_harness(monkeypatch):
    import background_jobs
    monkeypatch.setattr(run_svc.render_svc, "check_cancel", lambda jid: None)
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend", lambda lang: "auto")
    monkeypatch.setattr(run_svc.settings_service, "resolve_key", lambda k: None)
    patch_setting(monkeypatch, "tesseract_cmd", None)
    monkeypatch.setattr(run_svc.db, "get_drama", lambda did: {})


def test_a_timed_out_call_stops_the_run_instead_of_failing_every_page(isolated_db, monkeypatch):
    """The abandoned request keeps the job's worker slot, so every later page
    would be refused with "still finishing" and written up as a failure."""
    from engine_backends import llm_tasks
    _job_harness(monkeypatch)
    processed, notes, scopes = [], [], []

    def page(drama_id, drama, pid, *a, **k):
        processed.append(pid)
        raise llm_tasks.LLMTaskTimeout("The AI engine did not answer within 330 seconds.")
    monkeypatch.setattr(run_svc, "_process_page", page)
    monkeypatch.setattr(run_svc.db, "update_page", lambda pid, **f: notes.append((pid, f)))
    real_scope = llm_tasks.bounded_llm_calls

    def spy(job_id, cancel_check, **kw):
        scopes.append(kw)
        return real_scope(job_id, cancel_check, **kw)
    monkeypatch.setattr(run_svc.llm_tasks, "bounded_llm_calls", spy)
    engine = type("E", (), {"name": "deepseek", "model": "m"})()
    with pytest.raises(llm_tasks.LLMTaskTimeout, match="did not answer"):
        run_svc._run_job("j", 1, "all", [7, 8, 9], "deepseek", engine, "auto")
    assert processed == [7]  # pages 8 and 9 were never attempted
    assert len(notes) == 1 and notes[0][0] == 7 and "did not answer" in notes[0][1]["run_notes"]
    # Each page is one request, and with thinking on DeepSeek may take the whole
    # client timeout to answer it.
    assert scopes[0]["deadline"] >= translate_engines.SDK_REQUEST_TIMEOUT


def test_translate_helper_does_not_turn_a_timeout_into_a_warning(monkeypatch):
    from engine_backends import llm_tasks

    def hangs(*a, **k):
        raise llm_tasks.LLMTaskTimeout("did not answer")
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", hangs)
    notes = []
    with pytest.raises(llm_tasks.LLMTaskTimeout):
        run_svc._translate([_region(5)], _LLM(), "claude", {}, None,
                           None, 1, notes)
    assert notes == []


def test_stored_page_error_note_is_redacted(isolated_db, monkeypatch):
    import background_jobs
    stored = []
    monkeypatch.setattr(run_svc.render_svc, "check_cancel", lambda jid: None)
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(run_svc.settings_service, "resolve_ocr_backend", lambda lang: "auto")
    monkeypatch.setattr(run_svc.settings_service, "resolve_key", lambda k: None)
    patch_setting(monkeypatch, "tesseract_cmd", None)

    def page_fails(*a, **k):
        raise RuntimeError("bad key sk-ant-abcdefghijklmnopqrstuvwxyz0123")
    monkeypatch.setattr(run_svc, "_process_page", page_fails)
    monkeypatch.setattr(run_svc.db, "update_page", lambda pid, **f: stored.append(f["run_notes"]))
    monkeypatch.setattr(run_svc.db, "get_drama", lambda did: {})
    run_svc._run_job("j", 1, "all", [7], "claude", object(), "auto")
    assert len(stored) == 1 and "This page failed" in stored[0]
    assert "sk-ant-abcdefghijklmnopqrstuvwxyz0123" not in stored[0]


@needs_cv2
def test_a_headroom_refusal_in_detect_stops_the_job_with_its_message(client, monkeypatch):
    from memory_headroom import HeadroomError
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    seen = []

    def detect(image_path, lang, page_id=None, **kw):
        seen.append(page_id)
        raise HeadroomError("Not loading the OCR model: Keep free graphics memory.")
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    _run(client, did, mode="all", confirm=True, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["status"] == "error" and "Keep free graphics memory" in st["error"]
    assert seen == [p1]
    assert db.get_page(p2)["run_notes"] in (None, "", "[]")


@needs_cv2
def test_a_headroom_refusal_in_translate_stops_the_job_once(client, fake_detect, monkeypatch):
    from memory_headroom import HeadroomError
    did = _drama()
    _page(did, 0), _page(did, 1)
    attempts = []

    def refuse(*a, **kw):
        attempts.append(1)
        raise HeadroomError("Not loading the model: Keep free graphics memory.")
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", refuse)
    _run(client, did, mode="all", confirm=True, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["status"] == "error" and "Keep free graphics memory" in st["error"]
    assert len(attempts) == 1


def test_a_headroom_refusal_in_ocr_is_not_turned_into_blank_text(monkeypatch):
    from memory_headroom import HeadroomError

    class Region:
        def to_bubble(self):
            return {"language": "zh"}
    monkeypatch.setattr(scanlate, "detect_bubbles", lambda *a, **k: [(0, 0, 10, 10)])
    monkeypatch.setattr(scanlate, "analyze_page_regions", lambda *a, **k: [Region()])

    def refuse(*a, **k):
        raise HeadroomError("Not loading the OCR model: Keep free graphics memory.")
    monkeypatch.setattr(scanlate, "ocr_box_region", refuse)
    with pytest.raises(HeadroomError):
        scanlate.detect_and_ocr_page("page.png", "zh")


def test_translation_asks_the_engine_not_to_think(isolated_db, monkeypatch):
    """One request per page: DeepSeek's hidden reasoning is most of a page's time."""
    from engine_backends import llm_tasks
    _job_harness(monkeypatch)
    scopes = []
    monkeypatch.setattr(run_svc, "_process_page", lambda *a, **k: "done")
    real_scope = llm_tasks.bounded_llm_calls

    def spy(job_id, cancel_check, **kw):
        scopes.append(kw)
        return real_scope(job_id, cancel_check, **kw)
    monkeypatch.setattr(run_svc.llm_tasks, "bounded_llm_calls", spy)
    engine = type("E", (), {"name": "deepseek", "model": "m"})()
    run_svc._run_job("j", 1, "all", [7], "deepseek", engine, "auto")
    assert scopes[0]["no_thinking"] is True


@needs_cv2
def test_next_page_is_detected_while_this_one_is_translated(client, monkeypatch):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    detected, order = [], []

    def detect(image_path, lang, page_id=None, **kw):
        detected.append(page_id)
        order.append(("detect", page_id))
        return [_region(5, "你好")], []

    def translate(keyed, engine, drama, **kw):
        order.append(("translate", len(detected)))
        # Page 2's detection is already running (or done) while page 1 is still translating.
        deadline = time.monotonic() + 5
        while p2 not in detected and time.monotonic() < deadline:
            time.sleep(0.01)
        assert p2 in detected
        return {k: "hi" for k in keyed}, "ctx"
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", translate)
    _run(client, did, mode="all", confirm=True, engine="fake")
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    assert detected == [p1, p2]
    assert [b["translated_text"] for b in db.load_bubbles(p2)] == ["hi"]


@needs_cv2
def test_a_page_failing_ocr_does_not_fail_the_next_one(client, monkeypatch):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)

    def detect(image_path, lang, page_id=None, **kw):
        return [_region(5, "")] if page_id == p1 else [_region(5, "你好")], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id",
                        lambda keyed, *a, **k: ({k: "hi" for k in keyed}, "ctx"))
    _run(client, did, mode="all", confirm=True, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "1 failed" in st["message"] and "1 translated" in st["message"]


def test_export_without_typeset_text_writes_the_original_pages(client, monkeypatch):
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    db.save_bubbles(p1, [_region(5, translated_text="Hello")])
    monkeypatch.setattr(run_svc.render_svc, "render_page",
                        lambda *a, **k: pytest.fail("rendered for an original-pages export"))
    r = client.post(f"/api/scanlate/dramas/{did}/export", json={"formats": ["zip"], "typeset": False})
    assert r.status_code == 200, r.text
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["status"] == "done" and "original" in st["message"] and "typeset" not in st["message"]
    z = client.get(f"/api/artifacts/dramas/{did}/scanlate_zip")
    orig = open(os.path.join(db.drama_dir(did), "pages", "page_0000.png"), "rb").read()
    assert zipfile.ZipFile(io.BytesIO(z.content)).read("page_0001.png") == orig


def test_export_message_names_the_pages_left_untranslated(client):
    did = _drama()
    _page(did, 0)
    client.post(f"/api/scanlate/dramas/{did}/export", json={"formats": ["zip"]})
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert "1 page(s) have no translated text and are the original" in st["message"]


@needs_cv2
def test_job_summary_leads_with_the_failures(client, monkeypatch):
    did = _drama()
    pid = _page(did)
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", lambda *a, **k: ([], []))
    _run(client, did, mode="page", page_id=pid, engine="fake")
    st = wait_for_job(f"scanlate_{did}", timeout=20)
    assert st["message"].startswith("Pages: 1 with no translation")
    assert "0 translated" in st["message"]


@needs_cv2
def test_page_with_no_bubbles_is_a_short_warning(tmp_path):
    import cv2
    import numpy as np
    blank = tmp_path / "blank.png"
    cv2.imwrite(str(blank), np.zeros((200, 200, 3), dtype=np.uint8))
    bubbles, notes = scanlate.detect_and_ocr_page(str(blank), "zh", detect_backend="cv")
    assert bubbles == []
    levels = [n[0] for n in notes]
    assert "warning" in levels and "error" not in levels
    assert len(notes[-1][-1]) < 300


def test_read_ahead_detection_keeps_the_runs_offline_switch(isolated_db, monkeypatch):
    """A run started away from the PC must never download a model, and the
    read-ahead runs on another thread."""
    from lib import model_offline
    did = _drama()
    p1, p2 = _page(did, 0), _page(did, 1)
    seen = []

    def detect(image_path, lang, page_id=None, **kw):
        seen.append((page_id, model_offline.active()))
        return [], []
    monkeypatch.setattr(scanlate, "detect_and_ocr_page", detect)
    jid = f"scanlate_{did}"
    _put_job(jid)
    engine = type("E", (), {"name": "deepseek", "model": "m"})()
    token = model_offline.set_offline(True)
    try:
        run_svc._run_job(jid, did, "all", [p1, p2], "deepseek", engine, "cv")
    finally:
        model_offline.reset(token)
    assert seen == [(p1, True), (p2, True)]
