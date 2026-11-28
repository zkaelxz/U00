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
    monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
    sent = []

    def translate(bubbles, engine, meta, **kwargs):
        sent.append(([b["source_text"] for b in bubbles], kwargs.get("character_notes", "")))
        return ""

    monkeypatch.setattr(scanlate_translate, "translate_page_bubbles", translate)
    bubbles = [_bubble("勇有が来た", 0.4)]
    page_server._translate_read_page(bubbles, {}, None, None, "", [], False, _png(), False, "ja")
    assert bubbles[0]["source_text"] == "勇者が来た"
    assert sent[1][0] == ["勇者が来た"] and "勇者 → default: Hero" in sent[1][1]
    # A saved page keeps the read it stored, so it is not corrected.
    other = [_bubble("勇有が来た", 0.4)]
    page_server._translate_read_page(other, {}, None, None, "", [], False, _png(), True, "ja")
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


class TestStylisedLettering:
    def test_lettering_on_the_art_is_read_again_whatever_its_score(self):
        title = _bubble("突然の追放宣告", 0.988, kind="caption", w=300, h=60)
        assert reread.hard_regions([title]) == [title]

    def test_a_sign_with_no_score_is_read_again_but_a_balloon_with_none_is_not(self):
        banner = _bubble("シクシーンバッズ部の人は", None, kind="sign", w=800, h=60)
        balloon = _bubble("こんにちは", None, w=200, h=60)
        assert reread.hard_regions([banner, balloon]) == [banner]

    def test_a_scored_sign_is_left_to_its_score(self):
        assert reread.hard_regions([_bubble("看板", 0.97, kind="sign", w=200, h=60)]) == []

    def test_the_least_certain_go_first_when_the_page_has_more_than_the_cap(self):
        sure = [_bubble(f"題{i}", 0.99, kind="caption", w=300, h=60) for i in range(4)]
        shaky = _bubble("怪しい", 0.3, w=300, h=60)
        picked = reread.hard_regions(sure + [shaky])
        assert picked[0] is shaky and len(picked) == reread.MAX_REGIONS_PER_PAGE


def test_an_unscored_banner_shaped_box_is_read_again_even_as_a_balloon():
    banner = _bubble("シクシーンバッズ部の人は", None, w=800, h=60)
    assert reread.hard_regions([banner]) == [banner]
    assert reread.hard_regions([_bubble("シクシーンバッズ部の人は", 0.97, w=800, h=60)]) == []


def test_a_tall_unscored_column_is_not_taken_for_a_banner():
    assert reread.hard_regions([_bubble("縦書きの文字です", None, w=40, h=400)]) == []


class TestTitleMemory:
    """Names, pronouns and language Quick read learns from a title's pages, kept across its
    chapters (no saved series, so no glossary or cast list to take them from)."""

    @staticmethod
    def _reply(monkeypatch, replies, prompts):
        import json
        from engine_backends import llm_tasks

        def call(engine, prompt, **kw):
            prompts.append(prompt)
            return json.dumps(replies[len(prompts) - 1])
        monkeypatch.setattr(llm_tasks, "call_llm_json", call)

    def test_a_pronoun_a_page_showed_is_sent_with_the_next_page(self, monkeypatch):
        prompts = []
        self._reply(monkeypatch, [
            {"translations": {"0": "Mira, wait!"}, "context_summary": "x",
             "names": {"ミラ": "Mira"}, "cast": {"Mira": "she", "the girl": "she", "Kenji": "he"}},
            {"translations": {"0": "Got it."}, "context_summary": "y", "cast": {}},
        ], prompts)
        names, cast = {}, {}
        scanlate_translate.translate_page_bubbles([_bubble("ミラ、待って！")], _Engine(),
                                                  {"source_language": "ja"}, chapter_names=names,
                                                  cast=cast)
        # Only a name this page's English or the title's names use becomes a character.
        assert cast == {"Mira": {"she": 1}}
        assert '"cast"' in prompts[0] and "pronouns, from earlier pages" not in prompts[0]
        scanlate_translate.translate_page_bubbles([_bubble("分かった")], _Engine(),
                                                  {"source_language": "ja"}, chapter_names=names,
                                                  cast=cast)
        assert "Mira: she" in prompts[1]

    def test_a_tied_pronoun_is_not_sent(self):
        assert scanlate_translate.settled_pronouns({"Ren": {"she": 1, "he": 1},
                                                    "Mira": {"she": 2, "he": 1}}) == {"Mira": "she"}
        assert scanlate_translate.cast_block({"Ren": {"she": 1, "he": 1}}) == ""

    def test_pages_in_flight_together_add_only_their_own_votes(self, monkeypatch):
        def translate(bubbles, engine, meta, cast=None, **kw):
            cast.setdefault("Mira", {})["she"] = cast.get("Mira", {}).get("she", 0) + 1
            return "ctx"
        monkeypatch.setattr(scanlate_translate, "translate_page_bubbles", translate)
        page_turns.seed_title_memory(None, "t", {"cast": {"Mira": {"she": 2}}})
        for _ in range(2):
            page_turns.translate_in_chain(None, "t", [], object(), None, None, None,
                                          wait_for_turn=False)
        assert page_turns.title_memory(None, "t")["cast"] == {"Mira": {"she": 4}}

    @pytest.mark.parametrize("url, title", [
        ("https://rawkuma.net/manga/psycho-eyes/chapter-1/", "rawkuma:psycho-eyes"),
        ("https://site.invalid/comic/abc-chapter-12", "site.invalid/comic/abc"),
        ("https://site.invalid/comic/abc/ch-3/", "site.invalid/comic/abc"),
        ("https://site.invalid/m/123/第3话", "site.invalid/m/123"),
        # Nothing that reads as a chapter: the link stays its own title.
        ("https://site.invalid/comic/12345/678.html", "https://site.invalid/comic/12345/678.html"),
        ("https://site.invalid/12", "https://site.invalid/12"),
    ])
    def test_a_chapter_link_names_its_title(self, url, title):
        from services import quick_read_cache
        assert quick_read_cache.title_key(url) == title

    def test_a_page_with_no_language_takes_its_sources_then_what_the_title_showed(self, isolated_db):
        from services import page_server
        assert page_server._page_source_language(
            None, "", "https://rawkuma.net/manga/psycho-eyes/chapter-1/") == "ja"
        url = "https://site.invalid/comic/abc/chapter-1"
        assert page_server._page_source_language(None, "", url) == "zh"
        # Kana read by the Chinese reader: the title is Japanese, and its next chapter knows it.
        assert page_server._learn_page_language([_bubble("あの人は誰ですか")], "zh", url) == "ja"
        assert page_server._page_source_language(None, "", url.replace("-1", "-2")) == "ja"
        assert page_server._page_source_language(None, "ko", url) == "ko"
        assert page_server._page_source_language({"source_language": "zh"}, "", url) == "zh"

    def test_the_next_chapter_starts_from_what_the_last_one_learned(self, isolated_db, monkeypatch):
        from services import page_server
        seen = []

        def translate(bubbles, engine, meta, chapter_names=None, cast=None, **kw):
            seen.append((meta, dict(chapter_names), {k: dict(v) for k, v in cast.items()}))
            chapter_names.setdefault("ミラ", "Mira")
            cast.setdefault("Mira", {"she": 1})
            for b in bubbles:
                b["translated_text"] = "English"
            return "ctx"
        monkeypatch.setattr(scanlate_translate, "translate_page_bubbles", translate)
        monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
        base = "https://site.invalid/comic/abc/chapter-"
        page_server._translate_read_page([_bubble("ミラ")], {}, None, None, base + "1", [], False,
                                         b"", False, "ja")
        # A restart forgets what is in memory; the title's memory on disk is still there.
        with page_turns._context_lock:
            page_turns._chapter_names.clear()
            page_turns._cast.clear()
        page_server._translate_read_page([_bubble("ミラ")], {}, None, None, base + "2", [], False,
                                         b"", False, "ja")
        assert seen[0] == ({"source_language": "ja"}, {}, {})
        assert seen[1] == ({"source_language": "ja"}, {"ミラ": "Mira"}, {"Mira": {"she": 1}})


class _Engine:
    name = "claude"
    supports_reference = True
    client = object()


@pytest.mark.parametrize("sent, used", [("ja", "ja"), ("KO", "ko"), ("en", ""), ("<x>", ""), (None, "")])
def test_the_language_a_sender_read_off_the_page_reaches_the_reader(isolated_db, monkeypatch, sent, used):
    import base64
    from services import extension_bridge_service as bridge
    from services import page_server
    seen = []

    def fake_translate(data, content_type, **kw):
        seen.append(kw["source_language"])
        return {"regions": [], "notes": []}
    monkeypatch.setattr(page_server, "translate_image", fake_translate)
    from PIL import Image
    out = io.BytesIO()
    picture = Image.new("RGB", (600, 900), (240, 240, 240))
    picture.paste((10, 10, 10), (150, 225, 450, 450))   # a blank page is refused before reading
    picture.save(out, "PNG")
    image = {"key": "a", "data": base64.b64encode(out.getvalue()).decode(), "content_type": "image/png"}
    bridge.run_images({"store": False, "filter_pages": False, "source_language": sent}, [image])
    assert seen == [used]


class _LLM:
    supports_reference = True


class TestEnglishOnlyAnswers:
    def _llm(self, monkeypatch, answers):
        """Each call to the engine answers with the next dict of {id: text}."""
        import json
        from engine_backends import llm_tasks
        prompts = []

        def fake(engine, prompt, max_tokens=2000, fallback="[]", usage_cb=None):
            prompts.append(prompt)
            return json.dumps({"translations": answers.pop(0), "context_summary": "s"})
        monkeypatch.setattr(llm_tasks, "call_llm_json", fake)
        return prompts

    def test_prompt_demands_english_only(self, monkeypatch):
        prompts = self._llm(monkeypatch, [{"0": "Hi"}])
        scanlate_translate.translate_regions_by_id({"0": "やあ"}, _LLM(), {"source_language": "ja"})
        assert "English only" in prompts[0]

    def test_a_leaked_term_is_asked_for_again_and_the_clean_answer_kept(self, monkeypatch):
        prompts = self._llm(monkeypatch, [{"0": "An accomplice of the Notice Criminal"}])
        keyed = {"0": "予告犯の共犯者"}
        result, leaking = scanlate_translate.recheck_source_script(
            keyed, {"0": "An accomplice of the 予告犯 (Yokokuhan)"}, _LLM(), {"source_language": "ja"})
        assert result == {"0": "An accomplice of the Notice Criminal"}
        assert leaking == []
        assert "予告犯 (Yokokuhan)" in prompts[0]

    def test_a_retry_that_still_leaks_is_flagged_and_never_worse(self, monkeypatch):
        self._llm(monkeypatch, [{"0": "The 予告犯 and 共犯 came"}])
        result, leaking = scanlate_translate.recheck_source_script(
            {"0": "x"}, {"0": "The 予告犯 came"}, _LLM(), {})
        assert result == {"0": "The 予告犯 came"}
        assert leaking == ["0"]

    def test_clean_answers_cost_no_extra_call(self, monkeypatch):
        prompts = self._llm(monkeypatch, [])
        result, leaking = scanlate_translate.recheck_source_script(
            {"0": "x"}, {"0": "“Fine.” Murata Yusuke"}, _LLM(), {})
        assert (result, leaking, prompts) == ({"0": "“Fine.” Murata Yusuke"}, [], [])

    def test_quick_read_bubbles_get_the_clean_retry(self, monkeypatch):
        self._llm(monkeypatch, [{"0": "Who is the 予告犯?"}, {"0": "Who is the Notice Criminal?"}])
        bubbles = [_bubble("予告犯は誰？")]
        scanlate_translate.translate_page_bubbles(bubbles, _LLM(), {"source_language": "ja"})
        assert bubbles[0]["translated_text"] == "Who is the Notice Criminal?"


class TestTranslateBesideReread:
    URL = "https://site.invalid/comic/race/chapter-1"

    def _setup(self, monkeypatch, reading):
        from services import extension_service, page_server
        started = threading.Event()
        asked, overlapped = [], []

        def translate(bubbles, engine, meta, chapter_names=None, cast=None, **kw):
            asked.append([b["source_text"] for b in bubbles])
            started.set()
            chapter_names.setdefault(bubbles[0]["source_text"], f"Name{len(asked)}")
            cast.setdefault(f"Name{len(asked)}", {})["she"] = 1
            for b in bubbles:
                b["translated_text"] = f"English {len(asked)}"
            return f"ctx {len(asked)}"

        def ask(*a, **k):
            overlapped.append(started.wait(5))
            return reading, {"input_tokens": 1, "output_tokens": 1}

        monkeypatch.setattr(scanlate_translate, "translate_page_bubbles", translate)
        monkeypatch.setattr(reread.vision, "ask_about_image", ask)
        monkeypatch.setattr(extension_service, "page_extras",
                            lambda *a: {"page_image": {"readers": [{"engine": object()}]}})
        monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
        from collections import OrderedDict

        class Summaries(OrderedDict):
            def __setitem__(self, key, value):
                self.written = [*getattr(self, "written", []), value]
                super().__setitem__(key, value)

        monkeypatch.setattr(page_turns, "_contexts", Summaries())
        monkeypatch.setattr(page_turns, "_chapter_names", {})
        monkeypatch.setattr(page_turns, "_cast", {})
        return page_server, asked, overlapped

    def _memory(self, part="names"):
        from services import quick_read_cache
        return page_turns.title_memory(None, quick_read_cache.title_key(self.URL))[part]

    def _cast(self):
        return self._memory("cast")

    def test_a_reread_that_changes_nothing_keeps_the_answer_started_beside_it(self, isolated_db, monkeypatch):
        page_server, asked, overlapped = self._setup(monkeypatch, "勇者が来た")
        bubbles = [_bubble("勇者が来た", 0.4)]
        split = {}
        page_server._translate_read_page(bubbles, {}, None, None, self.URL, [], False, _png(),
                                         False, "ja", split=split)
        assert overlapped == [True]
        assert asked == [["勇者が来た"]]
        assert bubbles[0]["translated_text"] == "English 1"
        assert split["reread_changed"] == 0
        assert self._memory() == {"勇者が来た": "Name1"}

    def test_a_corrected_reading_is_translated_again_and_the_early_answer_leaves_nothing(
            self, isolated_db, monkeypatch):
        page_server, asked, overlapped = self._setup(monkeypatch, "勇者が来た")
        bubbles = [_bubble("勇有が来た", 0.4)]
        page_server._translate_read_page(bubbles, {}, None, None, self.URL, [], False, _png(),
                                         False, "ja")
        for thread in threading.enumerate():
            if thread.name == "quick-read-early-translate":
                thread.join(5)
        assert overlapped == [True]
        assert asked == [["勇有が来た"], ["勇者が来た"]]
        assert bubbles[0]["source_text"] == "勇者が来た"
        assert bubbles[0]["translated_text"] == "English 2"
        assert "勇有が来た" not in self._memory()
        assert self._cast() == {"Name2": {"she": 1}}
        assert page_turns._contexts.written == ["ctx 2"]

    def test_a_page_with_no_hard_text_translates_once_after_no_reread(self, isolated_db, monkeypatch):
        page_server, asked, overlapped = self._setup(monkeypatch, "x")
        bubbles = [_bubble("勇者が来た", 0.99)]
        page_server._translate_read_page(bubbles, {}, None, None, self.URL, [], False, _png(),
                                         False, "ja")
        assert asked == [["勇者が来た"]]
        assert overlapped == []
