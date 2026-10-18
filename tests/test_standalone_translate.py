"""
tests/test_standalone_translate.py -- Step 26b: the standalone translate
tool. Covers translate_engines.build_standalone_instructions,
standalone_direction_support, chunk_standalone_text, and
standalone_translate -- the drama-translation prompt/pipeline itself
(build_llm_instructions, translate_lines_with_engine) is covered by
test_translate_engines.py and stays untouched by this step.
"""
import sys
import os
import json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import translate_engines as te


class TestBuildStandaloneInstructions:
    def test_to_english_names_both_languages(self):
        instructions = te.build_standalone_instructions("zh", "en")
        assert "Chinese" in instructions
        assert "English" in instructions

    def test_from_english_names_both_languages(self):
        instructions = te.build_standalone_instructions("en", "ja")
        assert "English" in instructions
        assert "Japanese" in instructions

    def test_is_not_subtitle_or_genre_specific(self):
        """Unlike build_llm_instructions (the drama prompt), this must
        never assume subtitles or the baihe/GL genre -- it's a generic
        text-translation prompt."""
        instructions = te.build_standalone_instructions("zh", "en")
        assert "baihe" not in instructions
        assert "subtitle" not in instructions.lower()


class TestBuildStablePromptStandaloneFlag:
    """context["standalone"] routes to the new generic prompt; every
    existing drama-translation call site (which never sets this flag)
    keeps using build_llm_instructions completely unchanged."""

    def test_standalone_flag_uses_the_generic_prompt(self):
        instructions, novel_block = te.build_stable_prompt(
            {"standalone": True, "source_language": "zh", "target_language": "en"})
        assert "baihe" not in instructions
        assert novel_block == ""

    def test_drama_context_without_the_flag_is_unchanged(self):
        instructions, _ = te.build_stable_prompt({"source_language": "zh", "drama_meta": {}})
        assert "baihe" in instructions

    def test_from_english_direction_reaches_the_generic_prompt(self):
        instructions, _ = te.build_stable_prompt(
            {"standalone": True, "source_language": "en", "target_language": "ko"})
        assert "English" in instructions
        assert "Korean" in instructions


class TestStandaloneDirectionSupport:
    """Step 26b item 6: which configured engine actually supports the
    requested direction. zh/ja/ko -> English is this app's existing,
    well-tested direction (every engine keeps doing it, unwarned).
    English -> zh/ja/ko is new: a pure-MT engine takes an explicit
    source+target pair so it's just as capable; the LLM engines are
    prompted directly; Ollama depends on whichever local model is
    loaded (attempted, with a warning)."""

    def test_to_english_is_always_supported_for_every_engine(self):
        for name in te.ENGINES:
            ok, message = te.standalone_direction_support(name, "zh", "en")
            assert ok is True
            assert message is None

    def test_pure_mt_engine_supports_english_to_cjk(self):
        for name in ("fake_mt",):
            ok, message = te.standalone_direction_support(name, "en", "zh")
            assert ok is True
            assert message is None

    def test_llm_engines_support_english_to_cjk(self):
        for name in ("claude", "deepseek", "gemini", "fake"):
            ok, message = te.standalone_direction_support(name, "en", "ja")
            assert ok is True
            assert message is None

    def test_ollama_is_attempted_with_a_warning_not_refused(self):
        ok, message = te.standalone_direction_support("ollama", "en", "zh")
        assert ok is True
        assert message
        assert "model" in message.lower()


class TestChunkStandaloneText:
    def test_short_text_is_a_single_chunk(self):
        assert te.chunk_standalone_text("Just one short paragraph.") == ["Just one short paragraph."]

    def test_empty_text_produces_no_chunks(self):
        assert te.chunk_standalone_text("   ") == []

    def test_paragraph_breaks_are_preserved_within_a_chunk(self):
        text = "Para one.\n\nPara two."
        chunks = te.chunk_standalone_text(text, max_chars_per_chunk=1000)
        assert chunks == ["Para one.\n\nPara two."]

    def test_long_text_splits_into_multiple_chunks(self):
        paragraphs = [f"Paragraph {i} " + "x" * 500 for i in range(6)]
        text = "\n\n".join(paragraphs)
        chunks = te.chunk_standalone_text(text, max_chars_per_chunk=1000)
        assert len(chunks) > 1
        rejoined = "\n\n".join(chunks)
        for p in paragraphs:
            assert p in rejoined

    def test_a_single_paragraph_longer_than_the_limit_is_kept_whole(self):
        long_paragraph = "y" * 5000
        chunks = te.chunk_standalone_text(long_paragraph, max_chars_per_chunk=1000)
        assert chunks == [long_paragraph]


class _ShufflingEngine:
    """Mimics a real LLM engine's translate_batch -- id-keyed under the
    hood, via the same _request_translations_with_retry every real LLM
    engine already uses -- but returns its JSON keys in reverse order.
    Proves standalone_translate's chunks are reassembled by id, not by
    the raw response's position (the exact bug
    _request_translations_with_retry's own docstring describes)."""
    name = "shuffle_llm"

    def translate_batch(self, zh_lines, context):
        def call_model(numbered):
            ids = list(range(1, len(zh_lines) + 1))
            pairs = {str(i): f"OUT:{zh_lines[i - 1]}" for i in ids}
            shuffled = dict(reversed(list(pairs.items())))
            return json.dumps(shuffled)
        return te.request_translations_with_retry(zh_lines, None, call_model)


class _CountingEngine:
    """Records whether translate_batch was ever actually called --
    standalone_translate must refuse an unsupported direction BEFORE
    calling it, never attempt it and hope for the best."""

    def __init__(self, name):
        self.name = name
        self.called = False

    def translate_batch(self, zh_lines, context):
        self.called = True
        return [f"X:{z}" for z in zh_lines]


class TestStandaloneTranslate:
    def test_to_english_direction_translates_and_joins_chunks(self):
        # Short enough that chunk_standalone_text groups both paragraphs
        # into a single chunk -- confirms the single-chunk path still
        # goes through the same id-keyed translate_batch call, prefixed
        # once for the whole chunk, not once per paragraph.
        engine = _ShufflingEngine()
        text = "First paragraph.\n\nSecond paragraph."
        result = te.standalone_translate(text, engine, "zh", "en")
        assert result == f"OUT:{text}"

    def test_long_text_is_chunked_and_reassembled_by_id_not_position(self):
        paragraphs = [f"Paragraph {i} " + "x" * 300 for i in range(8)]
        text = "\n\n".join(paragraphs)
        engine = _ShufflingEngine()
        expected_chunks = te.chunk_standalone_text(text)
        assert len(expected_chunks) > 1  # otherwise this test proves nothing

        result = te.standalone_translate(text, engine, "zh", "en")
        # Compared against the exact expected reassembly (not split back
        # apart) since a chunk can itself contain "\n\n" internally
        # (preserved paragraph breaks), which would make a naive re-split
        # ambiguous with the "\n\n" this function joins chunks with.
        assert result == "\n\n".join(f"OUT:{c}" for c in expected_chunks)

    def test_empty_text_returns_empty_string_without_calling_the_engine(self):
        engine = _CountingEngine("claude")
        assert te.standalone_translate("   ", engine, "zh", "en") == ""
        assert engine.called is False

    def test_unsupported_engine_direction_is_refused_before_translate_batch_runs(self, monkeypatch):
        monkeypatch.setattr("engine_backends.standalone.standalone_direction_support",
                            lambda *a: (False, "Not supported."))
        engine = _CountingEngine("fake_mt")
        with pytest.raises(te.UnsupportedDirectionError):
            te.standalone_translate("Hello there.", engine, "en", "zh")
        assert engine.called is False  # refused, never attempted

    def test_from_english_direction_reaches_the_engine(self):
        engine = _ShufflingEngine()
        result = te.standalone_translate("Hello world.", engine, "en", "ja")
        assert result == "OUT:Hello world."

    def test_batches_chunks_in_groups_not_all_in_one_call(self):
        """Mirrors translate_lines_with_engine's own batch_size -- a long
        paste shouldn't go to the engine as one unbounded request."""
        calls = []

        class _BatchCountingEngine:
            name = "claude"

            def translate_batch(self, zh_lines, context):
                calls.append(len(zh_lines))
                return [f"OUT:{z}" for z in zh_lines]

        paragraphs = [f"Paragraph {i} " + "x" * 300 for i in range(20)]
        text = "\n\n".join(paragraphs)
        te.standalone_translate(text, _BatchCountingEngine(), "zh", "en", batch_size=3)
        assert len(calls) > 1
        assert all(n <= 3 for n in calls)


class _BlobMergingEngine:
    """Translates per item like a real engine, but, handed one item holding
    several lines, answers with the numbering stripped and two lines merged:
    what a model did to pasted lines on a real run."""
    name = "claude"

    def __init__(self, collapse_numbered=False):
        self.calls = []
        self.collapse_numbered = collapse_numbered

    def translate_batch(self, zh_lines, context):
        self.calls.append(list(zh_lines))
        out = []
        for item in zh_lines:
            lines = [ln for ln in item.splitlines() if ln.strip()]
            if len(lines) > 1 and self.collapse_numbered and lines[0][:2] == "1.":
                out.append(f"OUT:{lines[0]}")
            elif len(lines) > 1:
                out.append("\n".join([f"OUT:{lines[0]} {lines[1]}"] + [f"OUT:{ln}" for ln in lines[2:]]))
            else:
                out.append(f"OUT:{item}")
        return out


class TestOneTranslatedLinePerInputLine:
    def test_twenty_plain_lines_stay_twenty_when_the_engine_merges_two(self):
        text = "\n".join(f"line {i}" for i in range(1, 21))
        result = te.standalone_translate(text, _BlobMergingEngine(), "ja", "en")
        assert result.split("\n") == [f"OUT:line {i}" for i in range(1, 21)]

    def test_numbered_lines_are_not_collapsed_into_one(self):
        text = "\n".join(f"{i}. line {i}" for i in range(1, 6))
        result = te.standalone_translate(text, _BlobMergingEngine(collapse_numbered=True), "ja", "en")
        assert result.split("\n") == [f"OUT:{i}. line {i}" for i in range(1, 6)]

    def test_blank_lines_between_paragraphs_are_kept(self):
        result = te.standalone_translate("a\nb\n\nc\nd", _BlobMergingEngine(), "ja", "en")
        assert result == "OUT:a\nOUT:b\n\nOUT:c\nOUT:d"

    def test_bullets_and_punctuation_only_lines_keep_their_place(self):
        engine = _BlobMergingEngine()
        result = te.standalone_translate("- one\n...\n- two", engine, "ja", "en")
        assert result == "OUT:- one\n...\nOUT:- two"
        assert ["..."] not in engine.calls and all("..." not in c for c in engine.calls)

    def test_a_blob_chunk_with_extra_lines_in_the_reply_is_redone_line_by_line(self):
        class _Verbose:
            name = "claude"

            def translate_batch(self, zh_lines, context):
                return [f"OUT:{z}" + ("\nextra" if "\n\n" in z else "") for z in zh_lines]

        result = te.standalone_translate("a\n\nb", _Verbose(), "ja", "en")
        assert result == "OUT:a\n\nOUT:b"

    def test_a_line_the_engine_never_answers_is_marked_and_reported(self):
        class _Dropping:
            name = "claude"

            def translate_batch(self, zh_lines, context):
                return ["" if z == "b" else f"OUT:{z}" for z in zh_lines]

        result, message = te.standalone_translate_partial("a\nb\nc", _Dropping(), "ja", "en")
        assert result == "OUT:a\n[untranslated] b\nOUT:c"
        assert message == "1 line could not be translated and is marked [untranslated]."

    def test_a_reply_of_the_wrong_length_is_never_matched_by_position(self):
        class _Short:
            name = "claude"

            def translate_batch(self, zh_lines, context):
                return [f"OUT:{z}" for z in zh_lines][:-1]

        result, message = te.standalone_translate_partial("a\nb", _Short(), "ja", "en")
        assert result == "[untranslated] a\n[untranslated] b"
        assert "2 lines" in message

    def test_a_budget_stop_mid_run_returns_the_finished_lines(self):
        class _StopAfterOne:
            def __init__(self):
                self.seen = 0

            def refuse(self, batch, first, total):
                self.seen += 1
                return "stopped" if self.seen > 1 else None

            def abandoned(self, batch):
                pass

        text = "\n".join(f"l{i}" for i in range(1, 5))
        result, message = te.standalone_translate_partial(
            text, _BlobMergingEngine(), "ja", "en", batch_size=2, budget=_StopAfterOne())
        # The chunk the stop landed in is dropped whole, as before, not half merged.
        assert (result, message) == ("", "stopped")
