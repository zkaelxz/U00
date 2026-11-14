"""Scanlate layout on pages like real ones: columns of one balloon, thought dots, column order, cleanup."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import ocr_layout
import scanlate
import scanlate_detect
import scanlate_inpaint
import scanlate_regions


def _page(balloons, size=(300, 400), ground=70):
    """Grey page with white ellipse balloons (black outline) and dark lettering blocks."""
    img = np.full((size[0], size[1]), ground, np.uint8)
    for cx, cy, ax, ay in balloons:
        cv2.ellipse(img, (cx, cy), (ax, ay), 0, 0, 360, 0, -1)
        cv2.ellipse(img, (cx, cy), (ax - 3, ay - 3), 0, 0, 360, 255, -1)
    return img


def _box(x, y, w, h, label="text_bubble"):
    return {"x": x, "y": y, "w": w, "h": h, "label": label, "confidence": 0.5}


def _letters(img, x, y, w, h):
    for i in range(0, h, 22):
        img[y + i:y + i + 16, x:x + w] = 0


def test_two_columns_of_one_balloon_become_one_box():
    img = _page([(150, 150, 80, 110)])
    _letters(img, 175, 80, 24, 140)
    _letters(img, 120, 90, 24, 120)
    left, right = _box(115, 85, 35, 130), _box(168, 75, 38, 150)
    out = scanlate_detect.merge_fragment_boxes([left, right], img)
    assert len(out) == 1
    assert (out[0]["x"], out[0]["y"]) == (115, 75)


def test_boxes_of_two_separate_balloons_stay_apart():
    img = _page([(90, 150, 60, 100), (215, 150, 60, 100)])
    _letters(img, 80, 90, 24, 120)
    _letters(img, 205, 90, 24, 120)
    out = scanlate_detect.merge_fragment_boxes([_box(70, 80, 45, 140), _box(195, 80, 45, 140)], img)
    assert len(out) == 2


def test_adjacent_balloons_with_touching_boxes_stay_apart():
    img = _page([(95, 150, 55, 100), (200, 150, 55, 100)])
    _letters(img, 85, 90, 22, 120)
    _letters(img, 190, 90, 22, 120)
    out = scanlate_detect.merge_fragment_boxes([_box(60, 70, 100, 160), _box(150, 70, 100, 160)], img)
    assert len(out) == 2


@pytest.mark.parametrize("text, dropped", [
    ("o o", True), ("000o", True), ("O", True), ("o.o", True),
    ("……", False), ("我走了", False), ("ok", False), ("", False), ("0 yen", False),
])
def test_thought_dots_are_not_text(text, dropped):
    assert scanlate._is_thought_dots(text, "zh") is dropped


def test_latin_source_lettering_is_never_dropped_as_dots():
    assert scanlate._is_thought_dots("o o", "en") is False


def test_two_short_columns_read_right_to_left_even_if_called_horizontal():
    lines = ocr_layout.lines_from_boxes(
        ["不明白", "我完全"],
        [[[10, 12], [38, 12], [38, 90], [10, 90]], [[50, 5], [78, 5], [78, 80], [50, 80]]])
    assert ocr_layout.layout_text(lines, vertical=False) == "我完全\n不明白"


def test_horizontal_rows_still_read_left_to_right():
    lines = ocr_layout.lines_from_boxes(
        ["B", "A"], [[[100, 10], [180, 10], [180, 30], [100, 30]], [[10, 12], [90, 12], [90, 32], [10, 32]]])
    assert ocr_layout.layout_text(lines, vertical=False) == "A\nB"


def test_balloon_on_dark_art_is_erased_inside_its_outline_only():
    img = _page([(150, 150, 70, 100)])
    _letters(img, 135, 90, 24, 120)
    box = {"x": 70, "y": 40, "w": 160, "h": 220}   # reaches past the balloon on every side
    mask = scanlate_detect.bubble_erase_mask("x.png", box, image=img)
    assert mask is not None and not mask.all()
    assert not mask[150 - box["y"], 150 - 69 - box["x"]]      # on the outline ring, 69 px left of centre
    assert mask[150 - box["y"], 150 - box["x"]]


def _ghosting_balloon(noise_std):
    rng = np.random.default_rng(0)
    gray = np.full((120, 100), 250, np.uint8)
    gray[30:90, 40:60] = 20
    gray = np.clip(gray + rng.normal(0, noise_std, gray.shape), 0, 255).astype(np.uint8)
    halo = cv2.dilate((gray < 100).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    gray[halo & (gray > 200)] = 215                     # the faint ring a JPEG leaves
    roi = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return gray, roi


def test_faint_halo_round_lettering_is_painted_out_in_a_plain_white_balloon():
    gray, roi = _ghosting_balloon(1.0)
    mask = scanlate_inpaint._lettering_mask(gray)
    inside = np.ones(gray.shape, bool)
    inpainted = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    flat = scanlate_inpaint._flatten_plain_paper(roi, inpainted, mask, inside)
    core = np.zeros(gray.shape, bool)
    core[6:-6, 6:-6] = True
    assert cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)[core].min() >= 245


def test_a_toned_balloon_is_left_as_inpainted():
    rng = np.random.default_rng(1)
    gray = np.clip(rng.normal(200, 25, (120, 100)), 0, 255).astype(np.uint8)
    roi = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    mask = np.zeros(gray.shape, np.uint8)
    inpainted = roi.copy()
    out = scanlate_inpaint._flatten_plain_paper(roi, inpainted, mask, np.ones(gray.shape, bool))
    assert out is inpainted


def test_ellipsis_column_does_not_shrink_the_measured_lettering_size():
    gray = np.full((200, 80), 255, np.uint8)
    gray[20:150, 20:42] = 0       # a 22 px glyph column
    gray[160:176, 28:37] = 0      # its ellipsis: a 9 px run
    box = {"x": 0, "y": 0, "w": 80, "h": 200}
    assert scanlate_regions.lettering_size(gray, box, "vertical") >= 20


def test_a_sky_that_fades_behind_the_lettering_is_not_whitened_into_a_box():
    gray = np.full((140, 200), 254, np.uint8)
    gray[:50, :90] = 238                              # cloud: darker, far from the letters
    gray[60:110, 110:180] = 20
    roi = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    mask = scanlate_inpaint._lettering_mask(gray)
    inpainted = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    out = scanlate_inpaint._flatten_plain_paper(roi, inpainted, mask, np.ones(gray.shape, bool))
    assert out is inpainted
    assert cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)[10:40, 10:80].max() < 245



def _band_over_art_page():
    """Black-and-white hatched art with a white caption band tilted 8 degrees across it, a line of
    dark glyphs on the band and stray dark marks above it."""
    import cv2
    # Dense hatching: the paper-relative lettering mask grows across it and covers the crop.
    page = np.where((np.arange(400)[None, :] // 3 + np.arange(200)[:, None] // 40) % 2 == 0, 0, 255)
    page = page.astype(np.uint8)
    band = np.zeros_like(page)
    cv2.fillConvexPoly(band, np.array([[10, 75], [390, 105], [390, 135], [10, 105]]), 255)
    page[band > 0] = 255
    glyphs = np.zeros_like(page)
    for i in range(10):
        x = 30 + i * 34
        y = 85 + int(i * 34 * 0.0787)
        cv2.rectangle(glyphs, (x, y), (x + 10, y + 12), 255, 3)
        cv2.line(glyphs, (x + 2, y + 6), (x + 8, y + 6), 255, 2)
    page[glyphs > 0] = 0
    return page, band > 0, glyphs > 0


def test_a_line_of_lettering_on_a_white_band_is_erased_without_whitening_the_art():
    page, band, glyphs = _band_over_art_page()
    bgr = np.dstack([page] * 3).copy()
    box = {"x": 0, "y": 40, "w": 400, "h": 140}
    out = scanlate_inpaint.inpaint_region(None, box, image=bgr.copy(), backend="cv", on_art=True)
    out_gray = out[..., 0]
    assert (out_gray[glyphs] > 200).mean() > 0.9                    # the lettering is gone
    art = ~band
    art[:40] = art[180:] = False
    assert (out_gray[art] == page[art]).mean() > 0.99               # the art round it is untouched


def test_art_with_no_line_of_lettering_keeps_the_usual_mask():
    page, _band, _glyphs = _band_over_art_page()
    assert scanlate_inpaint._strokes_on_a_line(page[:60, :200]) is None
    assert scanlate_inpaint._strokes_on_a_line(page) is not None


def test_a_sign_that_is_one_line_on_a_tilted_band_is_set_along_the_band(tmp_path):
    import scanlate_render
    from PIL import Image
    page, band, _glyphs = _band_over_art_page()
    src = tmp_path / "band.png"
    Image.fromarray(page).convert("RGB").save(src)
    sign = {"x": 0, "y": 40, "w": 400, "h": 140, "kind": "sign", "kind_confidence": 0.5,
            "translated_text": "Traffic congestion on both lines, service suspended until further notice", "font_size": 40}
    out = scanlate_render.process_page(str(src), [sign], str(tmp_path / "out.png"))[0]
    result = np.asarray(Image.open(out).convert("L"))
    drawn = (result < 200) & (page > 200) & band          # text darkening the white band (antialiasing and font vary by machine)
    outside = (result != page) & ~band
    outside[:40] = outside[180:] = False
    assert drawn.sum() > 150
    assert outside.sum() < 0.01 * drawn.sum()             # none of the English spills onto the art
    ys, xs = np.nonzero(drawn)
    slope = np.polyfit(xs, ys, 1)[0]
    assert 0.04 < slope < 0.12                            # runs down to the right like the band


def _sign(x, y, w, h, confidence=0.5, **extra):
    return {"x": x, "y": y, "w": w, "h": h, "kind": "sign", "kind_confidence": confidence, **extra}


def _art_page():
    """Paper-white page with a dense mid-grey patch (a boot) and a sign with a few strokes."""
    gray = np.full((200, 300), 245, np.uint8)
    gray[20:140, 20:60] = 120
    gray[30:44, 150:230] = 0
    gray[60:74, 150:230] = 0
    return gray


def test_a_dense_dark_patch_boxed_as_a_sign_is_art():
    assert scanlate_inpaint.is_art_mistaken_for_sign(_art_page(), _sign(20, 20, 40, 120))


def test_a_sign_with_paper_between_its_strokes_is_lettering():
    assert not scanlate_inpaint.is_art_mistaken_for_sign(_art_page(), _sign(140, 20, 100, 70))


def test_a_confident_sign_or_another_kind_is_never_judged_as_art():
    page = _art_page()
    assert not scanlate_inpaint.is_art_mistaken_for_sign(page, _sign(20, 20, 40, 120, confidence=0.9))
    assert not scanlate_inpaint.is_art_mistaken_for_sign(page, dict(_sign(20, 20, 40, 120), kind="bubble"))


def test_art_boxed_as_a_sign_is_left_unerased_and_its_translation_set_beside_it(tmp_path):
    import scanlate_render
    from PIL import Image
    gray = _art_page()
    src = tmp_path / "p.png"
    Image.fromarray(gray).convert("RGB").save(src)
    art = _sign(20, 20, 40, 120, translated_text="Boot", font_size=18)
    sign = _sign(150, 25, 80, 55, translated_text="Closed", font_size=18)
    out = scanlate_render.process_page(str(src), [art, sign], str(tmp_path / "out.png"))[0]
    result = np.asarray(Image.open(out).convert("L"))
    assert (result[20:140, 20:60] == 120).all()                 # the art is untouched
    assert (result[20:140, 66:140] != 245).any()                # the gloss sits beside it
    assert (result[30:44, 150:230] != 0).any()                 # a real sign is still erased


def test_a_wide_caption_strip_is_lettering_even_when_its_mask_covers_the_box():
    gray = np.full((200, 400), 245, np.uint8)
    gray[20:60, 20:320] = 120
    assert not scanlate_inpaint.is_art_mistaken_for_sign(gray, _sign(20, 20, 300, 40))


def _gloss_rect(before, after):
    changed = np.argwhere((before != after).any(axis=2))
    (top, left), (bottom, right) = changed.min(axis=0), changed.max(axis=0)
    return left, top, right, bottom


def test_a_gloss_keeps_clear_of_other_regions_and_inside_its_panel():
    from PIL import Image
    import scanlate_sfx
    art = {"x": 100, "y": 100, "w": 40, "h": 100, "translated_text": "What's wrong?"}
    neighbour = {"x": 150, "y": 90, "w": 80, "h": 120}      # the nearest side, taken
    panel = {"x": 80, "y": 60, "w": 200, "h": 300}
    blank = np.full((400, 400, 3), 128, np.uint8)
    img = Image.fromarray(blank.copy())
    scanlate_sfx.draw_gloss(img, art, avoid=(neighbour,), bounds=panel)
    left, top, right, bottom = _gloss_rect(blank, np.asarray(img))
    assert not (left < 230 and right > 150 and top < 210 and bottom > 90)    # clear of the neighbour
    assert left >= 80 and top >= 60 and right <= 280 and bottom <= 360       # inside the panel


def test_a_sound_effect_gloss_with_nothing_to_avoid_goes_to_the_right_as_before():
    from PIL import Image
    import scanlate_sfx
    region = {"x": 100, "y": 100, "w": 40, "h": 100, "translated_text": "Boom"}
    blank = np.full((400, 400, 3), 128, np.uint8)
    img = Image.fromarray(blank.copy())
    scanlate_sfx.draw_gloss(img, region)
    left, top, _, _ = _gloss_rect(blank, np.asarray(img))
    assert left >= 140 and top >= 90


def _form(rule=True, drop=16):
    """Grey form cells with a label of separate glyphs in each, the cells parted by a slanted rule."""
    img = np.full((170, 200), 200, np.uint8)
    for top in (30, 110):
        for left in range(55, 125, 22):
            img[top:top + 16, left:left + 14] = 20
    if rule:
        cv2.line(img, (0, 78), (199, 78 + drop), 150, 1)
    return img


def test_form_labels_parted_by_a_rule_stay_apart():
    labels = [_box(45, 20, 100, 45, "text_free"), _box(45, 95, 100, 45, "text_free")]
    assert len(scanlate_detect.merge_fragment_boxes(labels, _form())) == 2
    assert len(scanlate_detect.merge_fragment_boxes(labels, _form(rule=False))) == 1


def _detect_with_scores(monkeypatch, img, scored):
    def ml(path, image=None, confidence=None, **kwargs):
        return [dict(b, confidence=s) for b, s in scored if s >= confidence]
    monkeypatch.setattr(scanlate_detect, "detect_bubbles_ml", ml)
    monkeypatch.setattr(scanlate_detect, "_ml_failure", None)
    return scanlate_detect.detect_bubbles("page.png", backend="ml", image=cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))


def test_a_weak_form_label_beside_a_kept_one_is_kept(monkeypatch):
    kept, weak = _box(45, 20, 100, 45, "text_free"), _box(45, 95, 100, 45, "text_free")
    found = _detect_with_scores(monkeypatch, _form(), [(kept, 0.3), (weak, 0.15)])
    assert [b["y"] for b in found] == [20, 95]


def test_a_weak_box_over_several_form_labels_is_dropped(monkeypatch):
    kept, weak = _box(45, 20, 100, 45, "text_free"), _box(45, 95, 100, 45, "text_free")
    both = _box(40, 15, 110, 150, "text_free")
    found = _detect_with_scores(monkeypatch, _form(drop=0), [(kept, 0.3), (both, 0.24), (weak, 0.15)])
    assert [(b["y"], b["h"]) for b in found] == [(20, 45), (95, 45)]


def test_a_weak_lettering_box_without_a_rule_to_a_kept_one_is_dropped(monkeypatch):
    kept, weak = _box(45, 20, 100, 45, "text_free"), _box(45, 95, 100, 45, "text_free")
    found = _detect_with_scores(monkeypatch, _form(rule=False), [(kept, 0.3), (weak, 0.15)])
    assert [b["y"] for b in found] == [20]
    found = _detect_with_scores(monkeypatch, _form(), [(kept, 0.3), (weak, 0.05)])
    assert [b["y"] for b in found] == [20]


def _cut_line_page(top):
    """A balloon whose first line of lettering is hollow glyphs cut off at row `top`, as on
    a page where a balloon runs on from the page before."""
    img = _page([(200, top + 20, 150, 120)])
    img[:top] = 70
    for x in (130, 175, 220):                       # a glyph like 口 with its top cut off
        img[top:top + 22, x:x + 4] = 0
        img[top:top + 22, x + 24:x + 28] = 0
        img[top + 18:top + 22, x:x + 28] = 0
    _letters(img, 140, top + 45, 100, 16)          # the line OCR read, wholly inside
    return img


def test_lettering_cut_off_by_the_page_edge_is_erased():
    img = _cut_line_page(0)
    box = {"x": 110, "y": 0, "w": 160, "h": 80}
    mask = scanlate_detect.bubble_erase_mask("x.png", box, image=img)
    assert mask is not None
    strokes = (img[:80, 110:270] == 0) & (np.arange(80)[:, None] < 22)
    assert mask[strokes].all()


def test_a_balloon_outline_cut_off_by_the_page_edge_is_not_erased():
    img = _page([(200, 10, 80, 60)], ground=255)        # white page: paper round the balloon
    img[0:22, 186:190] = img[0:22, 210:214] = img[18:22, 186:214] = 0   # a glyph the page cuts
    _letters(img, 170, 28, 60, 12)
    box = {"x": 100, "y": 0, "w": 200, "h": 90}
    mask = scanlate_detect.bubble_erase_mask("x.png", box, image=img)
    assert mask is not None
    outline = img[:90, 100:300] == 0
    outline[:50, 65:135] = False                        # the lettering, which may go
    assert outline.any() and not mask[outline].any()
    assert mask[:22, 86:114][img[:22, 186:214] == 0].all()


def test_art_in_the_notch_of_two_lobes_cut_off_by_the_page_edge_is_not_erased():
    img = np.full((300, 400), 70, np.uint8)             # grey art round the balloon
    for cx in (160, 250):
        cv2.ellipse(img, (cx, 40), (50, 60), 0, 0, 360, 0, -1)
    for cx in (160, 250):
        cv2.ellipse(img, (cx, 40), (47, 57), 0, 0, 360, 255, -1)
    for x in (135, 260):                                # glyphs the page cuts, one per lobe
        img[0:22, x:x + 4] = img[0:22, x + 24:x + 28] = img[18:22, x:x + 28] = 0
    box = {"x": 110, "y": 0, "w": 200, "h": 100}
    mask = scanlate_detect.bubble_erase_mask("x.png", box, image=img)
    notch = np.zeros((100, 200), bool)
    notch[:45, 85:105] = True                           # between the lobes, at the page edge
    notch &= img[:100, 110:310] < 128
    assert notch.any() and not mask[notch].any()
    assert mask[:22, 25:53][img[:22, 135:163] == 0].all()
