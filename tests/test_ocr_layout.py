"""Reading order and furigana filtering of RapidOCR-style lines with boxes (mocked; no models)."""
import sys
import types

import pytest

import ocr
import ocr_layout
import scanlate_tuning


def quad(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# Balloon of three columns, right to left: おくつろぎの / ところ / 失礼いたします,
# with ruby columns (half width) beside the right-hand one, listed in a scrambled order.
COLUMNS = [
    ("失礼いたします", quad(200, 10, 230, 150)),
    ("しっれい", quad(232, 10, 245, 60)),
    ("ところ", quad(150, 10, 180, 80)),
    ("おくつろぎの", quad(100, 10, 130, 120)),
]


def read(texts_boxes, vertical=True):
    lines = ocr_layout.lines_from_boxes([t for t, _ in texts_boxes], [b for _, b in texts_boxes])
    return ocr_layout.layout_text(lines, vertical).split("\n")


def test_vertical_columns_read_right_to_left_without_the_ruby_column():
    assert read(COLUMNS) == ["失礼いたします", "ところ", "おくつろぎの"]


def test_a_column_split_in_two_lines_reads_top_to_bottom_before_the_next_column():
    parts = [("じょう", quad(200, 10, 230, 60)), ("ぶ", quad(100, 10, 130, 40)),
             ("む・いん", quad(201, 70, 229, 150))]
    assert read(parts) == ["じょう", "む・いん", "ぶ"]


def test_a_balloon_without_furigana_is_only_reordered():
    assert read([("下", quad(100, 10, 130, 60)), ("上", quad(200, 10, 230, 60))]) == ["上", "下"]


def test_small_text_with_no_larger_line_beside_it_is_kept():
    assert read([("ナレーション", quad(100, 10, 115, 90)), ("ひとこと", quad(300, 10, 315, 70))]) == [
        "ひとこと", "ナレーション"]


def test_a_small_mark_inside_the_column_is_not_ruby():
    assert read([("本気", quad(200, 10, 230, 80)), ("！", quad(208, 85, 222, 100))]) == ["本気", "！"]


def test_a_distant_small_caption_is_kept():
    assert read([("本気", quad(200, 10, 230, 80)), ("注", quad(20, 10, 35, 40))]) == ["本気", "注"]


def test_horizontal_ruby_above_the_line_is_dropped_and_rows_read_top_down():
    rows = [("二行目", quad(10, 60, 100, 90)), ("ふり", quad(10, 5, 50, 20)),
            ("一行目", quad(10, 22, 100, 52))]
    assert read(rows, vertical=False) == ["一行目", "二行目"]


def test_the_ratio_is_tunable(monkeypatch):
    monkeypatch.setattr(scanlate_tuning, "FURIGANA_MAX_THICKNESS_RATIO", 0.3)
    assert "しっれい" in read(COLUMNS)


@pytest.fixture
def fake_rapidocr(monkeypatch):
    def install(result):
        fake = types.ModuleType("rapidocr")
        fake.RapidOCR = lambda params=None: lambda path: result
        fake.LangRec = fake.ModelType = fake.OCRVersion = types.SimpleNamespace(
            KOREAN="ko", MEDIUM="medium", MOBILE="mobile", PPOCRV5="v5")
        monkeypatch.setitem(sys.modules, "rapidocr", fake)
        monkeypatch.setattr(ocr, "ensure_rapidocr_model", lambda spec: "/fake/" + spec["file"])
        monkeypatch.delitem(vars(ocr), "_rapidocr_instances", raising=False)
    yield install
    monkeypatch.delitem(vars(ocr), "_rapidocr_instances", raising=False)


def test_rapidocr_text_follows_the_boxes(fake_rapidocr):
    class Result:
        txts = tuple(t for t, _ in COLUMNS)
        boxes = [b for _, b in COLUMNS]

    fake_rapidocr(Result())
    assert ocr.extract_text_from_images(
        ["/fake.png"], backend="rapidocr", source_language="ja", vertical=True
    ) == "失礼いたします\nところ\nおくつろぎの"


def test_rapidocr_without_boxes_keeps_its_own_order(fake_rapidocr):
    class Result:
        txts = ("b", "a")
        boxes = None

    fake_rapidocr(Result())
    assert ocr.extract_text_rapidocr("/fake.png", vertical=True) == "b\na"
