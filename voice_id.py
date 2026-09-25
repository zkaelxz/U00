"""
voice_id.py -- Step 8's recurring-voice suggestions: "SPEAKER_01 sounds
like <name> (similarity 0.82)", ranked by cosine similarity between a
drama's own diarization voice embeddings (diarize.extract_speaker_embeddings)
and each series character's running-average fingerprint
(db.update_series_character_voice_fingerprint).

Experimental, per Phase 1 -- nobody has shown this works reliably across
different recordings. Nothing here labels a speaker automatically: this
only ever produces suggestions for a person to confirm or dismiss.
"""
import json
import math

DEFAULT_THRESHOLD = 0.75


def cosine_similarity(a: list, b: list) -> float:
    """1.0 = identical direction, 0.0 = orthogonal/unrelated, -1.0 =
    opposite. 0.0 for a zero vector or a dimension mismatch (an undefined
    comparison, not a real 0% match) rather than raising -- a speaker
    with a garbled or empty embedding just never matches anything."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def suggest_speaker_matches(embeddings_by_speaker: dict, series_characters: list,
                            already_named: set = None, dismissed: set = None,
                            threshold: float = DEFAULT_THRESHOLD) -> list:
    """One suggestion per speaker that isn't already named and has a
    candidate above threshold: its single BEST-matching series character,
    by cosine similarity against that character's voice_fingerprint.

    embeddings_by_speaker: {speaker_label: [float, ...]} for this drama
    (diarize.load_embeddings/extract_speaker_embeddings).
    series_characters: rows from db.list_series_characters (only ones
    with a voice_fingerprint set actually compare against anything).
    already_named: speaker labels that already have a character assigned
    in this drama -- they don't need a suggestion.
    dismissed: {(speaker_label, series_character_id), ...} pairs already
    rejected for this drama (db.list_dismissed_voice_suggestions) -- that
    specific candidate is skipped for that speaker, but a different
    candidate for the same speaker can still surface.

    Returns [{"speaker_label", "series_character_id", "character_name",
    "similarity"}, ...], sorted by similarity descending -- highest-
    confidence suggestions first, for review order, not that order
    implying anything should be auto-applied.
    """
    already_named = already_named or set()
    dismissed = dismissed or set()
    candidates = []
    for sc in series_characters:
        fp = sc.get("voice_fingerprint")
        if not fp:
            continue
        try:
            # db stores this as a JSON-encoded string; a caller that
            # already parsed it (e.g. building a fixture in a test) can
            # just pass a list through unchanged.
            fingerprint = json.loads(fp) if isinstance(fp, str) else fp
        except (TypeError, ValueError):
            continue
        candidates.append((sc, fingerprint))

    suggestions = []
    for speaker, embedding in embeddings_by_speaker.items():
        if speaker in already_named:
            continue
        best = None
        for sc, fingerprint in candidates:
            if (speaker, sc["id"]) in dismissed:
                continue
            similarity = cosine_similarity(embedding, fingerprint)
            if similarity >= threshold and (best is None or similarity > best["similarity"]):
                best = {"speaker_label": speaker, "series_character_id": sc["id"],
                       "character_name": sc["character_name"], "similarity": similarity}
        if best is not None:
            suggestions.append(best)

    suggestions.sort(key=lambda s: -s["similarity"])
    return suggestions
