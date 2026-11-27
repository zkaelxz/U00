"""Joining consecutive webtoon slices into chunks for Quick read."""
import io

from PIL import Image

from services import extension_bridge_service as bridge
from services import quick_read_stitch


def _slice(key, width=800, height=700, colour=(200, 200, 200)):
    out = io.BytesIO()
    image = Image.new("RGB", (width, height), colour)
    # Stripes, so the blank-page check does not reject the picture.
    for x in range(0, width, 40):
        image.paste((0, 0, 0), (x, 0, x + 10, height))
    image.save(out, "PNG")
    return {"key": key, "url": f"u/{key}", "content_type": "image/png", "content": out.getvalue()}


def _size(image):
    return Image.open(io.BytesIO(image["content"])).size


def test_slices_join_in_order_up_to_the_height():
    slices = [_slice(f"k{i}", height=600, colour=(i * 20, 0, 0)) for i in range(5)]
    chunks = quick_read_stitch.stitch(slices, 12 * 1024 * 1024, target_height=2000)
    assert [c["key"] for c in chunks] == ["k0", "k3"]
    assert chunks[0]["merged_keys"] == ["k1", "k2"] and chunks[1]["merged_keys"] == ["k4"]
    assert _size(chunks[0]) == (800, 1800) and _size(chunks[1]) == (800, 1200)
    # Order: the first slice's colour is at the top, the third's at the bottom.
    img = Image.open(io.BytesIO(chunks[0]["content"]))
    assert img.getpixel((20, 5))[0] == 0 and img.getpixel((20, img.size[1] - 5))[0] == 40


def test_a_different_width_or_tall_or_missing_picture_stays_alone():
    tall = _slice("tall", height=1500)
    narrow = _slice("narrow", width=400)
    unread = {"key": "u", "url": "x", "content_type": "", "content": None}
    images = [_slice("a"), _slice("b"), tall, narrow, unread, _slice("c")]
    out = quick_read_stitch.stitch(images, 12 * 1024 * 1024)
    assert [i["key"] for i in out] == ["a", "tall", "narrow", "u", "c"]
    assert out[0]["merged_keys"] == ["b"]
    assert out[1] is tall and out[2] is narrow and out[3] is unread


def test_a_failed_join_leaves_the_slices(monkeypatch):
    monkeypatch.setattr(quick_read_stitch, "_join", lambda parts: (_ for _ in ()).throw(MemoryError()))
    images = [_slice("a"), _slice("b")]
    assert quick_read_stitch.stitch(images, 12 * 1024 * 1024) == images


def test_a_chunk_over_the_byte_cap_leaves_the_slices():
    images = [_slice("a"), _slice("b")]
    assert quick_read_stitch.stitch(images, 10) == images


def test_chunk_releases_every_slice_claim_when_it_fails():
    chunk = {"key": "a", "run_claims": ["d1", "d2"]}
    bridge._runs_seen["r"] = {"d1": "a", "d2": "b", "d3": "c"}
    bridge._release_unread([chunk], [("failed", {})], "r")
    assert bridge._runs_seen["r"] == {"d3": "c"}
    bridge._runs_seen.pop("r")


def test_health_says_slices_can_be_joined(isolated_db):
    assert bridge.health_payload()["stitch_slices"] is True


def _run(monkeypatch, payload, images):
    from services import page_server
    seen = []

    def fake(content, content_type, **kwargs):
        seen.append(Image.open(io.BytesIO(content)).size)
        return {"regions": [], "seconds": {}}

    monkeypatch.setattr(page_server, "translate_image", fake)
    answer = bridge.run_images({"drama_id": None, "store": False, **payload}, images)
    return seen, answer


def _wire(slices):
    import base64
    return [{"data": base64.b64encode(s["content"]).decode(), "content_type": "image/png", "key": s["key"]}
            for s in slices]


def test_quick_read_joins_only_when_asked(isolated_db, monkeypatch):
    slices = [_slice(f"k{i}", height=600, colour=(i * 30, 9, 9)) for i in range(3)]
    seen, answer = _run(monkeypatch, {"stitch": True, "filter_pages": False}, _wire(slices))
    assert seen == [(800, 1800)]
    assert answer["pages"][0]["key"] == "k0" and answer["pages"][0]["merged_keys"] == ["k1", "k2"]
    seen, answer = _run(monkeypatch, {"filter_pages": False, "run_id": "r2"},
                        _wire([_slice(f"j{i}", height=600, colour=(i * 30 + 1, 9, 9)) for i in range(3)]))
    assert seen == [(800, 600)] * 3
    assert all("merged_keys" not in p for p in answer["pages"])


def test_a_saved_send_is_never_joined(isolated_db, monkeypatch):
    import db
    drama = db.create_drama(title_en="Sample")
    slices = [_slice(f"s{i}", height=600, colour=(i * 30 + 5, 9, 9)) for i in range(2)]
    seen, _answer = _run(monkeypatch, {"stitch": True, "store": True, "drama_id": drama,
                                       "filter_pages": False}, _wire(slices))
    assert seen == [(800, 600)] * 2


def test_whole_comic_pages_are_never_joined():
    pages = [_slice(f"p{i}", width=800, height=1130, colour=(i * 20, 0, 0)) for i in range(3)]
    assert quick_read_stitch.stitch(pages, 12 * 1024 * 1024) == pages


def test_landscape_frames_are_never_joined():
    frames = [_slice(f"f{i}", width=1280, height=720, colour=(i * 20, 0, 0)) for i in range(2)]
    assert quick_read_stitch.stitch(frames, 12 * 1024 * 1024) == frames


def test_a_chunk_carries_the_claims_of_its_slices():
    slices = [{**_slice(f"c{i}", height=600), "run_claim": f"d{i}"} for i in range(2)]
    assert quick_read_stitch.stitch(slices, 12 * 1024 * 1024)[0]["run_claims"] == ["d0", "d1"]
