"""UI-free Scanlate page-upload helper (Streamlit retirement).

`add_uploaded_pages` moved here unchanged from tabs/scanlate_tab.py so the
tab and a future API route share one implementation; the tab imports it
back. Never imports streamlit/fastapi.
"""
import os

import db


def _upload_bytes(upload):
    """Raw bytes of an upload: `getbuffer()` if it has one, else `read()`."""
    getbuffer = getattr(upload, "getbuffer", None)
    if getbuffer is not None:
        return getbuffer()
    return upload.read()


def add_uploaded_pages(drama_id: int, pages_dir: str, uploads, slice_strips: bool = False):
    """Saves uploaded images/PDFs as the drama's next pages. Returns
    (pages added, PDF pages skipped for having no embedded image).

    `uploads` is any iterable of objects with a `.name` (used only for its
    extension) and either `getbuffer()` (Streamlit's UploadedFile) or
    `read()` returning bytes (e.g. a FastAPI/Starlette upload's file)."""
    import shutil
    import tempfile
    import scanlate
    from PIL import Image as PILImage
    next_idx = len(db.list_pages(drama_id))
    added = 0
    pdf_skipped_total = 0

    def add_copy(src_path, ext):
        nonlocal added
        fname = f"page_{next_idx + added:04d}{ext}"
        fpath = os.path.join(pages_dir, fname)
        shutil.copy(src_path, fpath)
        with PILImage.open(fpath) as im:
            w, h = im.size
        db.create_page(drama_id, next_idx + added, os.path.join("pages", fname), w, h)
        added += 1

    for f in uploads:
        ext = os.path.splitext(f.name)[1].lower()
        with tempfile.TemporaryDirectory() as tmp_dir:
            # A fixed temp name: OpenCV can't open non-ASCII paths on Windows.
            tmp_path = os.path.join(tmp_dir, "upload" + ext)
            with open(tmp_path, "wb") as out:
                out.write(_upload_bytes(f))
            if ext == ".pdf":
                extracted, skipped = scanlate.pdf_to_page_images(tmp_path, tmp_dir)
                for p in extracted:
                    add_copy(p, ".png")
                pdf_skipped_total += len(skipped)
                continue
            with PILImage.open(tmp_path) as im:
                size = im.size
            if slice_strips and scanlate.is_webtoon_strip(*size):
                for p in scanlate.slice_webtoon_to_files(tmp_path, tmp_dir):
                    add_copy(p, ".png")
            else:
                add_copy(tmp_path, ext)
    return added, pdf_skipped_total
