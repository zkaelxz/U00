"""Flag keys a line can carry, and the text shown for each."""


# Kept intentionally to what's actually assessable from the text alone --
# "speaker uncertain" and "overlapping speech"/"audio unclear" would need
# real diarization-confidence or audio evidence this codebase doesn't
# expose yet (see diarize.py), not something worth guessing at from text.
FLAG_REASONS = {
    "uncertain_translation": "Possible mistranslation or awkward phrasing worth a second look",
    "ambiguous_reference": "A pronoun or reference ('she', 'that place') that isn't clearly resolved",
    "name_uncertain": "A name or term that might be misspelled or inconsistently romanized",
    "slang_idiom": "Slang or an idiom that may not have translated well",
}

# Flags the app sets itself (not offered to the LLM as a reason to pick).
SYSTEM_FLAG_REASONS = {
    "timing_uncertain": "Timing uncertain -- forced alignment fell back to approximate timing",
    "language_uncertain": ("Language uncertain -- the text doesn't match the language detected "
                           "for this line"),
    "timing_overlap": "Overlaps the next line -- exports trim it",
    "timing_drift": "Timing disagrees with the audio -- the line may start or end away from the speech",
    "reading_speed": "Too fast to read -- too many characters for the time it's shown",
    "factual_detail": ("Auto QC: a number, date, name, amount or unit differs between the "
                       "source and the translation"),
    "gap_untranscribed": ("Added for a stretch with no subtitle line -- check the text "
                          "heard there"),
    "bulk_source_changed": ("Source text changed while a bulk translation was pending -- its "
                            "result wasn't applied; translate this line again"),
    "source_changed": ("Source changed -- the source text was changed after this line was "
                       "translated; retranslate it or keep the English"),
    "pronoun_check": ("Pronoun check -- the translation says he/him but no he/him character "
                      "is set for this speaker"),
    "content_blocked": ("Blocked by the translation engine's own content-moderation system -- "
                        "see the note for which engine and its stated reason"),
}


def flag_reason_label(flag: str) -> str:
    return FLAG_REASONS.get(flag) or SYSTEM_FLAG_REASONS.get(flag) or flag
