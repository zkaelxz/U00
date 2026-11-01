"""Scanlate and comic fixes from the second-pass review."""
import os

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")
from PIL import Image  # noqa: E402

import db  # noqa: E402
import scanlate_detect  # noqa: E402
import scanlate_inpaint  # noqa: E402
import scanlate_render  # noqa: E402
from services import keep_original_service, scanlate_render_service  # noqa: E402


def test_a_strip_tail_that_is_only_the_overlap_band_joins_the_last_slice(tmp_path):
    path = str(tmp_path / "strip.png")
    cv2.imwrite(path, np.full((1700, 8, 3), 255, dtype=np.uint8))
    slices = scanlate_detect.split_webtoon_strip(path, target_height=1600, overlap=100)
    assert slices[-1]["y_end"] == 1700
    assert all(s["y_end"] - s["y_start"] > 160 for s in slices)


def test_transparent_pixels_decode_as_white(tmp_path):
    path = str(tmp_path / "t.png")
    Image.new("RGBA", (4, 4), (0, 0, 0, 0)).save(path)
    assert scanlate_detect.decode_page(path).min() == 255


def test_cjk_punctuation_is_replaced_before_typesetting():
    assert "「".translate(scanlate_render._CJK_PUNCTUATION) == '"'
    assert "～".translate(scanlate_render._CJK_PUNCTUATION) == "~"


def test_large_dark_art_running_off_the_box_edge_is_not_erased():
    mask = np.zeros((40, 40), np.uint8)
    mask[:, :20] = 255          # art touching the edge
    mask[18:22, 30:34] = 255    # a stroke inside
    scanlate_inpaint._drop_marks_running_off_edge(mask)
    assert mask[:, :20].sum() == 0 and mask[18:22, 30:34].all()


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
