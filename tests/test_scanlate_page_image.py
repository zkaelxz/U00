"""Scanlate's page-with-picture translation: what is sent, how the answer is applied, and the
fallback to the text-only request. The provider call is a double."""

import io
import json
import os

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

import db
from scanlate import scanlate_translate
from engine_backends import vision
from services import scanlate_run_service as run_svc
from tests.saved_settings import patch_setting


class _Engine:
    supports_reference = True
    client = object()
    name = "claude"
    model = "claude-sonnet-5-5"


def _region(text, x=10, y=20, w=100, h=40, **kw):
    return {"x": x, "y": y, "w": w, "h": h, "source_text": text, "translated_text": "", "skip": False,
            "kind": "bubble", **kw}


def _answer(translations, summary="Two people argue."):
    return json.dumps({"translations": translations, "context_summary": summary}), \
        {"input_tokens": 3000, "output_tokens": 200}


def test_the_picture_goes_out_with_the_regions_and_the_answer_is_matched_by_id(monkeypatch):
    sent = {}

    def ask(engine, prompt, image, mime, max_tokens=0, on_done=None):
        sent.update(prompt=prompt, image=image, mime=mime, max_tokens=max_tokens)
        # Reversed on purpose: position must not matter.
        return _answer({"1": "Don't go.", "0": "Why are you avoiding me?"})

    monkeypatch.setattr(vision, "ask_about_image", ask)
    usage = []
    regions = [{"id": "0", "text": "왜 나를 피하는 거야?", "box": {"x": 1, "y": 2, "w": 3, "h": 4}},
               {"id": "1", "text": "가지마", "box": {"x": 5, "y": 6, "w": 7, "h": 8}}]
    got, context = scanlate_translate.translate_regions_with_image(
        regions, b"PNGDATA", "image/png", _Engine(), {"source_language": "ko"},
        previous_context="Before.", usage_cb=lambda i, o: usage.append((i, o)),
        glossary_terms=None, character_notes="", chapter_brief="A prison romance.")
    assert got == {"0": "Why are you avoiding me?", "1": "Don't go."}
    assert context == "Two people argue."
    assert sent["image"] == b"PNGDATA" and sent["mime"] == "image/png"
    assert "[1, 2, 3, 4]" in sent["prompt"] and "Before." in sent["prompt"]
    assert "A prison romance." in sent["prompt"] and "Korean" in sent["prompt"]
    assert usage == [(3000, 200)]


def test_an_answer_that_misses_an_id_applies_nothing(monkeypatch):
    monkeypatch.setattr(vision, "ask_about_image",
                        lambda *a, **k: _answer({"0": "only one"}))
    regions = [{"id": "0", "text": "a", "box": {"x": 0, "y": 0, "w": 1, "h": 1}},
               {"id": "1", "text": "b", "box": {"x": 0, "y": 0, "w": 1, "h": 1}}]
    got, context = scanlate_translate.translate_regions_with_image(
        regions, b"x", "image/png", _Engine(), {"source_language": "zh"}, previous_context="keep")
    assert got is None and context == "keep"


def test_a_page_translation_applies_the_answer_and_scales_the_boxes(monkeypatch):
    seen = {}

    def with_image(regions, data, mime, engine, drama, **kw):
        seen["regions"] = regions
        return {r["id"]: "EN hello" for r in regions}, "ctx"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    bubbles = [_region("你好", x=100, y=200, w=40, h=20), _region("世界", skip=True)]
    notes = []
    ok, context = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "zh"}, None,
                                     "", 1, notes, page_image=(b"x", "image/png", 0.5))
    assert ok and context == "ctx" and notes == []
    assert bubbles[0]["translated_text"] == "EN hello" and bubbles[1]["translated_text"] == ""
    assert seen["regions"] == [{"id": "0", "text": "你好", "box": {"x": 50, "y": 100, "w": 20, "h": 10}}]


def test_a_failed_picture_request_falls_back_to_the_text_request(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("provider said no sk-ant-secretsecretsecretsecret")

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", broken)
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id",
                        lambda keyed, *a, **k: ({k_: "from text" for k_ in keyed}, "text ctx"))
    bubbles = [_region("你好")]
    notes = []
    ok, context = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "zh"}, None,
                                     "", 1, notes, page_image=(b"x", "image/png", 1.0))
    assert ok and context == "text ctx" and bubbles[0]["translated_text"] == "from text"
    assert notes and notes[0][0] == "info" and "text alone" in notes[0][1]
    assert "secretsecret" not in notes[0][1]


def test_a_dense_page_sends_the_picture_with_each_chunk_in_turn(monkeypatch):
    calls, texts = [], []

    def with_image(regions, data, mime, engine, drama, previous_context="", character_notes="", **kw):
        calls.append(([r["id"] for r in regions], previous_context, character_notes))
        if len(calls) == 3:
            return None, previous_context   # an unusable answer: only these ids go as text
        return {r["id"]: "seen " + r["id"] for r in regions}, f"after part {len(calls)}"

    def by_text(keyed, *a, **k):
        texts.append(list(keyed))
        return {k_: "text " + k_ for k_ in keyed}, "text ctx"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", by_text)
    per_call = scanlate_translate.REGIONS_PER_CALL
    bubbles = [_region(f"文{i}") for i in range(2 * per_call + 1)]
    ok, context = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "zh"}, None,
                                     "", 1, [], page_image=(b"x", "image/png", 1.0))
    assert ok and [len(ids) for ids, _c, _n in calls] == [per_call, per_call, 1]
    assert [c for _i, c, _n in calls] == ["", "after part 1", "after part 2"]
    assert "part 2 of 3" in calls[1][2] and "Answer only the listed ids" in calls[1][2]
    assert texts == [[str(2 * per_call)]]
    assert bubbles[0]["translated_text"] == "seen 0" and bubbles[-1]["translated_text"] == f"text {2 * per_call}"
    assert context == "after part 2"


def test_a_dense_quick_read_page_keeps_its_picture_and_sends_only_the_rest_as_text(monkeypatch):
    calls, texts = [], []

    def with_image(regions, data, mime, engine, drama, **kw):
        calls.append(len(regions))
        return ({r["id"]: "seen" for r in regions}, "c") if len(calls) == 1 else (None, "x")

    def by_text(keyed, *a, **k):
        texts.append(len(keyed))
        return {k_: "text" for k_ in keyed}, "t"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", by_text)
    per_call = scanlate_translate.REGIONS_PER_CALL
    bubbles = [_region(f"文{i}") for i in range(per_call + 5)]
    scanlate_translate.translate_page_bubbles(
        bubbles, _Engine(), {"source_language": "zh"},
        page_image={"data": b"x", "mime": "image/png", "scale": 1.0})
    assert calls == [per_call, 5] and texts == [5]
    assert [b["translated_text"] for b in bubbles] == ["seen"] * per_call + ["text"] * 5


def test_a_page_that_fits_one_call_gets_no_chunk_note(monkeypatch):
    notes = []

    def with_image(regions, data, mime, engine, drama, character_notes="", **kw):
        notes.append(character_notes)
        return {r["id"]: "t" for r in regions}, "c"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    bubbles = [_region(f"文{i}") for i in range(scanlate_translate.REGIONS_PER_CALL)]
    ok, _ = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "zh"}, None,
                               "", 1, [], page_image=(b"x", "image/png", 1.0))
    assert ok and len(notes) == 1 and "part 1 of" not in notes[0]


def test_a_page_that_loses_its_picture_says_so_in_the_log(monkeypatch):
    import types
    import applog
    messages = []
    monkeypatch.setattr(applog, "get_logger", lambda: types.SimpleNamespace(
        info=lambda msg, *a: messages.append(("info", msg % a)),
        warning=lambda msg, *a: messages.append(("warning", msg % a))))
    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image",
                        lambda *a, **k: (None, "ctx"))
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id",
                        lambda keyed, *a, **k: ({k_: "t" for k_ in keyed}, "c"))
    picture = {"data": b"x", "mime": "image/png", "scale": 1.0}
    scanlate_translate.translate_page_bubbles([_region("你好")], _Engine(), {"source_language": "zh"},
                                              page_image=picture)
    assert any(level == "warning" and "did not match" in m and "the page again from its text only" in m
               for level, m in messages)
    # A dense page whose second chunk lost its picture sends only that chunk as text.
    messages.clear()
    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image",
                        lambda chunk, *a, **k: ({r["id"]: "t" for r in chunk}, "c")
                        if chunk[0]["id"] == "0" else (None, "c"))
    count = scanlate_translate.REGIONS_PER_CALL + 1
    scanlate_translate.translate_page_bubbles([_region(f"文{i}") for i in range(count)], _Engine(),
                                              {"source_language": "zh"}, page_image=picture)
    left = count - scanlate_translate.REGIONS_PER_CALL
    assert [m for level, m in messages if level == "warning"] == [
        "Comic translate: no usable answer with the picture (answer did not match the regions), "
        f"translating the {left} of {count} regions it did not answer from their text only"]


def _stored_page(drama_id, width=120, height=80):
    folder = os.path.join(db.drama_dir(drama_id), "pages")
    os.makedirs(folder, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "PNG")
    with open(os.path.join(folder, "page_0000.png"), "wb") as f:
        f.write(buf.getvalue())
    return db.create_page(drama_id, 0, "pages/page_0000.png", width, height)


def test_the_page_picture_needs_the_setting_and_an_engine_that_reads_pictures(isolated_db, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    pid = _stored_page(did)
    patch_setting(monkeypatch, "scanlate_page_image", True)
    data, mime, scale = run_svc._page_image(did, pid, "claude")
    assert data and mime == "image/jpeg" and scale == 1.0
    ja = db.create_drama(title_en="Manga", source_language="ja")
    assert run_svc._page_image(ja, _stored_page(ja), "claude")[1] == "image/png"
    # DeepSeek takes no pictures, but its page is already off the PC, so Gemma may read it.
    assert run_svc._page_image(did, pid, "deepseek") is not None
    assert run_svc._page_image(did, pid, "ollama", "translategemma:27b") is not None
    assert run_svc._page_image(did, pid, "nonesuch") is None
    patch_setting(monkeypatch, "scanlate_page_image", False)
    assert run_svc._page_image(did, pid, "claude") is None


def test_a_page_goes_as_jpeg_only_for_a_language_known_to_read_fine_that_way():
    from PIL import JpegImagePlugin

    from services import image_text_service
    buf = io.BytesIO()
    Image.new("RGBA", (64, 48), (200, 30, 30, 128)).save(buf, "PNG")
    for language in ("zh", "ko"):
        data, mime, scale = run_svc.page_picture(buf.getvalue(), language)
        assert mime == "image/jpeg" and scale == 1.0
        with Image.open(io.BytesIO(data)) as sent:
            # 4:4:4: small coloured lettering keeps its edges.
            assert sent.format == "JPEG" and JpegImagePlugin.get_sampling(sent) == 0
    for language in ("ja", None, "", "th", "xx"):
        assert run_svc.page_picture(buf.getvalue(), language)[1] == "image/png"
    assert run_svc.page_picture(buf.getvalue())[1] == "image/png"
    assert image_text_service._prepared(buf.getvalue())[1] == "image/png"
    clear = io.BytesIO()
    Image.new("RGBA", (64, 48), (0, 0, 0, 0)).save(clear, "PNG")
    with Image.open(io.BytesIO(run_svc.page_picture(clear.getvalue(), "zh")[0])) as sent:
        assert min(sent.convert("L").getextrema()) > 245  # a see-through page reads as paper, not black


def test_a_title_without_a_language_sends_png(isolated_db, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="")
    pid = _stored_page(did)
    patch_setting(monkeypatch, "scanlate_page_image", True)
    assert run_svc._page_image(did, pid, "claude")[1] == "image/png"


def test_a_missing_page_file_means_a_text_only_translation(isolated_db, monkeypatch):
    did = db.create_drama(title_en="Comic", source_language="zh")
    pid = db.create_page(did, 0, "pages/gone.png", 120, 80)
    patch_setting(monkeypatch, "scanlate_page_image", True)
    assert run_svc._page_image(did, pid, "claude") is None


def test_a_claude_engine_reads_the_picture_as_haiku_and_logs_that_model(monkeypatch):
    built = {}

    class _Haiku(_Engine):
        model = "claude-haiku-5-5"

    def get_engine(name, key, model=None, **kw):
        built.update(name=name, model=model)
        return _Haiku()

    monkeypatch.setattr(run_svc.engine_registry, "get_engine", get_engine)
    monkeypatch.setattr(run_svc.translate_service, "resolve_api_key", lambda n: "k")
    assert run_svc._picture_engine(_Engine(), "claude").model == "claude-haiku-5-5"
    assert built == {"name": "claude", "model": "claude-haiku-5-5"}
    other = _Engine()
    assert run_svc._picture_engine(other, "gemini") is other


def test_the_chosen_engine_is_kept_when_haiku_cannot_be_built(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("no key")

    monkeypatch.setattr(run_svc.engine_registry, "get_engine", boom)
    engine = _Engine()
    assert run_svc._picture_engine(engine, "claude") is engine


def test_the_prompt_asks_for_an_empty_answer_for_a_watermark_not_a_note(monkeypatch):
    sent = {}

    def ask(engine, prompt, image, mime, max_tokens=0, on_done=None):
        sent["prompt"] = prompt
        return _answer({"0": "", "1": "Hello."})

    monkeypatch.setattr(vision, "ask_about_image", ask)
    regions = [{"id": "0", "text": "구글검색 뉴토끼", "box": {"x": 1, "y": 2, "w": 3, "h": 4}},
               {"id": "1", "text": "안녕", "box": {"x": 5, "y": 6, "w": 7, "h": 8}}]
    got, _ = scanlate_translate.translate_regions_with_image(
        regions, b"x", "image/png", _Engine(), {"source_language": "ko"})
    assert got == {"0": "", "1": "Hello."}
    assert 'answer "" (an empty string)' in sent["prompt"]


def test_both_prompts_ask_for_natural_dialogue_and_only_the_blind_one_forbids_guessing_gender(monkeypatch):
    sent = {}

    def ask(engine, prompt, image, mime, max_tokens=0, on_done=None):
        sent["picture"] = prompt
        return _answer({"0": "Hello."})

    monkeypatch.setattr(vision, "ask_about_image", ask)
    scanlate_translate.translate_regions_with_image(
        [{"id": "0", "text": "안녕", "box": {"x": 1, "y": 2, "w": 3, "h": 4}}], b"x", "image/png",
        _Engine(), {"source_language": "ko"})

    from engine_backends import llm_tasks

    def call(engine, prompt, **kw):
        sent["text_only"] = prompt
        return json.dumps({"translations": {"0": "Hello."}, "context_summary": "x"})

    monkeypatch.setattr(llm_tasks, "call_llm_json", call)
    scanlate_translate.translate_regions_by_id({"0": "안녕"}, _Engine(), {"source_language": "ko"})

    for key in ("picture", "text_only"):
        assert "word-for-word gloss" in sent[key] and "drop nothing" in sent[key]
        assert "romanised" in sent[key]
    assert "never guess he or she" in sent["text_only"]
    assert "never guess he or she" not in sent["picture"]


class _AnswersByCall:
    """Stands in for translate_regions_by_id: answers[n] is the dict returned
    on call n (None = unusable answer); records the ids each call asked for."""

    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, keyed, *a, **k):
        self.asked.append(sorted(keyed))
        answer = self.answers[len(self.asked) - 1] if len(self.asked) <= len(self.answers) else None
        return (answer, "ctx") if answer is not None else (None, "old")


def test_a_region_the_picture_answer_dropped_is_retried_alone(monkeypatch):
    # A picture answer that misses an id is rejected whole; the text request that
    # replaces it used to lose the page when it, too, dropped one id.
    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image",
                        lambda *a, **k: (None, ""))
    asked = _AnswersByCall([{"0": "first"}, {"1": "second"}])
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", asked)
    bubbles = [_region("a"), _region("b")]
    notes = []
    ok, _ = run_svc._translate(bubbles, _Engine(), "claude", {"source_language": "ko"}, None,
                               "", 1, notes, page_image=(b"x", "image/png", 1.0))
    assert ok and notes == []
    assert [b["translated_text"] for b in bubbles] == ["first", "second"]
    assert asked.asked == [["0", "1"], ["1"]]


def test_a_page_whose_every_answer_was_unusable_gets_one_more_try(monkeypatch):
    asked = _AnswersByCall([None, {"0": "worked"}])
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", asked)
    bubbles = [_region("a")]
    ok, _ = run_svc._translate(bubbles, _Engine(), "deepl", {"source_language": "ko"}, None,
                               "", 1, [])
    assert ok and bubbles[0]["translated_text"] == "worked"


def test_a_region_that_stays_untranslated_is_still_reported(monkeypatch):
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id",
                        _AnswersByCall([{"0": "one"}]))
    bubbles = [_region("a"), _region("b")]
    notes = []
    ok, _ = run_svc._translate(bubbles, _Engine(), "deepl", {"source_language": "ko"}, None,
                               "", 1, notes)
    assert not ok and any("1 of 2 regions weren't translated" in n[1] for n in notes)


def test_an_empty_answer_for_a_region_is_flagged_not_silent(monkeypatch):
    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id",
                        lambda keyed, *a, **k: ({"0": "Hello", "1": ""}, "ctx"))
    bubbles = [_region("a"), _region("b")]
    notes = []
    ok, _ = run_svc._translate(bubbles, _Engine(), "deepl", {"source_language": "ko"}, None,
                               "", 1, notes)
    assert ok
    assert any(n[0] == "warning" and "1 of 2 regions came back with an empty" in n[1] for n in notes)


def test_ids_written_beside_the_translations_object_are_still_read(monkeypatch):
    # The V100 blind run's raw answer: a stray } after the first id.
    raw = ('{"translations": {"0": "...making me go crazy."}, "1": "What are you thinking, '
           'Manager Myung?", "context_summary": "s"}')
    monkeypatch.setattr(vision, "ask_about_image", lambda *a, **k: (raw, {}))
    regions = [{"id": "0", "text": "a", "box": {"x": 0, "y": 0, "w": 1, "h": 1}},
               {"id": "1", "text": "b", "box": {"x": 0, "y": 0, "w": 1, "h": 1}}]
    got, _ = scanlate_translate.translate_regions_with_image(
        regions, b"x", "image/png", _Engine(), {"source_language": "ko"})
    assert got == {"0": "...making me go crazy.", "1": "What are you thinking, Manager Myung?"}


@pytest.mark.parametrize("stop", ["cancel", "run_stopped", "timeout", "headroom"])
def test_a_stop_signal_from_a_retry_sends_no_further_requests(monkeypatch, stop):
    from engine_backends.run_limits import TranslateRunStopped
    from engine_backends.shared import LLMTaskTimeout, TranslationCancelled
    from memory_headroom import HeadroomError
    error = {"cancel": TranslationCancelled(), "run_stopped": TranslateRunStopped("limit"),
             "timeout": LLMTaskTimeout("slow"), "headroom": HeadroomError("low")}[stop]
    calls = []

    def stops(keyed, *a, **k):
        calls.append(sorted(keyed))
        raise error

    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", stops)
    texts = {"0": "a", "1": "b", "2": "c"}
    with pytest.raises(type(error)):
        scanlate_translate.retry_missing_regions(texts, {}, _Engine(), {})
    assert calls == [["0", "1", "2"]]


def test_an_ordinary_error_in_one_retry_does_not_stop_the_others(monkeypatch):
    def flaky(keyed, *a, **k):
        if len(keyed) > 1 or "0" in keyed:
            raise RuntimeError("provider hiccup")
        return {k_: "ok" for k_ in keyed}, "ctx"

    monkeypatch.setattr(scanlate_translate, "translate_regions_by_id", flaky)
    got = scanlate_translate.retry_missing_regions({"0": "a", "1": "b"}, {}, _Engine(), {})
    assert got == {"1": "ok"}


class _Gemma(_Engine):
    name = "ollama"
    client = None
    model = "gemma4:31b-cloud"


@pytest.fixture
def ollama_up(monkeypatch):
    """Ollama answers; engines are doubles named after what was asked for."""
    built = []

    def get_engine(name, key, model=None, **kw):
        built.append((name, model))
        return _Gemma() if name == "ollama" else type("_Built", (_Engine,), {"model": model})()

    monkeypatch.setattr(run_svc.engine_registry, "get_engine", get_engine)
    monkeypatch.setattr(run_svc.translate_service, "resolve_api_key", lambda n: "k")
    monkeypatch.setattr(run_svc.engine_local, "check_ollama_reachable", lambda url: True)
    monkeypatch.setattr(run_svc.engine_local, "member_cloud_key", lambda: None)
    monkeypatch.setattr(run_svc, "_ollama_picture_reader", run_svc._ollama_picture_reader.real)
    return built


def _reader_models(readers):
    return [(name, engine.model) for engine, name in readers]


def test_gemma_on_ollama_reads_the_picture_first_and_haiku_is_the_fallback(ollama_up):
    assert _reader_models(run_svc.picture_readers(_Engine(), "claude")) == [
        ("ollama", "gemma4:31b-cloud"), ("claude", "claude-haiku-5-5")]
    gemini = type("_Gemini", (_Engine,), {"name": "gemini", "model": "gemini-flash"})()
    assert _reader_models(run_svc.picture_readers(gemini, "gemini")) == [
        ("ollama", "gemma4:31b-cloud"), ("gemini", "gemini-flash")]


def test_deepseek_gets_gemma_for_the_picture_since_it_takes_none_itself(ollama_up):
    deepseek = type("_DeepSeek", (_Engine,), {"name": "deepseek", "model": "deepseek-chat"})()
    assert _reader_models(run_svc.picture_readers(deepseek, "deepseek")) == [
        ("ollama", "gemma4:31b-cloud")]


def test_a_model_on_this_pc_never_sends_the_page_to_ollamas_servers(ollama_up):
    local = type("_Local", (_Gemma,), {"model": "translategemma:27b"})()
    assert _reader_models(run_svc.picture_readers(local, "ollama")) == [
        ("ollama", "translategemma:27b")]
    assert ollama_up == []


def test_a_gemma_run_reads_the_picture_with_itself_once(ollama_up):
    assert _reader_models(run_svc.picture_readers(_Gemma(), "ollama")) == [
        ("ollama", "gemma4:31b-cloud")]


def test_without_a_running_ollama_the_old_picture_reader_is_used(ollama_up, monkeypatch):
    monkeypatch.setattr(run_svc.engine_local, "check_ollama_reachable", lambda url: False)
    assert _reader_models(run_svc.picture_readers(_Engine(), "claude")) == [
        ("claude", "claude-haiku-5-5")]
    deepseek = type("_DeepSeek", (_Engine,), {"name": "deepseek", "model": "deepseek-chat"})()
    assert run_svc.picture_readers(deepseek, "deepseek") == []


def test_a_members_own_ollama_key_needs_no_ollama_on_the_pc(ollama_up, monkeypatch):
    monkeypatch.setattr(run_svc.engine_local, "check_ollama_reachable", lambda url: False)
    monkeypatch.setattr(run_svc.engine_local, "member_cloud_key", lambda: "member-key")
    assert _reader_models(run_svc.picture_readers(_Engine(), "claude"))[0] == (
        "ollama", "gemma4:31b-cloud")


def test_gemma_not_signed_in_falls_back_to_haiku_and_is_skipped_on_later_pages(ollama_up, monkeypatch):
    asked, logged = [], []

    def with_image(regions, data, mime, engine, drama, **kw):
        asked.append(engine.model)
        if engine.model == "gemma4:31b-cloud":
            raise run_svc.engine_local.OllamaUnavailableError(
                "ollama_cloud_signin", "Ollama cloud models need you to be signed in.")
        kw["usage_cb"](100, 10)
        return {r["id"]: "EN" for r in regions}, "ctx"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    monkeypatch.setattr(run_svc.paid_calls, "usage_logger",
                        lambda did, name, engine, op: lambda i, o: logged.append((name, engine.model)))
    notes = []
    ok, _ = run_svc._translate([_region("你好")], _Engine(), "claude", {"source_language": "zh"},
                               None, "", 1, notes, page_image=(b"x", "image/png", 1.0))
    assert ok and asked == ["gemma4:31b-cloud", "claude-haiku-5-5"]
    assert logged == [("claude", "claude-haiku-5-5")]
    assert "gemma4:31b-cloud failed" in notes[0][1] and "trying claude-haiku-5-5" in notes[0][1]
    asked.clear()
    ok, _ = run_svc._translate([_region("你好")], _Engine(), "claude", {"source_language": "zh"},
                               None, "", 1, [], page_image=(b"x", "image/png", 1.0))
    assert ok and asked == ["claude-haiku-5-5"]


def test_a_gemma_picture_read_is_logged_as_ollama(ollama_up, monkeypatch):
    logged = []
    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image",
                        lambda regions, *a, **kw: (kw["usage_cb"](100, 10)
                                                   or ({r["id"]: "EN" for r in regions}, "ctx")))
    monkeypatch.setattr(run_svc.paid_calls, "usage_logger",
                        lambda did, name, engine, op: lambda i, o: logged.append((name, engine.model)))
    deepseek = type("_DeepSeek", (_Engine,), {"name": "deepseek", "model": "deepseek-chat"})()
    ok, _ = run_svc._translate([_region("你好")], deepseek, "deepseek", {"source_language": "zh"},
                               None, "", 1, [], page_image=(b"x", "image/png", 1.0))
    assert ok and logged == [("ollama", "gemma4:31b-cloud")]


def test_the_gemma_request_carries_the_page_picture(monkeypatch):
    from engine_backends import local
    seen = {}

    def chat(base_url, payload):
        seen.update(payload)
        return {"message": {"content": _answer({"0": "Hello."})[0]},
                "prompt_eval_count": 900, "eval_count": 20}

    monkeypatch.setattr(vision, "_ollama_chat", chat)
    got, _ = scanlate_translate.translate_regions_with_image(
        [{"id": "0", "text": "你好", "box": {"x": 1, "y": 2, "w": 3, "h": 4}}], b"PNGDATA",
        "image/png", local.OllamaEngine(model="gemma4:31b-cloud"), {"source_language": "zh"})
    assert got == {"0": "Hello."}
    assert seen["model"] == "gemma4:31b-cloud"
    assert seen["messages"][0]["images"] == ["UE5HREFUQQ=="]


def test_an_engine_on_this_pc_other_than_ollama_never_gets_gemma(ollama_up):
    fake = type("_Fake", (_Engine,), {"name": "fake", "model": "fake"})()
    assert run_svc.picture_readers(fake, "fake") == [] and ollama_up == []


def test_a_gemma_timeout_falls_back_instead_of_stopping_the_run(ollama_up, monkeypatch):
    asked = []

    def with_image(regions, data, mime, engine, drama, **kw):
        asked.append(engine.model)
        if engine.model == "gemma4:31b-cloud":
            raise run_svc.LLMTaskTimeout("slow")
        return {r["id"]: "EN" for r in regions}, "ctx"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    notes = []
    ok, _ = run_svc._translate([_region("你好")], _Engine(), "claude", {"source_language": "zh"},
                               None, "", 1, notes, page_image=(b"x", "image/png", 1.0))
    assert ok and asked == ["gemma4:31b-cloud", "claude-haiku-5-5"] and "timed out" in notes[0][1]


def test_an_ollama_that_is_not_answering_is_not_asked_again_on_every_page(ollama_up, monkeypatch):
    probes = []
    monkeypatch.setattr(run_svc.engine_local, "check_ollama_reachable",
                        lambda url: probes.append(url) or False)
    run_svc.picture_readers(_Engine(), "claude")
    run_svc.picture_readers(_Engine(), "claude")
    assert len(probes) == 1


def test_a_members_own_key_ignores_the_pcs_ollama_failure(ollama_up, monkeypatch):
    monkeypatch.setattr(run_svc, "_ollama_reader_down_until", float("inf"))
    monkeypatch.setattr(run_svc.engine_local, "member_cloud_key", lambda: "member-key")
    assert _reader_models(run_svc.picture_readers(_Engine(), "claude"))[0] == (
        "ollama", "gemma4:31b-cloud")


def test_the_extension_tries_gemma_then_haiku_and_a_gemma_failure_starts_the_skip(ollama_up, monkeypatch):
    from services import extension_service
    asked = []

    def with_image(regions, data, mime, engine, drama, **kw):
        asked.append(engine.model)
        if engine.model == "gemma4:31b-cloud":
            raise run_svc.engine_local.OllamaUnavailableError("ollama_cloud_signin", "sign in")
        return {r["id"]: "EN" for r in regions}, "ctx"

    monkeypatch.setattr(scanlate_translate, "translate_regions_with_image", with_image)
    patch_setting(monkeypatch, "scanlate_page_image", True)
    monkeypatch.setattr(run_svc, "load_translation_guide", lambda drama: (None, ""))
    monkeypatch.setattr(run_svc, "page_picture", lambda image, language=None: (b"x", "image/png", 1.0))
    extras = extension_service.page_extras({}, {"engine": "claude"}, _Engine(), b"png")
    bubbles = [{"source_text": "你好", "x": 1, "y": 2, "w": 3, "h": 4}]
    scanlate_translate.translate_page_bubbles(bubbles, _Engine(), {}, **extras)
    assert asked == ["gemma4:31b-cloud", "claude-haiku-5-5"] and bubbles[0]["translated_text"] == "EN"
    assert run_svc.picture_readers(_Engine(), "claude")[0][1] == "claude"


def test_a_chapter_keeps_the_names_its_pages_reported(monkeypatch):
    # Quick read pages translate side by side, so a name spelled on one page is sent with the
    # next; only a name written on the page itself is kept, and the first spelling stays.
    from engine_backends import llm_tasks
    prompts, replies = [], [
        {"translations": {"0": "Aro, wait!"}, "context_summary": "x",
         "names": {"아로": "Aro", "유령": "Ghost"}},
        {"translations": {"0": "Aro runs."}, "context_summary": "y", "names": {"아로": "Arrow"}},
    ]

    def call(engine, prompt, **kw):
        prompts.append(prompt)
        return json.dumps(replies[len(prompts) - 1])
    monkeypatch.setattr(llm_tasks, "call_llm_json", call)
    names = {}
    scanlate_translate.translate_page_bubbles([_region("아로, 기다려!")], _Engine(),
                                              {"source_language": "ko"}, chapter_names=names)
    assert names == {"아로": "Aro"}
    assert '"names"' in prompts[0] and "spell them exactly the same" not in prompts[0]
    scanlate_translate.translate_page_bubbles([_region("아로가 달린다.")], _Engine(),
                                              {"source_language": "ko"}, chapter_names=names)
    assert "아로 → Aro" in prompts[1]
    assert names == {"아로": "Aro"}


def test_a_page_outside_a_chapter_is_not_asked_for_names(monkeypatch):
    from engine_backends import llm_tasks
    prompts = []

    def call(engine, prompt, **kw):
        prompts.append(prompt)
        return json.dumps({"translations": {"0": "Hi."}, "context_summary": "x"})
    monkeypatch.setattr(llm_tasks, "call_llm_json", call)
    scanlate_translate.translate_page_bubbles([_region("안녕")], _Engine(), {"source_language": "ko"})
    assert '"names"' not in prompts[0]


def test_comic_translations_come_back_without_em_dashes(monkeypatch):
    from engine_backends import llm_tasks
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda engine, prompt, **kw: json.dumps(
        {"translations": {"0": "Wait—", "1": "He—the king—came."}, "context_summary": "x"}))
    got, _ = scanlate_translate.translate_regions_by_id({"0": "잠깐", "1": "왕이 왔다"}, _Engine(),
                                                        {"source_language": "ko"})
    assert got == {"0": "Wait...", "1": "He, the king, came."}
