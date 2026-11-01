"""Per-medium prompt wording: subtitle titles keep their exact pre-existing text;
novel, stream and audio titles get their own."""
from engine_backends import medium_prompts
from engine_backends.prompts import build_llm_instructions
from engine_backends.reflect_prompts import (
    build_reflect_expressive_task, build_reflect_faithful_task, build_reflect_reflection_task)

# Captured before medium_prompts existed: a changed word here changes every
# existing audio drama and video translation.
SUBTITLE_INSTRUCTIONS = 'You are translating Chinese video drama into natural, idiomatic English subtitles. You will be given numbered lines to translate in each request, each optionally prefixed with the name of the character speaking it in [brackets] -- use that to get pronouns, honorifics, and register right, but never include the bracketed name itself in your translation.\n\nWrite in American English -- match its spelling conventions (e.g. color/realize) and phrasing register consistently throughout.\nRules:\n- Keep each translation concise enough to read comfortably as a subtitle.\n- Keep character names, honorifics, and recurring terms consistent.\n- Match tone/register to the reference novel translation below, if provided -- that is the authoritative source for names, relationships, and voice for THIS drama only. Do not draw on any other novel or outside knowledge of similarly-named characters.\n- If a line has no clear match in the reference, translate it naturally while staying consistent with the established voice.\n- Return ONLY a JSON object mapping each line\'s number (as a string) to its translation, e.g. {"1": "...", "2": "..."} -- include EVERY number you were given, and no numbers you weren\'t. No preamble, no markdown fences, no commentary.'
SUBTITLE_FAITHFUL = 'This is the FAITHFULNESS pass of a multi-stage translation: translate each line below preserving its exact literal meaning, grammar and information -- word choice and register still matter, but don\'t optimize yet for how naturally it reads as a subtitle; a later pass polishes that. Keep names and glossary terms consistent as instructed above.\n\nReturn ONLY a JSON object mapping each line number to its translation, e.g. {"1": "...", "2": "..."}. No preamble, no markdown fences.\n\nLines:\n1. x'
SUBTITLE_REFLECTION = 'This is the REFLECTION pass: for each numbered line below (source -> a literal draft translation), critique that SPECIFIC draft -- where it\'s technically correct but reads unnaturally as a subtitle, plus accuracy, pronoun/gender choices, glossary use, tone and register. One or two plain sentences per line. Skip a line\'s key entirely if the draft is already good -- don\'t invent a critique to fill space.\n\nReturn ONLY a JSON object mapping each line number to its critique, e.g. {"1": "..."}. Omit a key entirely for a line that needs no critique. No preamble, no markdown fences.\n\nLines:\n1. x -> y'
SUBTITLE_EXPRESSIVE = 'This is the EXPRESSIVENESS pass: rewrite each line\'s draft translation using its critique (where one is given) so it reads naturally as a subtitle -- fluent, well-paced, in-register -- while keeping the source\'s exact meaning. A line with no critique is probably already close; still return your best final wording for every line.\n\nReturn ONLY a JSON object mapping each line number to its final translation, e.g. {"1": "..."}. No preamble, no markdown fences.\n\nLines:\n1. Source: x\n   Draft: y\n   Critique: c'

# Captured from the audio prompt set when it was added: a changed word here changes
# every audio drama and ASMR translation.
AUDIO_INSTRUCTIONS = 'You are translating Chinese audio drama into natural, speakable English lines that read as the same performance. You will be given numbered lines to translate in each request, each optionally prefixed with the name of the character speaking it in [brackets] -- use that to get pronouns, honorifics, and register right, but never include the bracketed name itself in your translation.\n\nWrite in American English -- match its spelling conventions (e.g. color/realize) and phrasing register consistently throughout.\nRules:\n- The listener hears these lines, so write what an actor can say in one breath: contractions and spoken rhythm, no constructions that only work on a page.\n- Keep the delivery cues the source carries: pauses, trailing-off, whispers, sighs and sound effects stay as short bracketed or inline cues, never explained.\n- Keep each speaker\'s register line to line; a gentle voice stays gentle.\n- Keep character names, honorifics, and recurring terms consistent.\n- Match tone/register to the reference novel translation below, if provided -- that is the authoritative source for names, relationships, and voice for THIS drama only. Do not draw on any other novel or outside knowledge of similarly-named characters.\n- If a line has no clear match in the reference, translate it naturally while staying consistent with the established voice.\n- Return ONLY a JSON object mapping each line\'s number (as a string) to its translation, e.g. {"1": "...", "2": "..."} -- include EVERY number you were given, and no numbers you weren\'t. No preamble, no markdown fences, no commentary.'
AUDIO_FAITHFUL = 'This is the FAITHFULNESS pass of a multi-stage translation: translate each line below preserving its exact literal meaning, grammar and information -- word choice and register still matter, but don\'t optimize yet for how naturally it sounds spoken; a later pass polishes that. Keep names and glossary terms consistent as instructed above.\n\nReturn ONLY a JSON object mapping each line number to its translation, e.g. {"1": "...", "2": "..."}. No preamble, no markdown fences.\n\nLines:\n1. x'
AUDIO_REFLECTION = 'This is the REFLECTION pass: for each numbered line below (source -> a literal draft translation), critique that SPECIFIC draft -- where it\'s technically correct but would make a voice actor stumble or sounds written, not spoken, plus accuracy, pronoun/gender choices, glossary use, tone and register. Also flag a lost pause, whisper or other delivery cue, and a speaker whose register differs from their other lines. One or two plain sentences per line. Skip a line\'s key entirely if the draft is already good -- don\'t invent a critique to fill space.\n\nReturn ONLY a JSON object mapping each line number to its critique, e.g. {"1": "..."}. Omit a key entirely for a line that needs no critique. No preamble, no markdown fences.\n\nLines:\n1. x -> y'
AUDIO_EXPRESSIVE = 'This is the EXPRESSIVENESS pass: rewrite each line\'s draft translation using its critique (where one is given) so it sounds natural spoken aloud -- fluent, well-paced, in-register -- while keeping the source\'s exact meaning. A line with no critique is probably already close; still return your best final wording for every line.\n\nReturn ONLY a JSON object mapping each line number to its final translation, e.g. {"1": "..."}. No preamble, no markdown fences.\n\nLines:\n1. Source: x\n   Draft: y\n   Critique: c'
AUDIO_IMPROVE_RUBRIC = "\n\nThis line is spoken audio: rephrase it so an actor can say it naturally in one breath, keeping the meaning, glossary terms, delivery cues and the speaker's register."
ASMR_INSTRUCTIONS = 'You are translating Chinese ASMR audio into natural, speakable English lines that read as the same performance. You will be given numbered lines to translate in each request, each optionally prefixed with the name of the character speaking it in [brackets] -- use that to get pronouns, honorifics, and register right, but never include the bracketed name itself in your translation.\n\nWrite in American English -- match its spelling conventions (e.g. color/realize) and phrasing register consistently throughout.\nRules:\n- The listener hears these lines, so write what an actor can say in one breath: contractions and spoken rhythm, no constructions that only work on a page.\n- Keep the delivery cues the source carries: pauses, trailing-off, whispers, sighs and sound effects stay as short bracketed or inline cues, never explained.\n- Keep each speaker\'s register line to line; a gentle voice stays gentle.\n- Keep character names, honorifics, and recurring terms consistent.\n- Match tone/register to the reference novel translation below, if provided -- that is the authoritative source for names, relationships, and voice for THIS drama only. Do not draw on any other novel or outside knowledge of similarly-named characters.\n- If a line has no clear match in the reference, translate it naturally while staying consistent with the established voice.\n- Return ONLY a JSON object mapping each line\'s number (as a string) to its translation, e.g. {"1": "...", "2": "..."} -- include EVERY number you were given, and no numbers you weren\'t. No preamble, no markdown fences, no commentary.'


def test_subtitle_prompts_are_unchanged():
    meta = {"content_mode": "video_drama", "genre": "drama"}
    assert build_llm_instructions("", meta) == SUBTITLE_INSTRUCTIONS
    assert build_reflect_faithful_task("", [1], {1: "x"}) == SUBTITLE_FAITHFUL
    assert build_reflect_reflection_task("", [1], {1: "x"}, {1: "y"}) == SUBTITLE_REFLECTION
    assert build_reflect_expressive_task(
        "", [1], {1: "x"}, {1: "y"}, {1: "c"}) == SUBTITLE_EXPRESSIVE


def test_audio_prompts_are_unchanged():
    meta = {"content_mode": "audio_drama", "genre": "drama"}
    assert build_llm_instructions("", meta) == AUDIO_INSTRUCTIONS
    assert build_llm_instructions("", {**meta, "content_mode": "asmr"}) == ASMR_INSTRUCTIONS
    assert build_reflect_faithful_task("", [1], {1: "x"}, kind="audio") == AUDIO_FAITHFUL
    assert build_reflect_reflection_task("", [1], {1: "x"}, {1: "y"}, kind="audio") == AUDIO_REFLECTION
    assert build_reflect_expressive_task(
        "", [1], {1: "x"}, {1: "y"}, {1: "c"}, kind="audio") == AUDIO_EXPRESSIVE
    assert medium_prompts.improve_rubric("audio") == AUDIO_IMPROVE_RUBRIC


def test_kind_follows_content_mode():
    assert medium_prompts.kind_for({"content_mode": "novel_narration"}) == "novel"
    assert medium_prompts.kind_for({"content_mode": "streamer_vod"}) == "stream"
    for mode in ("audio_drama", "asmr"):
        assert medium_prompts.kind_for({"content_mode": mode}) == "audio"
    assert medium_prompts.kind_for({"media_type": "asmr"}) == "audio"
    for meta in ({}, {"content_mode": "video_drama"}):
        assert medium_prompts.kind_for(meta) == "subtitle"


def test_novel_instructions_are_prose_not_subtitles():
    text = build_llm_instructions("", {"content_mode": "novel_narration", "genre": "drama"})
    assert "English subtitles" not in text
    assert "read comfortably as a subtitle" not in text
    assert "no length limit" in text
    assert "consistent voice" in text


def test_novel_instructions_ask_for_native_prose():
    text = build_llm_instructions("", {"content_mode": "novel_narration", "genre": "drama"})
    assert "drops subjects and pronouns" in text
    assert "translationese" in text
    assert "double quotation marks" in text
    assert "Add no image, detail or flourish" in text
    assert "sound spoken" in text and "Dialogue tags" in text
    assert "always keep their quotation marks" in text
    assert "Never cut a line short" in text
    assert "never copy it into the translation" in text
    assert "single quoted word" in text and "doubled English filler" in text
    assert "syllable by syllable" in text and "plain noun" in text
    reflection = build_reflect_reflection_task("", [1], {1: "x"}, {1: "y"}, kind="novel")
    assert "translationese" in reflection
    assert "native novelist" in medium_prompts.improve_rubric("novel")


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


def test_audio_instructions_are_spoken_not_subtitles():
    text = build_llm_instructions("", {"content_mode": "audio_drama", "genre": "drama"})
    assert "English subtitles" not in text
    assert "read comfortably as a subtitle" not in text
    assert "one breath" in text and "whispers" in text


def test_audio_reflect_and_improve_ask_for_speech():
    reflection = build_reflect_reflection_task("", [1], {1: "x"}, {1: "y"}, kind="audio")
    assert "voice actor" in reflection and "delivery cue" in reflection
    assert "subtitle" not in reflection
    assert "subtitle" not in build_reflect_faithful_task("", [1], {1: "x"}, kind="audio")
    assert "subtitle" not in build_reflect_expressive_task(
        "", [1], {1: "x"}, {1: "y"}, {1: "c"}, kind="audio")
    assert "one breath" in medium_prompts.improve_rubric("audio")


def test_every_kind_keeps_the_id_keyed_reply_contract():
    for mode in ("video_drama", "audio_drama", "novel_narration", "streamer_vod"):
        kind = medium_prompts.kind_for({"content_mode": mode})
        text = build_llm_instructions("", {"content_mode": mode, "genre": "drama"})
        assert "include EVERY number you were given" in text and "JSON object" in text
        for task in (build_reflect_faithful_task("", [7], {7: "x"}, kind=kind),
                     build_reflect_reflection_task("", [7], {7: "x"}, {7: "y"}, kind=kind),
                     build_reflect_expressive_task("", [7], {7: "x"}, {7: "y"}, {7: "c"}, kind=kind)):
            assert "JSON object mapping each line number" in task and "7. " in task
