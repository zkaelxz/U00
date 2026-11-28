"""Balloon text set in the roomiest rectangle (scanlate_rect_layout)."""
import numpy as np
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw

from scanlate import scanlate_text_layout
def _ellipse_mask(w, h):
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2 <= 1


def test_roomiest_rectangle_is_the_largest_all_inside_rectangle():
    mask = np.zeros((6, 8), dtype=bool)
    mask[1:5, 2:7] = True        # 4 x 5 = 20
    mask[5, 0:2] = True          # stray inside pixels elsewhere must not win
    assert scanlate_text_layout._roomiest_rectangle(mask) == (1, 2, 5, 7)


def test_roomiest_rectangle_of_an_empty_mask_is_none():
    assert scanlate_text_layout._roomiest_rectangle(np.zeros((4, 4), dtype=bool)) is None


def test_rectangle_layout_gives_every_line_the_same_span_inside_the_mask():
    mask = _ellipse_mask(220, 160)
    draw = ImageDraw.Draw(Image.new("RGB", (220, 160)))
    from PIL import ImageFont
    laid = scanlate_text_layout._layout_in_rectangle(
        draw, "this is a fairly long line of translated text", mask, 20,
        lambda sz: ImageFont.load_default(sz))
    assert laid is not None
    _font, lines = laid
    assert len(lines) > 1
    assert len({(l, r) for _t, l, r, _top in lines}) == 1
    for _t, l, r, top in lines:
        assert mask[top, l] and mask[top, r - 1]


def test_a_rectangle_that_is_a_small_part_of_the_balloon_defers_to_the_outline():
    mask = np.zeros((100, 100), dtype=bool)
    mask[:, 45:55] = True
    mask[45:55, :] = True        # a cross: the best rectangle is a bar
    draw = ImageDraw.Draw(Image.new("RGB", (100, 100)))
    from PIL import ImageFont
    assert scanlate_text_layout._layout_in_rectangle(
        draw, "hello there", mask, 18, lambda sz: ImageFont.load_default(sz)) is None


def test_render_text_in_box_uses_the_rectangle_only_when_asked():
    mask = _ellipse_mask(220, 160)
    box = {"x": 0, "y": 0, "w": 220, "h": 160}
    text = "this is a fairly long line of translated text"

    def drawn(**kw):
        img = Image.new("RGB", (220, 160), "white")
        scanlate_text_layout.render_text_in_box(img, box, text, font_size=20, mask=mask, **kw)
        return np.array(img)

    plain, rect = drawn(), drawn(rect_layout=True)
    assert not np.array_equal(plain, rect)
    top, left, bottom, right = scanlate_text_layout._roomiest_rectangle(mask)
    ink = np.argwhere(rect.min(axis=2) < 128)
    assert ink[:, 0].min() >= top and ink[:, 0].max() < bottom
    assert ink[:, 1].min() >= left and ink[:, 1].max() < right


# -- balanced lines ---------------------------------------------------------

def _first_fit(draw, text, font, avail):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and scanlate_text_layout._ink_width(draw, trial, font) > avail:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur]


STUB_TEXT = "I can't believe you actually did that to me"


def test_box_lines_are_balanced_with_the_same_line_count_and_no_stub():
    from PIL import ImageFont
    draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
    font = ImageFont.load_default(20)
    width = lambda t: scanlate_text_layout._ink_width(draw, t, font)  # noqa: E731
    avail = width("I can't believe you actually") + 2
    greedy = _first_fit(draw, STUB_TEXT, font, avail)
    assert len(greedy) == 2 and width(greedy[-1]) < 0.6 * width(greedy[0])
    lines = scanlate_text_layout._wrap_words(draw, STUB_TEXT, font, avail, hyphenate=False)
    assert " ".join(lines) == STUB_TEXT and len(lines) == len(greedy)
    assert all(width(ln) <= avail for ln in lines)
    assert min(map(width, lines)) >= 0.6 * max(map(width, lines))


def test_balancing_keeps_one_line_and_a_hyphenated_wrap_as_they_were():
    from PIL import ImageFont
    draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
    font = ImageFont.load_default(20)
    assert scanlate_text_layout._wrap_words(draw, "Short one", font, 300, False) == ["Short one"]
    narrow = scanlate_text_layout._ink_width(draw, "Unbelievab", font)
    hyphenated = scanlate_text_layout._wrap_words(draw, "Unbelievable", font, narrow, True)
    assert len(hyphenated) > 1 and hyphenated[0].endswith("-")


def test_balloon_lines_are_balanced_at_the_same_size_and_line_count():
    from PIL import ImageFont
    mask = np.ones((120, 300), dtype=bool)
    draw = ImageDraw.Draw(Image.new("RGB", (300, 120)))
    font = ImageFont.load_default(20)
    width = lambda t: scanlate_text_layout._ink_width(draw, t, font)  # noqa: E731
    span = width("I can't believe you actually") + 2
    mask[:, span:] = False
    laid = scanlate_text_layout._layout_at_size(draw, STUB_TEXT.split(), mask, 120, font, False)
    assert laid is not None
    got_font, placed = laid
    assert got_font is font
    lines = [t for t, *_ in placed]
    assert len(lines) == len(_first_fit(draw, STUB_TEXT, font, span)) == 2
    assert " ".join(lines) == STUB_TEXT
    assert min(map(width, lines)) >= 0.6 * max(map(width, lines))
