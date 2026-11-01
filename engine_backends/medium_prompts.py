"""Per-medium wording for the translation prompts.

A title's content_mode picks one of four kinds. "subtitle" is every mode that
predates this module (video drama, no mode set) and keeps the original prompt
text word for word, so existing titles translate the same. "novel", "stream"
and "audio" (spoken drama, ASMR) replace the parts that only make sense for
on-screen subtitles."""

NOVEL_MODES = ("novel_narration", "novel")
STREAM_MODES = ("streamer_vod",)
AUDIO_MODES = ("audio_drama", "asmr")


def kind_for(drama_meta: dict) -> str:
    mode = (drama_meta or {}).get("content_mode") or (drama_meta or {}).get("media_type")
    if mode in NOVEL_MODES:
        return "novel"
    if mode in STREAM_MODES:
        return "stream"
    if mode in AUDIO_MODES:
        return "audio"
    return "subtitle"


_OUTPUT = {
    "subtitle": "natural, idiomatic English subtitles",
    "novel": "English prose for readers, in the register of a published translation",
    "stream": "short, natural English captions",
    "audio": "natural, speakable English lines that read as the same performance",
}

_LENGTH_RULE = {
    "subtitle": "- Keep each translation concise enough to read comfortably as a subtitle.\n",
    "novel": (
        "- This is literature, not subtitles: there is no length limit, so never shorten or "
        "summarise to fit. Keep the narrator's tense and point of view fixed unless the source "
        "changes them.\n"
        "- Keep imagery and metaphor. If the source reaches for a poetic image, so do you.\n"
        "- Give each speaker one consistent voice from line to line (diction, formality, "
        "verbal habits); a blunt character stays blunt.\n"
        "- Keep deliberate ambiguity and subtext; never explain what the source leaves unsaid.\n"
        "- The lines are consecutive pieces of one chapter, and a sentence may continue into "
        "the next line. Write it so the chapter reads as one continuous text.\n"
        "- Write English a native novelist would, not a mirror of the source's syntax. "
        "CJK drops subjects and pronouns and chains clauses with commas: supply the subject "
        "from context, vary name and pronoun instead of repeating a name, join choppy clauses "
        "and split run-ons so the sentences have English rhythm.\n"
        "- Idioms, four-character set phrases and web-novel stock phrases carry their sense, "
        "not their words: give the natural English with the same force, and avoid stock "
        "translationese (\"a hint of\", \"couldn't help but\", \"the corner of her mouth "
        "curled\") when the source is plainer.\n"
        "- Add no image, detail or flourish the source does not have, and prefer plain wording "
        "to ornate: fluent means natural, not embellished. This covers nouns too: translate "
        "a plain noun (a bird, a sword) with the plain English word, never a more specific or "
        "poetic one, and do not turn one verb into a phrase.\n"
        "- A name not in the glossary is the pinyin (or the language's standard romanisation) "
        "of its exact characters, syllable by syllable; never swap in a different-looking "
        "name or one you guess from the sound.\n"
        "- Dialogue must sound spoken: contractions, short plain wording, and a character's "
        "register only as formal as the source makes them. Render polite formulas by their "
        "plain meaning (\"My surname is Lan\" becomes \"I'm Lan\") instead of archaic stock "
        "phrases (\"esteemed\", \"Milady\", \"Might I ask\"), unless the glossary or a "
        "character's voice calls for them. Turn particles such as 嗯 into \"Mm\", not a "
        "romanisation.\n"
        "- Dialogue tags: plain \"said\" and \"asked\" by default, placed after the quote or "
        "dropped when the speaker is clear; do not stack manner adverbs or fancy verbs "
        "(\"choked out resentfully\"), and do not repeat one tag pattern line after line.\n"
        "- Phrase plainly: the simplest natural verb and adjective that fits, one idea per "
        "clause, and sentence length close to the source's.\n"
        "- Put dialogue in standard English form: double quotation marks, with the dialogue "
        "tag and punctuation placed as an English novel would, and no CJK quotation marks. "
        "Spoken words always keep their quotation marks, and a line that has a speaker tag in "
        "the source keeps it in the translation.\n"
    ),
    "stream": (
        "- Keep each caption short enough to read in a glance while the stream continues.\n"
        "- The text comes from speech recognition and may contain mistakes. If a phrase looks "
        "like a misheard word, use the sense that fits the recent lines and the glossary; if "
        "you can't tell, translate it literally.\n"
        "- Keep the streamer's casual voice: slang, jokes and catchphrases stay casual and "
        "short. Drop fillers (嗯, 那个, えーと, 음) unless they carry tone.\n"
        "- A line may start or end mid-sentence. Translate what is there and use the earlier "
        "lines only to resolve pronouns and topic; never repeat them. Keep usernames "
        "untranslated.\n"
    ),
    "audio": (
        "- The listener hears these lines, so write what an actor can say in one breath: "
        "contractions and spoken rhythm, no constructions that only work on a page.\n"
        "- Keep the delivery cues the source carries: pauses, trailing-off, whispers, "
        "sighs and sound effects stay as short bracketed or inline cues, never explained.\n"
        "- Keep each speaker's register line to line; a gentle voice stays gentle.\n"
    ),
}


def output_phrase(kind: str) -> str:
    return _OUTPUT[kind]


def length_rule(kind: str) -> str:
    return _LENGTH_RULE[kind]


# Wording that Reflect and Improve share with the three passes: how the finished
# line is judged. "subtitle" values are the original strings.
_REFLECT = {
    "subtitle": {
        "optimise": "how naturally it reads as a subtitle",
        "critique": "reads unnaturally as a subtitle",
        "rewrite": "reads naturally as a subtitle",
        "extra_critique": "",
    },
    "novel": {
        "optimise": "prose rhythm",
        "critique": "reads flat as prose",
        "rewrite": "reads as polished prose",
        "extra_critique": (
            " Also flag lost imagery, shortened or summarised content, a speaker whose voice "
            "differs from their other lines, a tense or point-of-view shift, and translationese: "
            "a literal idiom, a repeated name where a pronoun reads better, or source syntax "
            "carried into English."),
    },
    "stream": {
        "optimise": "how naturally it reads as a live caption",
        "critique": "reads stiff or too long as a caption",
        "rewrite": "reads as a short, casual caption",
        "extra_critique": (
            " Also flag a likely speech-recognition mistake that was translated literally."),
    },
    "audio": {
        "optimise": "how naturally it sounds spoken",
        "critique": "would make a voice actor stumble or sounds written, not spoken",
        "rewrite": "sounds natural spoken aloud",
        "extra_critique": (
            " Also flag a lost pause, whisper or other delivery cue, and a speaker whose "
            "register differs from their other lines."),
    },
}


def reflect_terms(kind: str) -> dict:
    return _REFLECT[kind]


_IMPROVE = {
    "subtitle": "",
    "novel": (
        "\n\nThis line is from a novel: improve it as prose. Keep the full meaning, imagery, "
        "names and glossary terms; do not shorten it, keep the speaker's voice, and rewrite "
        "any literal idiom or source-shaped sentence the way a native novelist would."),
    "stream": (
        "\n\nThis line is a live-stream caption: keep it short and casual, and fix it if it "
        "looks like a speech-recognition mistake was translated literally."),
    "audio": (
        "\n\nThis line is spoken audio: rephrase it so an actor can say it naturally in one "
        "breath, keeping the meaning, glossary terms, delivery cues and the speaker's register."),
}


def improve_rubric(kind: str) -> str:
    return _IMPROVE[kind]
