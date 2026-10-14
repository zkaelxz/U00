"""Per-medium wording for the translation prompts.

A title's content_mode picks one of three kinds. "subtitle" is every mode that
predates this module (audio drama, video drama, no mode set) and keeps the
original prompt text word for word, so existing titles translate the same.
"novel" and "stream" replace the parts that only make sense for subtitles."""

NOVEL_MODES = ("novel_narration", "novel")
STREAM_MODES = ("streamer_vod",)


def kind_for(drama_meta: dict) -> str:
    mode = (drama_meta or {}).get("content_mode") or (drama_meta or {}).get("media_type")
    if mode in NOVEL_MODES:
        return "novel"
    if mode in STREAM_MODES:
        return "stream"
    return "subtitle"


_OUTPUT = {
    "subtitle": "natural, idiomatic English subtitles",
    "novel": "English prose for readers, in the register of a published translation",
    "stream": "short, natural English captions",
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
            "differs from their other lines, and a tense or point-of-view shift."),
    },
    "stream": {
        "optimise": "how naturally it reads as a live caption",
        "critique": "reads stiff or too long as a caption",
        "rewrite": "reads as a short, casual caption",
        "extra_critique": (
            " Also flag a likely speech-recognition mistake that was translated literally."),
    },
}


def reflect_terms(kind: str) -> dict:
    return _REFLECT[kind]


_IMPROVE = {
    "subtitle": "",
    "novel": (
        "\n\nThis line is from a novel: improve it as prose. Keep the full meaning, imagery, "
        "names and glossary terms; do not shorten it, and keep the speaker's voice."),
    "stream": (
        "\n\nThis line is a live-stream caption: keep it short and casual, and fix it if it "
        "looks like a speech-recognition mistake was translated literally."),
}


def improve_rubric(kind: str) -> str:
    return _IMPROVE[kind]
