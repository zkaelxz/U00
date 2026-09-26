"""
emotion.py -- emotional register detection and preservation.

The failure mode this exists to prevent: a translation that is
semantically correct and tonally dead. Sarcasm rendered as sincerity,
a barbed joke flattened into a plain statement, suppressed anger read
as calm. In an audio drama this is especially costly, because the
voice actor's delivery in the original already carries the emotion --
if the subtitle contradicts the performance, the scene breaks.

Two things happen here:
  1. Lines are tagged with their emotional register (optionally using
     the original audio's delivery as a signal, not just the text).
  2. Those tags are fed back into translation so the English carries
     the same charge.
"""

import re
import json
from translate_engines import call_llm_json

# Registers worth distinguishing because they change word choice in English.
EMOTION_TAGS = {
    "neutral": "Plain statement, no particular charge",
    "warm": "Affectionate, tender, fond",
    "flirtatious": "Teasing with romantic intent",
    "playful": "Joking, light, mischievous",
    "sarcastic": "Says the opposite of what's meant, or mock-sincere",
    "dry_humor": "Understated wit, deadpan",
    "angry": "Openly angry or aggressive",
    "suppressed_anger": "Angry but controlled -- cold, clipped, polite-on-the-surface",
    "sad": "Grieving, wistful, resigned",
    "anxious": "Worried, uncertain, afraid",
    "formal": "Deliberately formal or distant, often marking hierarchy",
    "intimate": "Softened, private register used only with someone close",
    "commanding": "Giving orders, asserting authority",
    "evasive": "Deliberately deflecting or refusing to answer directly",
}

# How each register should shape the English, beyond just "sound emotional".
EMOTION_TRANSLATION_GUIDANCE = {
    "sarcastic": ("Preserve the gap between what's said and what's meant. Don't "
                  "explain the sarcasm or soften it into sincerity."),
    "dry_humor": "Keep it understated. Over-punctuating or adding emphasis kills deadpan.",
    "suppressed_anger": ("Keep the surface controlled -- clipped sentences, formal word "
                         "choice -- so the anger reads underneath rather than on top."),
    "flirtatious": ("Preserve the teasing edge and any deliberate ambiguity. Don't resolve "
                    "innuendo into a plain statement."),
    "evasive": "Stay indirect. Don't supply the answer the speaker is dodging.",
    "formal": "Preserve the distance. Formality here is usually about hierarchy, not politeness.",
    "intimate": "Softer register, more contractions -- mark the shift from how they speak to others.",
}


def build_emotion_prompt(batch: list, use_audio_cues: bool = False,
                         id_fn=lambda ln: ln.idx) -> str:
    """The emotion-tagging prompt for one batch, each line numbered by
    id_fn(ln) (position by default, matching detect_emotions' own
    results dict). bulk_translate.py's bulk submission (Step 9d) passes
    id_fn=lambda ln: ln.id -- see build_flag_prompt's docstring for why a
    permanent id matters once results can come back hours later."""
    tags_desc = "\n".join(f"  - {k}: {v}" for k, v in EMOTION_TAGS.items())
    rows = []
    for i, ln in enumerate(batch):
        row = f"[{id_fn(ln)}] ({ln.speaker or '?'}) {ln.zh}"
        if ln.en:
            row += f" → {ln.en}"
        if use_audio_cues:
            dur = ln.end - ln.start
            chars = max(len(ln.zh), 1)
            pace = dur / chars
            prev = batch[i - 1] if i > 0 else None
            gap = (ln.start - prev.end) if prev else 0
            cues = []
            if pace > 0.35:
                cues.append("delivered slowly")
            elif pace < 0.12:
                cues.append("delivered quickly")
            if gap > 1.5:
                cues.append(f"{gap:.1f}s pause before")
            if cues:
                row += f"  [delivery: {', '.join(cues)}]"
        rows.append(row)

    return (
        "Tag the emotional register of each line of dialogue below.\n\n"
        f"Available tags:\n{tags_desc}\n\n"
        "Pick the single tag that best fits. Pay particular attention to sarcasm, "
        "suppressed anger, and flirtation -- these are the registers most often lost "
        "in translation, because the literal words say something different from what's "
        "meant. Give an intensity from 0.0 to 1.0.\n\n"
        + ("Delivery hints in brackets come from the original audio's timing -- treat "
           "them as weak supporting evidence, not proof.\n\n" if use_audio_cues else "")
        + 'Return ONLY a JSON array: [{"line_idx": 0, "emotion": "...", "intensity": 0.0, '
        '"note": "brief reason, only if non-obvious"}]. No preamble, no markdown fences.\n\n'
        + "\n".join(rows)
    )


def parse_emotion_tags(text: str) -> dict:
    """Parses one batch's response from build_emotion_prompt into
    {id: {"emotion", "intensity", "note"}}, keyed by whatever id the
    prompt embedded (a line's position or its permanent id -- this
    function doesn't care which, it just echoes back what the model
    returned). Shared by detect_emotions (live) and bulk_translate.py's
    bulk submission (Step 9d)."""
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        tagged = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(tagged, list):
        return {}
    results = {}
    for t in tagged:
        if not isinstance(t, dict) or t.get("line_idx") is None:
            continue
        emo = t.get("emotion") if t.get("emotion") in EMOTION_TAGS else "neutral"
        try:
            intensity = float(t.get("intensity", 0.5))
        except (TypeError, ValueError):
            intensity = 0.5
        try:
            key = int(t["line_idx"])
        except (TypeError, ValueError):
            continue
        results[key] = {
            "emotion": emo,
            "intensity": max(0.0, min(1.0, intensity)),
            "note": t.get("note", ""),
        }
    return results


def detect_emotions(lines, engine, batch_size: int = 40, use_audio_cues: bool = False,
                     progress_cb=None, usage_cb=None):
    """
    Tags each line with an emotional register and an intensity (0-1).

    use_audio_cues: when the lines came from real audio, timing already
    encodes delivery -- an unusually long gap before a line, or a line
    stretched well beyond its word count, often signals hesitation or
    emphasis. Those hints are passed to the model as weak evidence.

    progress_cb: optional callback invoked with a 0.0-1.0 fraction after
    each batch -- a full stream's worth of lines is enough LLM batches
    for a static spinner to look stuck, the same reasoning as
    transcribe_for_timing's progress_cb.

    Returns {line_idx: {"emotion": str, "intensity": float, "note": str}}.
    """
    if not getattr(engine, "supports_reference", False):
        return {}
    scoped = [ln for ln in lines if ln.zh.strip()]
    if not scoped:
        return {}

    results = {}
    n_batches = (len(scoped) + batch_size - 1) // batch_size

    for bi, start in enumerate(range(0, len(scoped), batch_size)):
        batch = scoped[start:start + batch_size]
        prompt = build_emotion_prompt(batch, use_audio_cues=use_audio_cues)

        text = call_llm_json(engine, prompt, max_tokens=3000, fallback="[]", usage_cb=usage_cb)
        if progress_cb:
            # Reported right after the call, before parsing -- a batch
            # whose response fails to parse below still counts as
            # attempted, so progress keeps moving instead of stalling on
            # one bad batch out of many.
            progress_cb((bi + 1) / n_batches)
        results.update(parse_emotion_tags(text))
    return results


def build_emotion_guidance(emotion_map: dict, line_indices) -> str:
    """
    Builds the prompt fragment for a specific batch of lines being
    translated. Only includes lines that are actually charged --
    telling the model that a neutral line is neutral wastes tokens and
    dilutes the instruction.
    """
    if not emotion_map:
        return ""
    charged = []
    for idx in line_indices:
        info = emotion_map.get(idx)
        if not info or info["emotion"] == "neutral" or info["intensity"] < 0.3:
            continue
        guidance = EMOTION_TRANSLATION_GUIDANCE.get(info["emotion"], "")
        entry = f"  Line {idx + 1}: {info['emotion']} (intensity {info['intensity']:.1f})"
        if guidance:
            entry += f" — {guidance}"
        if info.get("note"):
            entry += f" [{info['note']}]"
        charged.append(entry)

    if not charged:
        return ""
    return (
        "EMOTIONAL REGISTER — these lines carry a specific charge that must survive "
        "translation. A tonally flat rendering is a failed one, even if the meaning is "
        "correct:\n" + "\n".join(charged) + "\n"
    )


def emotion_summary(emotion_map: dict) -> dict:
    """Local counts, no API call -- gives a quick read on a drama's
    emotional texture and flags how much of it is high-risk register."""
    if not emotion_map:
        return {"total": 0, "by_emotion": {}, "high_risk": 0}
    by_emotion = {}
    for info in emotion_map.values():
        by_emotion[info["emotion"]] = by_emotion.get(info["emotion"], 0) + 1
    # Registers most likely to be flattened by a careless translation.
    high_risk = sum(by_emotion.get(k, 0) for k in
                    ("sarcastic", "dry_humor", "suppressed_anger", "flirtatious", "evasive"))
    return {"total": len(emotion_map), "by_emotion": by_emotion, "high_risk": high_risk}


# Chatterbox's `exaggeration` dial: its README recommends 0.4-0.7, with 0.5
# (its own default) as the neutral read.
CHATTERBOX_EXAGGERATION_MIN = 0.4
CHATTERBOX_EXAGGERATION_MAX = 0.7
CHATTERBOX_NEUTRAL_EXAGGERATION = 0.5

# Registers voiced with more energy than a neutral read push toward the top
# of the range; subdued/controlled ones toward the bottom. Anything not
# listed (neutral, or an unknown tag) stays at the neutral default.
_CHATTERBOX_HIGH_ENERGY = {"angry", "anxious", "playful", "commanding", "flirtatious", "sarcastic"}
_CHATTERBOX_LOW_ENERGY = {"sad", "suppressed_anger", "intimate", "warm", "formal", "dry_humor", "evasive"}


def chatterbox_exaggeration(emotion_info: dict = None) -> float:
    """Maps one line's detected emotion ({"emotion", "intensity"}, the shape
    detect_emotions/db.load_emotions return per line) to a Chatterbox
    exaggeration value, always inside the recommended 0.4-0.7 range. The
    distance from neutral scales with intensity, so a mildly annoyed line
    moves only a little and a furious one reaches the top. No tag at all
    (None, or never detected) reads as neutral."""
    if not emotion_info:
        return CHATTERBOX_NEUTRAL_EXAGGERATION
    emo = emotion_info.get("emotion")
    if emo in _CHATTERBOX_HIGH_ENERGY:
        target = CHATTERBOX_EXAGGERATION_MAX
    elif emo in _CHATTERBOX_LOW_ENERGY:
        target = CHATTERBOX_EXAGGERATION_MIN
    else:
        return CHATTERBOX_NEUTRAL_EXAGGERATION
    try:
        intensity = max(0.0, min(1.0, float(emotion_info.get("intensity", 0.5))))
    except (TypeError, ValueError):
        intensity = 0.5
    value = CHATTERBOX_NEUTRAL_EXAGGERATION + (target - CHATTERBOX_NEUTRAL_EXAGGERATION) * intensity
    return round(value, 2)

