"""Scanlate and comic fixes from the second-pass review."""
import os

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
from PIL import Image  # noqa: E402

import db  # noqa: E402
from scanlate import scanlate_page_decode  # noqa: E402
from scanlate import scanlate_panels  # noqa: E402
from scanlate import scanlate_inpaint  # noqa: E402
from scanlate import scanlate_fonts  # noqa: E402
from scanlate import scanlate_text_layout  # noqa: E402
from services import keep_original_service, scanlate_render_service  # noqa: E402


def test_a_strip_tail_that_is_only_the_overlap_band_joins_the_last_slice(tmp_path):
    path = str(tmp_path / "strip.png")
    cv2.imwrite(path, np.full((1700, 8, 3), 255, dtype=np.uint8))
    slices = scanlate_panels.split_webtoon_strip(path, target_height=1600, overlap=100)
    assert slices[-1]["y_end"] == 1700
    assert all(s["y_end"] - s["y_start"] > 160 for s in slices)


def test_transparent_pixels_decode_as_white(tmp_path):
    path = str(tmp_path / "t.png")
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(path)
    assert scanlate_page_decode.decode_page(path).min() == 255


def test_cjk_punctuation_is_replaced_before_typesetting():
    assert "「".translate(scanlate_fonts._CJK_PUNCTUATION) == '"'
    assert "～".translate(scanlate_fonts._CJK_PUNCTUATION) == "~"


def test_large_dark_art_running_off_the_box_edge_is_not_erased():
    mask = np.zeros((40, 40), np.uint8)
    mask[:, :20] = 255          # art touching the edge
    mask[18:22, 30:34] = 255    # a stroke inside
    scanlate_inpaint._drop_marks_running_off_edge(mask)
    assert mask[:, :20].sum() == 0 and mask[18:22, 30:34].all()


def test_lettering_in_the_strip_between_a_box_and_the_page_edge_is_erased():
    page = np.full((100, 100, 3), 255, np.uint8)
    page[90:100, 20:80] = (60, 60, 200)             # lettering the page cuts, 1 px below the box
    box = {"x": 10, "y": 40, "w": 80, "h": 59}
    inside = np.ones((59, 80), bool)
    scanlate_inpaint.inpaint_region("x.png", box, image=page, inside=inside, backend="cv")
    assert page[99, 20:80].min() > 200
    assert page[60, 50].min() == 255                # the rest of the page is left alone


@pytest.mark.parametrize("box, edge", [({"x": 10, "y": 79, "w": 20, "h": 20}, "bottom"),
                                       ({"x": 10, "y": 1, "w": 20, "h": 20}, "top"),
                                       ({"x": 79, "y": 10, "w": 20, "h": 20}, "right"),
                                       ({"x": 1, "y": 10, "w": 20, "h": 20}, "left")])
def test_the_balloon_inside_runs_on_to_each_page_edge_a_box_stops_short_of(box, edge):
    left, top = max(0, box["x"] - 4), max(0, box["y"] - 4)
    width, height = min(100 - left, box["w"] + 8), min(100 - top, box["h"] + 8)
    keep = np.zeros((height, width), bool)
    keep[box["y"] - top:box["y"] - top + box["h"], box["x"] - left:box["x"] - left + box["w"]] = True
    scanlate_inpaint._extend_inside_to_page_edge(keep, box, left, top, (100, 100))
    strip = {"bottom": keep[-1], "top": keep[0], "right": keep[:, -1], "left": keep[:, 0]}[edge]
    assert strip.sum() == 20


def test_the_balloon_inside_is_not_extended_for_a_box_away_from_the_page_edge():
    box = {"x": 30, "y": 30, "w": 20, "h": 20}
    keep = np.zeros((28, 28), bool)
    keep[4:24, 4:24] = True
    before = keep.copy()
    scanlate_inpaint._extend_inside_to_page_edge(keep, box, 26, 26, (100, 100))
    assert (keep == before).all()


def test_storage_cleanup_forgets_the_typeset_files_it_deleted(isolated_db):
    did = db.create_drama(title_en="t")
    pid = db.create_page(did, 0, "pages/a.png", 10, 10)
    db.update_page(pid, rendered_filename="pages/typeset_id1.png")
    keep_original_service.clean_title_storage(did, ["typeset_pages"])
    assert db.get_page(pid)["rendered_filename"] is None


def test_a_page_with_no_translation_left_drops_its_typeset_image(isolated_db):
    did = db.create_drama(title_en="t")
    pid = db.create_page(did, 0, "pages/a.png", 10, 10)
    folder = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(folder, exist_ok=True)
    stale = os.path.join(folder, "typeset_id1.png")
    open(stale, "wb").write(b"x")
    db.update_page(pid, rendered_filename="pages/typeset_id1.png")
    scanlate_render_service._drop_stale_typeset(did, db.get_page(pid))
    assert db.get_page(pid)["rendered_filename"] is None
    assert not os.path.exists(stale)


def _ink(img):
    return int((np.asarray(img.convert("L")) < 128).sum())


def test_kanji_in_english_text_use_a_cjk_font_when_one_exists(monkeypatch):
    font = scanlate_fonts.find_cjk_font()
    if font is None:
        pytest.skip("no CJK font on this machine")
    box = {"x": 0, "y": 0, "w": 120, "h": 60}
    img = Image.new("RGB", (120, 60), "white")
    scanlate_text_layout.render_text_in_box(img, box, "漢字", font_size=30)
    tofu = Image.new("RGB", (120, 60), "white")
    monkeypatch.setattr(scanlate_text_layout, "find_cjk_font", lambda: None)
    scanlate_text_layout.render_text_in_box(tofu, box, "漢字", font_size=30)
    assert _ink(img) > 0 and _ink(tofu) == 0


def test_kanji_without_a_cjk_font_are_dropped_and_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr(scanlate_text_layout, "find_cjk_font", lambda: None)
    img = Image.new("RGB", (160, 60), "white")
    scanlate_text_layout.render_text_in_box(img, {"x": 0, "y": 0, "w": 160, "h": 60}, "Hi 漢字 there",
                                       font_size=20)
    plain = Image.new("RGB", (160, 60), "white")
    scanlate_text_layout.render_text_in_box(plain, {"x": 0, "y": 0, "w": 160, "h": 60}, "Hi there",
                                       font_size=20)
    assert list(img.getdata()) == list(plain.getdata())
    assert scanlate_fonts.has_cjk("漢") and not scanlate_fonts.has_cjk("Hi, ok")
