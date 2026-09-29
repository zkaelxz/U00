"""Tests for services/scanlate_service.py (moved from
tests/test_emotion_manhua_ui.py::TestWebtoonUpload). No Streamlit needed."""
import io
import os
import shutil
import tempfile

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")  # requirements-media.txt, not core -- skip cleanly without it

from services.scanlate_service import add_uploaded_pages


def _strip_image(h=5000):
    """Same panel-banded white strip as TestWebtoonSlicing._strip."""
    strip = np.full((h, 800, 3), 255, dtype=np.uint8)
    for band in range(0, h, 900):
        cv2.rectangle(strip, (50, band + 50), (750, min(band + 700, h - 1)), (120, 120, 120), -1)
    return strip


class TestWebtoonUpload:
    """Step 25g item 3: Scanlate's own uploader slices a tall strip
    when asked to, and leaves ordinary pages alone."""

    class _Upload:
        def __init__(self, name, path):
            self.name = name
            with open(path, "rb") as fh:
                self._data = fh.read()

        def getbuffer(self):
            return memoryview(self._data)

    def _add(self, isolated_db, img, slice_strips):
        did = isolated_db.create_drama(title_zh="x", media_type="manhua")
        pages_dir = os.path.join(isolated_db.drama_dir(did), "pages")
        os.makedirs(pages_dir, exist_ok=True)
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "条漫.png")
            cv2.imencode(".png", img)[1].tofile(p)
            added, _ = add_uploaded_pages(did, pages_dir, [self._Upload("条漫.png", p)],
                                          slice_strips=slice_strips)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        return added, isolated_db.list_pages(did)

    def test_tall_strip_is_sliced_into_pages(self, isolated_db):
        img = _strip_image(5000)
        added, pages = self._add(isolated_db, img, slice_strips=True)
        assert added > 1 and len(pages) == added
        assert all(p["width"] == 800 and p["height"] < 5000 for p in pages)

    def test_strip_is_kept_whole_when_slicing_is_off(self, isolated_db):
        img = np.full((5000, 800, 3), 255, dtype=np.uint8)
        added, pages = self._add(isolated_db, img, slice_strips=False)
        assert added == 1 and pages[0]["height"] == 5000

    def test_ordinary_page_is_never_sliced(self, isolated_db):
        img = np.full((2400, 1700, 3), 255, dtype=np.uint8)
        added, _ = self._add(isolated_db, img, slice_strips=True)
        assert added == 1

    def test_read_only_upload_is_accepted(self, isolated_db):
        # A future API upload exposes read() rather than getbuffer().
        class _ReadUpload:
            name = "page.png"

            def __init__(self, data):
                self.file = io.BytesIO(data)

            def read(self):
                return self.file.read()

        did = isolated_db.create_drama(title_zh="x", media_type="manhua")
        pages_dir = os.path.join(isolated_db.drama_dir(did), "pages")
        os.makedirs(pages_dir, exist_ok=True)
        data = cv2.imencode(".png", np.full((200, 100, 3), 255, dtype=np.uint8))[1].tobytes()
        added, skipped = add_uploaded_pages(did, pages_dir, [_ReadUpload(data)])
        assert (added, skipped) == (1, 0)
        assert isolated_db.list_pages(did)[0]["width"] == 100
