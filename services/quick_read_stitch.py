"""Quick read: joins consecutive webtoon slices into taller chunks so a bubble on a seam is read whole."""

import io

# A site's slices are short (a few hundred to ~1,200 px); anything taller is a real page.
MAX_SLICE_HEIGHT = 1200
# A whole comic page is about 1.4 times taller than wide and is never joined to the next one; a strip's
# slice is squarer. Joining real pages would put two pages in one picture.
MAX_SLICE_ASPECT = 1.2
# Below this a picture is a landscape frame or spread, not a strip slice.
MIN_SLICE_ASPECT = 0.6
TARGET_CHUNK_HEIGHT = 2000
# Under the reader's 3 MP per-region cap even for a wide chunk.
MAX_CHUNK_PIXELS = 2_500_000


def _size(content: bytes):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(content)) as image:
            return image.size
    except Exception:
        return None


def _join(parts: list) -> bytes:
    """One PNG: lossless, so joining never costs the reader any detail."""
    from PIL import Image
    width = parts[0][1][0]
    canvas = Image.new("RGB", (width, sum(size[1] for _c, size in parts)), (255, 255, 255))
    top = 0
    for content, size in parts:
        with Image.open(io.BytesIO(content)) as image:
            # Transparent areas become white paper, not the black an alpha-less convert would give.
            rgba = image.convert("RGBA")
            canvas.paste(rgba, (0, top), rgba)
        top += size[1]
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def stitch(decoded: list, max_bytes: int, target_height: int = TARGET_CHUNK_HEIGHT) -> list:
    """`decoded` with each run of consecutive same-width slices replaced by one chunk. A chunk keeps
    the first slice's key and url and lists the other slices' keys in `merged_keys`, so the phone
    shows it where the first slice stood. Cuts fall only on slice edges. A picture with no bytes,
    a tall one or one shaped like a whole page, a different width, or a run that cannot be joined stays as it was."""
    out, run = [], []

    def flush():
        group = run[:]
        run.clear()
        if len(group) < 2:
            out.extend(image for image, _s in group)
            return
        try:
            content = _join([(image["content"], size) for image, size in group])
        except Exception:
            out.extend(image for image, _s in group)
            return
        if len(content) > max_bytes:
            out.extend(image for image, _s in group)
            return
        first = group[0][0]
        out.append({"key": first["key"], "url": first["url"], "content_type": "image/png",
                    "content": content, "merged_keys": [image["key"] for image, _s in group[1:]],
                    "run_claims": [image["run_claim"] for image, _s in group if image.get("run_claim")]})

    for image in decoded:
        size = _size(image["content"]) if image.get("content") is not None else None
        if size is None or size[1] > MAX_SLICE_HEIGHT or not MIN_SLICE_ASPECT <= size[1] / size[0] <= MAX_SLICE_ASPECT:
            flush()
            out.append(image)
            continue
        if run:
            width = run[0][1][0]
            height = sum(s[1] for _i, s in run) + size[1]
            if size[0] != width or height > target_height or height * width > MAX_CHUNK_PIXELS:
                flush()
        run.append((image, size))
    flush()
    return out
