"""Reading a comic page image once, as the BGR and grayscale arrays every detect and render pass shares."""

import numpy as np


def decode_page(image_path: str):
    """One page as an OpenCV BGR array, for a whole detect or render pass to
    share. Read through Pillow: cv2.imread can't open non-ASCII paths on
    Windows, and Pillow's pixel grid (no EXIF rotation) is the one the page's
    stored size and every crop already use. ValueError if unreadable."""
    from PIL import Image
    try:
        with Image.open(image_path) as opened:
            if "A" in opened.getbands() or "transparency" in opened.info:
                # Plain RGB conversion keeps whatever colour sits under a
                # transparent pixel, usually black.
                rgba = opened.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                rgb = np.asarray(flat)
            else:
                rgb = np.asarray(opened.convert("RGB"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read image: {image_path}") from exc
    return np.ascontiguousarray(rgb[:, :, ::-1])


def page_bgr(image_path: str, image=None):
    """The page as an OpenCV BGR array: `image` when the caller already
    decoded it, so one page pass decodes the file once, else a fresh read
    (None if unreadable)."""
    if image is not None:
        return image
    import cv2
    return cv2.imread(image_path)


def page_gray(image_path: str, image=None):
    """page_bgr() in grayscale; `image` may already be gray or BGR."""
    import cv2
    if image is None:
        return cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
