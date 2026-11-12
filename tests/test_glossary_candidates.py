"""glossary_candidates: recurring names found without a model. Lines are real shapes from the
stream and novel test files (a nickname that opens a line, a name inside longer words)."""
import importlib.util

import pytest

import glossary_candidates as gc

STREAM = ["8%仔是不是再喝一杯", "8%仔還敢敲", "20%仔勒 20%仔敢敲嗎", "20%仔喝多少了", "我很敲 20%仔喝多少了",
          "你們覺得洛克開始可愛起來了", "那個落口今天是唯一可以跟我比的人"]
NOVEL = ["畢藍站在天珠峰上", "天珠蛟從水面出現", "畢藍看見天珠蛟", "駱棋對畢藍說話", "駱棋走了", "畢藍低頭",
         "天珠峰的弟子", "駱棋笑了"]


# Cohesion is measured against the whole text, so a handful of lines proves nothing: pad with
# lines whose characters never form a repeated pair, as ordinary running text roughly does.
_POOL = "今天晚上我們一起去山裡看月亮聽風吹過樹林河水慢慢流向遠方雲朵變成各種形狀孩子們笑著跑開老人坐在門前講古早的故事春夏秋冬輪流來去"
FILLER = ["".join(_POOL[(i * 7 + j * 11 + i // 9) % len(_POOL)] for j in range(7)) for i in range(60)]


def texts(found):
    return [t for t, _ in found]


def test_a_nickname_that_opens_its_line_is_found():
    # 8%仔 only ever starts a line; counting a missing left neighbour as "no variety" lost it.
    assert "8%仔" in texts(gc.find_candidates(STREAM + FILLER))
    assert "20%仔" in texts(gc.find_candidates(STREAM + FILLER))


def test_names_repeated_across_lines_are_found_and_a_one_off_is_not():
    found = texts(gc.find_candidates(NOVEL + FILLER))
    assert "畢藍" in found and "駱棋" in found
    assert "弟子" not in found  # once


def test_a_term_already_in_the_glossary_is_left_out_with_its_pieces():
    found = texts(gc.find_candidates(NOVEL + FILLER, known_terms=["畢藍"]))
    assert "畢藍" not in found and "駱棋" in found


def test_a_piece_of_a_longer_equally_frequent_string_is_not_reported_twice():
    found = texts(gc.find_candidates(["畢藍說", "畢藍走", "畢藍笑"] * 2 + FILLER))
    assert not any(t != "畢藍" and t in "畢藍" for t in found)


def test_no_lines_or_blank_lines_give_nothing():
    assert gc.find_candidates([]) == []
    assert gc.find_candidates(["", None, "  "]) == []


@pytest.mark.skipif(importlib.util.find_spec("pypinyin") is None, reason="pypinyin not installed")
def test_nicknames_that_differ_only_in_the_number_sound_alike():
    groups = gc.group_by_sound(gc.find_candidates(STREAM + FILLER), "zh")
    assert any({"8%仔", "20%仔"} <= {t for t, _ in g} for g in groups)


def test_grouping_without_the_reading_library_is_empty(monkeypatch):
    monkeypatch.setattr(gc.ph, "availability", lambda lang: {"available": False})
    assert gc.group_by_sound([("洛克", 3), ("落口", 2)], "zh") == []
