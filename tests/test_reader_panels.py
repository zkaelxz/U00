"""scanlate_reader_panels: the comic reader's panel finder, on drawn pages."""
import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from scanlate.scanlate_reader_panels import find_panels, in_reading_order  # noqa: E402

W, H = 800, 1200


def _page(colour=(255, 255, 255)):
    return np.full((H, W, 3), colour, np.uint8)


def _panel(img, pts, fill=(170, 170, 170)):
    pts = np.array(pts, np.int32)
    cv2.fillPoly(img, [pts], fill)
    cv2.polylines(img, [pts], True, (0, 0, 0), 4)


def _rect(img, x, y, w, h, fill=(170, 170, 170)):
    _panel(img, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], fill)


def _starts(boxes):
    return [(b["x"], b["y"]) for b in boxes]


def _near(found, expected, tol=12):
    return len(found) == len(expected) and all(
        abs(a - b) <= tol for f, e in zip(found, expected) for a, b in zip(f, e))


def test_a_column_beside_a_tall_panel_is_read_down_the_column():
    img = _page()
    _rect(img, 40, 40, 340, 540)      # left, top
    _rect(img, 40, 620, 340, 540)     # left, bottom
    _rect(img, 420, 40, 340, 1120)    # right, full height
    boxes = find_panels(img)
    assert _near(_starts(in_reading_order(boxes, False)), [(38, 38), (38, 618), (418, 38)])
    assert _near(_starts(in_reading_order(boxes, True)), [(418, 38), (38, 38), (38, 618)])


def test_panels_a_few_pixels_apart_are_split_between_their_frames():
    img = _page()
    _rect(img, 40, 40, 356, 1120)
    _rect(img, 404, 40, 356, 1120)    # a 4 px gutter
    assert len(find_panels(img)) == 2


def test_a_slanted_gutter_is_followed():
    img = _page()
    _panel(img, [(40, 40), (760, 40), (760, 500), (40, 640)])
    _panel(img, [(40, 680), (760, 540), (760, 1160), (40, 1160)])
    boxes = in_reading_order(find_panels(img), False)
    assert len(boxes) == 2
    assert boxes[0]["y"] < 60 and boxes[0]["y"] + boxes[0]["h"] < 680
    assert boxes[1]["y"] > 500 and boxes[1]["y"] + boxes[1]["h"] > 1140


def test_a_colour_page_takes_no_slanted_cut():
    img = _page()
    _panel(img, [(40, 40), (760, 40), (760, 500), (40, 640)], fill=(200, 120, 40))
    _panel(img, [(40, 680), (760, 540), (760, 1160), (40, 1160)], fill=(200, 120, 40))
    assert find_panels(img) == []


def test_webtoon_art_and_a_balloon_on_white_stay_whole():
    img = _page()
    cv2.circle(img, (400, 330), 260, (90, 60, 160), -1)               # art with no frame
    cv2.ellipse(img, (400, 900), (220, 120), 0, 0, 360, (0, 0, 0), 3)  # a balloon below it
    cv2.putText(img, "Hello", (300, 915), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    assert find_panels(img) == []


def test_a_cover_with_art_to_every_edge_stays_whole():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 200, (H, W, 3), dtype=np.uint8)
    assert find_panels(img) == []


def test_a_thin_strip_between_two_lines_is_not_a_panel():
    img = _page()
    _rect(img, 40, 40, 720, 540)
    _rect(img, 40, 620, 330, 540)
    _rect(img, 378, 620, 30, 540)     # a 30 px wide strip: part of the panel beside it
    _rect(img, 416, 620, 344, 540)
    assert len(find_panels(img)) == 3


def test_white_sky_inside_a_panel_keeps_the_panel_whole():
    img = _page()
    cv2.rectangle(img, (40, 40), (760, 580), (0, 0, 0), 4)
    cv2.rectangle(img, (40, 620), (760, 1160), (0, 0, 0), 4)
    img[300:580, 44:756] = 120        # ground under white sky
    img[800:1156, 44:756] = 120
    boxes = in_reading_order(find_panels(img), False)
    assert _near([(b["x"], b["y"], b["w"], b["h"]) for b in boxes],
                 [(38, 38, 724, 544), (38, 618, 724, 544)], tol=6)


@pytest.mark.parametrize("fill,gutter", [((200, 120, 40), 40), ((100, 100, 100), 8)])
def test_art_to_both_page_edges_is_split_at_its_gutter(fill, gutter):
    img = _page()
    for top in (300, 580 + gutter):
        img[top:top + 280] = 0
        img[top + 4:top + 276] = fill
    assert len(find_panels(img)) == 2


def test_a_wide_strip_is_measured_without_blowing_it_up(monkeypatch):
    import scanlate.scanlate_reader_panels as panels
    sizes = []
    real = panels._Cutter.__init__

    def spy(self, ink, slanted):
        sizes.append(ink.shape)
        real(self, ink, slanted)
    monkeypatch.setattr(panels._Cutter, "__init__", spy)
    img = np.full((300, 3000, 3), 255, np.uint8)
    for x in (20, 1520):
        _rect(img, x, 20, 1460, 260)
    assert len(find_panels(img)) == 2
    assert max(sizes[0]) == 1000


def test_dark_jpeg_noise_does_not_count_as_colour():
    img = _page()
    _panel(img, [(40, 40), (760, 40), (760, 500), (40, 640)])
    _panel(img, [(40, 680), (760, 540), (760, 1160), (40, 1160)])
    cv2.rectangle(img, (100, 800), (400, 1100), (25, 20, 28), -1)   # a night scene
    assert len(find_panels(img)) == 2


@pytest.mark.parametrize("width", [781, 782, 793, 794])
def test_boxes_stay_inside_the_page(width):
    img = np.full((H, width, 3), 255, np.uint8)
    _rect(img, 0, 40, width // 2 - 10, 1120)
    _rect(img, width // 2 + 10, 40, width - width // 2 - 10, 1120)
    boxes = find_panels(img)
    assert len(boxes) == 2
    assert all(b["x"] + b["w"] <= width and b["y"] + b["h"] <= H for b in boxes)
