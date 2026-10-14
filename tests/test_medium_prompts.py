"""Per-medium prompt wording: subtitle titles keep their exact pre-existing text;
novel and stream titles get their own."""
from engine_backends import medium_prompts
from engine_backends.prompts import build_llm_instructions
from engine_backends.reflect_prompts import (
    build_reflect_expressive_task, build_reflect_faithful_task, build_reflect_reflection_task)

# Captured before medium_prompts existed: a changed word here changes every
# existing audio drama and video translation.
SUBTITLE_INSTRUCTIONS = 'You are translating Chinese audio drama into natural, idiomatic English subtitles. You will be given numbered lines to translate in each request, each optionally prefixed with the name of the character speaking it in [brackets] -- use that to get pronouns, honorifics, and register right, but never include the bracketed name itself in your translation.\n\nWrite in American English -- match its spelling conventions (e.g. color/realize) and phrasing register consistently throughout.\nRules:\n- Keep each translation concise enough to read comfortably as a subtitle.\n- Keep character names, honorifics, and recurring terms consistent.\n- Match tone/register to the reference novel translation below, if provided -- that is the authoritative source for names, relationships, and voice for THIS drama only. Do not draw on any other novel or outside knowledge of similarly-named characters.\n- If a line has no clear match in the reference, translate it naturally while staying consistent with the established voice.\n- Return ONLY a JSON object mapping each line\'s number (as a string) to its translation, e.g. {"1": "...", "2": "..."} -- include EVERY number you were given, and no numbers you weren\'t. No preamble, no markdown fences, no commentary.'
SUBTITLE_FAITHFUL = 'This is the FAITHFULNESS pass of a multi-stage translation: translate each line below preserving its exact literal meaning, grammar and information -- word choice and register still matter, but don\'t optimize yet for how naturally it reads as a subtitle; a later pass polishes that. Keep names and glossary terms consistent as instructed above.\n\nReturn ONLY a JSON object mapping each line number to its translation, e.g. {"1": "...", "2": "..."}. No preamble, no markdown fences.\n\nLines:\n1. x'
SUBTITLE_REFLECTION = 'This is the REFLECTION pass: for each numbered line below (source -> a literal draft translation), critique that SPECIFIC draft -- where it\'s technically correct but reads unnaturally as a subtitle, plus accuracy, pronoun/gender choices, glossary use, tone and register. One or two plain sentences per line. Skip a line\'s key entirely if the draft is already good -- don\'t invent a critique to fill space.\n\nReturn ONLY a JSON object mapping each line number to its critique, e.g. {"1": "..."}. Omit a key entirely for a line that needs no critique. No preamble, no markdown fences.\n\nLines:\n1. x -> y'
SUBTITLE_EXPRESSIVE = 'This is the EXPRESSIVENESS pass: rewrite each line\'s draft translation using its critique (where one is given) so it reads naturally as a subtitle -- fluent, well-paced, in-register -- while keeping the source\'s exact meaning. A line with no critique is probably already close; still return your best final wording for every line.\n\nReturn ONLY a JSON object mapping each line number to its final translation, e.g. {"1": "..."}. No preamble, no markdown fences.\n\nLines:\n1. Source: x\n   Draft: y\n   Critique: c'


def test_subtitle_prompts_are_unchanged():
    meta = {"content_mode": "audio_drama", "genre": "drama"}
    assert build_llm_instructions("", meta) == SUBTITLE_INSTRUCTIONS
    assert build_reflect_faithful_task("", [1], {1: "x"}) == SUBTITLE_FAITHFUL
    assert build_reflect_reflection_task("", [1], {1: "x"}, {1: "y"}) == SUBTITLE_REFLECTION
    assert build_reflect_expressive_task(
        "", [1], {1: "x"}, {1: "y"}, {1: "c"}) == SUBTITLE_EXPRESSIVE


def test_kind_follows_content_mode():
    assert medium_prompts.kind_for({"content_mode": "novel_narration"}) == "novel"
    assert medium_prompts.kind_for({"content_mode": "streamer_vod"}) == "stream"
    for meta in ({}, {"content_mode": "audio_drama"}, {"content_mode": "video_drama"}):
        assert medium_prompts.kind_for(meta) == "subtitle"


def test_novel_instructions_are_prose_not_subtitles():
    text = build_llm_instructions("", {"content_mode": "novel_narration", "genre": "drama"})
    assert "English subtitles" not in text
    assert "read comfortably as a subtitle" not in text
    assert "no length limit" in text
    assert "consistent voice" in text


def test_stream_instructions_cover_asr_slang_and_fillers():
    text = build_llm_instructions("", {"content_mode": "streamer_vod", "genre": "livestream"})
    assert "speech recognition" in text
    assert "slang" in text and "fillers" in text
    assert "mid-sentence" in text


def test_novel_reflect_passes_check_voice_and_prose():
    reflection = build_reflect_reflection_task("", [1], {1: "x"}, {1: "y"}, kind="novel")
    assert "voice" in reflection and "point-of-view" in reflection
    assert "subtitle" not in reflection
    assert "subtitle" not in build_reflect_faithful_task("", [1], {1: "x"}, kind="novel")
    assert "subtitle" not in build_reflect_expressive_task(
        "", [1], {1: "x"}, {1: "y"}, {1: "c"}, kind="novel")


def test_improve_rubric_by_kind():
    assert medium_prompts.improve_rubric("subtitle") == ""
    assert "do not shorten" in medium_prompts.improve_rubric("novel")
    assert "speech-recognition" in medium_prompts.improve_rubric("stream")
