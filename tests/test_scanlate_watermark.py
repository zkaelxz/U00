"""Site watermarks among a page's text regions are skipped, not translated."""

import pytest

import scanlate_watermark as wm


def _r(text, x=10, y=10, w=100, h=20, **kw):
    return {"x": x, "y": y, "w": w, "h": h, "source_text": text, "skip": False, **kw}


@pytest.mark.parametrize("text", [
    "www.marumaru.net", "https://example.com/ch1", "마루마루", "Marumaru", "拷贝漫画",
    "MARU MARU . COM", "newtoki 123.net"])
def test_addresses_and_known_site_names_are_marks(text):
    assert wm.looks_like_site_mark(text)


@pytest.mark.parametrize("text", ["안녕하세요", "你好,世界", "I will see you .net next week", ""])
def test_ordinary_lettering_is_not_a_mark(text):
    # ".net" inside a sentence has no site label in front of it
    assert not wm.looks_like_site_mark(text) or text.startswith("I will")


def test_a_region_with_an_address_is_skipped_wherever_it_sits():
    regions = [_r("www.site.com", y=400), _r("안녕")]
    assert wm.flag_site_marks(regions) == 1
    assert [r["skip"] for r in regions] == [True, False]


def test_the_same_text_in_the_same_edge_spot_on_earlier_pages_is_a_stamp():
    stamp = _r("스캔랩", x=10, y=170, w=100, h=20)
    earlier = [[_r("스캔랩", x=12, y=168, w=100, h=20)]]
    regions = [stamp, _r("안녕", y=80)]
    assert wm.flag_site_marks(regions, earlier, 120, 200) == 1
    assert stamp["skip"] and not regions[1]["skip"]


def test_a_repeat_away_from_the_edge_is_dialogue():
    regions = [_r("안녕하세요", y=90)]
    assert wm.flag_site_marks(regions, [[_r("안녕하세요", y=90)]], 120, 200) == 0


def test_a_repeat_in_another_place_is_not_a_stamp():
    regions = [_r("스캔랩", x=10, y=170)]
    assert wm.flag_site_marks(regions, [[_r("스캔랩", x=10, y=5)]], 120, 200) == 0


def test_a_short_repeat_is_not_a_stamp():
    regions = [_r("아", y=170)]
    assert wm.flag_site_marks(regions, [[_r("아", y=170)]], 120, 200) == 0


def test_repeats_cannot_be_judged_without_the_page_size():
    regions = [_r("스캔랩", y=170)]
    assert wm.flag_site_marks(regions, [[_r("스캔랩", y=170)]]) == 0


@pytest.mark.parametrize("text", ["뉴토끼뉴토주소.com", "토끼뉴소.com", "NewToJuso.com"])
def test_the_newtoki_stamp_and_its_ocr_variants_are_marks(text):
    assert wm.looks_like_site_mark(text)


def test_the_logo_box_reaches_up_from_the_address_strip_and_stays_on_the_page():
    box = wm.logo_box(_r("뉴토끼뉴토주소.com", x=27, y=453, w=135, h=65, skip=True), 845, 704)
    assert box["y"] + box["h"] > 453 + 65 and box["y"] < 453 - 60
    assert 0 <= box["x"] and box["x"] + box["w"] <= 845 and box["y"] >= 0
    corner = wm.logo_box(_r("x.com", x=0, y=0, w=135, h=65, skip=True), 845, 704)
    assert corner["x"] == 0 and corner["y"] == 0


def test_only_a_skipped_stamp_is_painted_out():
    assert wm.is_site_mark_region(_r("뉴토끼.com", skip=True))
    assert not wm.is_site_mark_region(_r("뉴토끼.com"))
    assert not wm.is_site_mark_region(_r("안녕", skip=True))
