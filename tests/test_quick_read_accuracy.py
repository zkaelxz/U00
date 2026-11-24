"""Quick read's accuracy aids: the previous page's lines beside a page, and the second look at low-confidence text."""

import io
import threading

import pytest

import ocr
import page_turns
from scanlate import scanlate_translate
from services import page_reread_service as reread


def _png(width=200, height=120) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(out, "PNG")
    return out.getvalue()


def _bubble(text, conf=None, **over):
    return {"x": 10, "y": 10, "w": 80, "h": 40, "source_text": text, "ocr_confidence": conf,
            "kind": "bubble", "skip": False, **over}


class TestPreviousLines:
    def test_the_first_page_has_none_and_the_next_sees_source_then_english(self):
        order = page_turns.PageOrder(2, ordered_translation=False)
        first, second = order.turn(0), order.turn(1)
        bubbles = [_bubble("彼は誰？"), _bubble("ドン", kind="sfx"), _bubble("", conf=None),
                   _bubble("隠し", skip=True)]
        first.keep(bubbles)
        assert first.previous_lines() == []
        assert second.previous_lines() == [{"source": "彼は誰？", "english": ""}]
        bubbles[0]["translated_text"] = "Who is he?"
        assert second.previous_lines() == [{"source": "彼は誰？", "english": "Who is he?"}]

    def test_a_page_that_failed_to_read_does_not_hold_the_next_one_up(self, monkeypatch):
        monkeypatch.setattr(page_turns, "PREVIOUS_LINES_WAIT_SECONDS", 5.0)
        order = page_turns.PageOrder(2, ordered_translation=False)
        done = []
        thread = threading.Thread(target=lambda: done.append(order.turn(1).previous_lines()))
        thread.start()
        order.finish(0)
        thread.join(2)
        assert done == [[]]

    def test_only_a_page_worth_of_lines_is_kept(self):
        order = page_turns.PageOrder(2, ordered_translation=False)
        order.turn(0).keep([_bubble(f"行{i}") for i in range(40)])
        assert len(order.turn(1).previous_lines()) == page_turns.PREVIOUS_LINES_MAX

    def test_an_unordered_turn_has_no_lines(self):
        page_turns.UNORDERED.keep([_bubble("x")])
        assert page_turns.UNORDERED.previous_lines() == []


class TestPreviousPagePrompt:
    def test_block_marks_untranslated_lines_and_forbids_answering_them(self):
        block = scanlate_translate.previous_page_block(
            [{"source": "彼は？", "english": "Who?"}, {"source": "あの人", "english": ""}])
        assert "1. 彼は？ => Who?" in block
        assert block.endswith("2. あの人")
        assert "Do not translate these lines" in block
        assert scanlate_translate.previous_page_block([]) == ""

    def test_the_block_reaches_the_translation_request(self, monkeypatch):
        seen = {}

        def fake(texts, engine, meta, **kwargs):
            seen.update(kwargs)
            return {"0": "Hello"}, "ctx"

        monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", fake)
        bubbles = [_bubble("こんにちは")]
        scanlate_translate.translate_page_bubbles(
            bubbles, object(), {}, character_notes="CAST",
            previous_lines=[{"source": "前", "english": ""}])
        assert seen["character_notes"].startswith("CAST\n\nThe previous page")
        assert bubbles[0]["translated_text"] == "Hello"


class TestConfidenceFromOcr:
    def test_the_mean_line_score_is_kept_once_per_read(self, monkeypatch):
        class Result:
            txts = ("あ", "い")
            scores = (0.9, 0.5)
            boxes = None

        monkeypatch.setattr(ocr, "_run_rapidocr", lambda key, source: Result())
        ocr.take_read_confidence()
        assert ocr.extract_text_rapidocr("/fake.png", "ja") == "あ\nい"
        assert ocr.take_read_confidence() == pytest.approx(0.7)
        assert ocr.take_read_confidence() is None


class TestHardRegions:
    def test_only_low_scores_are_picked_worst_first_and_capped(self):
        bubbles = [_bubble("a", 0.95), _bubble("b", 0.7), _bubble("c", 0.4), _bubble("d", None),
                   _bubble("e", 0.5, skip=True), _bubble("f", 0.5, kind="sfx"),
                   _bubble("g", 0.85, kind="caption"), _bubble("h", 0.85)]
        assert [b["source_text"] for b in reread.hard_regions(bubbles)] == ["c", "b", "g"]
        many = [_bubble(str(i), 0.1 + i / 100) for i in range(10)]
        assert len(reread.hard_regions(many)) == reread.MAX_REGIONS_PER_PAGE

    @pytest.mark.parametrize("answer,expected", [
        ("魔王が来た", "魔王が来た"), ("「魔王が来た」", "魔王が来た"), ("UNREADABLE", ""),
        ("I cannot read this.", ""), ("", ""), ("魔王" * 40, "")])
    def test_a_reading_is_used_only_when_it_is_the_lettering(self, answer, expected):
        assert reread.clean_reading(answer, "魔王", "ja") == expected


class TestReread:
    def _readers(self, monkeypatch, answers):
        calls = []

        def ask(engine, prompt, image, mime, max_tokens=0):
            calls.append({"prompt": prompt, "mime": mime, "size": len(image)})
            answer = answers[len(calls) - 1] if isinstance(answers, list) else answers
            if isinstance(answer, Exception):
                raise answer
            return answer, {"input_tokens": 10, "output_tokens": 2}

        monkeypatch.setattr(reread.vision, "ask_about_image", ask)
        spent = []
        return calls, spent, [{"engine": object(), "usage_cb": lambda i, o: spent.append((i, o))}]

    def test_a_low_confidence_line_gets_the_picture_models_reading(self, monkeypatch):
        calls, spent, readers = self._readers(monkeypatch, "魔王が来た")
        bubbles = [_bubble("魔玉が来た", 0.5), _bubble("正しい", 0.97)]
        assert reread.reread(bubbles, _png(), "ja", readers) == 1
        assert bubbles[0]["source_text"] == "魔王が来た" and bubbles[1]["source_text"] == "正しい"
        assert len(calls) == 1 and calls[0]["mime"] == "image/jpeg" and "魔玉が来た" in calls[0]["prompt"]
        assert spent == [(10, 2)]

    def test_a_failed_or_unusable_answer_keeps_the_ocr_text(self, monkeypatch):
        _, _, readers = self._readers(monkeypatch, [RuntimeError("down"), "sorry, no"])
        bubbles = [_bubble("魔玉", 0.5), _bubble("勇書", 0.4)]
        assert reread.reread(bubbles, _png(), "ja", readers) == 0
        assert [b["source_text"] for b in bubbles] == ["魔玉", "勇書"]

    def test_nothing_is_asked_without_a_reader_or_a_hard_line(self, monkeypatch):
        calls, _, readers = self._readers(monkeypatch, "x")
        assert reread.reread([_bubble("魔玉", 0.5)], _png(), "ja", []) == 0
        assert reread.reread([_bubble("魔王", 0.99)], _png(), "ja", readers) == 0
        assert calls == []


def test_quick_read_rereads_before_matching_the_glossary(isolated_db, monkeypatch):
    from services import extension_service, page_server
    monkeypatch.setattr(extension_service, "page_extras",
                        lambda *a: {"page_image": {"readers": [{"engine": object()}]}})
    monkeypatch.setattr(reread.vision, "ask_about_image",
                        lambda *a, **k: ("勇者が来た", {"input_tokens": 1, "output_tokens": 1}))
    bubbles = [_bubble("勇有が来た", 0.4)]
    extras = page_server._page_extras(None, {}, None, _png(), bubbles, "ja", None, reread=True)
    assert bubbles[0]["source_text"] == "勇者が来た"
    assert "勇者 → default: Hero" in extras["character_notes"]
    # A saved page keeps the read it stored, so it is not corrected.
    other = [_bubble("勇有が来た", 0.4)]
    page_server._page_extras(None, {}, None, _png(), other, "ja", None)
    assert other[0]["source_text"] == "勇有が来た"


def test_a_page_translates_with_the_lines_of_the_page_before_it(isolated_db, monkeypatch):
    from services import page_server
    sent = {}

    def translate(bubbles, engine, meta, **kwargs):
        sent.update(kwargs)
        for b in bubbles:
            b["translated_text"] = "English"
        return "ctx"

    monkeypatch.setattr(scanlate_translate, "translate_page_bubbles", translate)
    monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
    order = page_turns.PageOrder(2, ordered_translation=False)
    order.turn(0).keep([_bubble("彼は誰？")])
    page_server._translate_read_page([_bubble("あの人だ")], {}, None, None, "", [], False, b"",
                                     False, "ja", order.turn(1))
    assert sent["previous_lines"] == [{"source": "彼は誰？", "english": ""}]


def test_a_caption_carries_its_weakest_line_score():
    from scanlate import scanlate_art_text as art
    grouped = art.group_lines([
        {"x": 10, "y": 10, "w": 300, "h": 40, "text": "AA", "conf": 0.9},
        {"x": 12, "y": 60, "w": 296, "h": 42, "text": "BB", "conf": 0.65}])
    assert grouped[0]["ocr_confidence"] == 0.65


class TestMisreadsSeenOnRealPages:
    def test_a_misread_banner_a_stylised_caption_and_a_lost_chapter_tag_are_all_picked(self):
        banner = _bubble("シクシーンバッズ部の人は", 0.55, x=0, y=0, w=800, h=60, kind="caption")
        caption = _bubble("大獣の直放言室", 0.7, w=300, h=60, kind="caption")
        tag = _bubble("的\n7", 0.97, w=300, h=40)
        fine = _bubble("追放する", 0.99, w=120, h=40)
        assert {b["source_text"] for b in reread.hard_regions([banner, caption, tag, fine])} == {
            banner["source_text"], caption["source_text"], tag["source_text"]}

    def test_a_short_word_in_a_small_balloon_is_left_alone(self):
        assert reread.hard_regions([_bubble("はい", 0.97, w=60, h=40)]) == []
        assert reread.hard_regions([_bubble("はい", None, w=60, h=40)]) == []

    def test_a_short_read_is_picked_even_when_the_backend_gives_no_score(self):
        assert len(reread.hard_regions([_bubble("的", None, w=300, h=40)])) == 1
