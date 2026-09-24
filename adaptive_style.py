"""
adaptive_style.py -- learns your translation preferences from the edits
you actually make, and folds them into future translation prompts.

The mechanism is simple and honest: every time you rewrite a line in
the review table, the before/after pair is recorded. Once enough
samples accumulate, they're analyzed for consistent patterns -- do you
reliably make things shorter? more literal? do you keep more Chinese
terms than the model does? prefer different punctuation? -- and the
result becomes a style profile injected into later prompts.

Deliberately conservative: it only reports patterns that show up
repeatedly, and the profile is always shown for review before it's
applied, because a mis-learned preference would quietly degrade every
future translation.
"""

import re
import json
from translate_engines import call_llm_json

MIN_SAMPLES_TO_LEARN = 8


def analyze_edit_patterns(edit_samples, engine, existing_profile: dict = None, usage_cb=None):
    """
    Looks at before/after pairs and extracts consistent preferences.

    Returns {"preferences": [str], "summary": str, "confidence": str}
    -- a list of concrete, actionable rules rather than vague
    impressions, since the whole point is to inject them into a prompt.
    """
    if not getattr(engine, "supports_reference", False):
        return {"preferences": [], "summary": "Needs an LLM engine.", "confidence": "none"}
    if len(edit_samples) < MIN_SAMPLES_TO_LEARN:
        return {"preferences": [], "confidence": "insufficient",
                "summary": (f"Only {len(edit_samples)} edit(s) recorded. "
                            f"At least {MIN_SAMPLES_TO_LEARN} are needed before patterns "
                            "are meaningful rather than noise.")}

    pairs = "\n\n".join(
        f"Source: {s['zh']}\nAI wrote: {s['ai_version']}\nYou changed it to: {s['user_version']}"
        for s in edit_samples[:60])

    prior = ""
    if existing_profile and existing_profile.get("preferences"):
        prior = ("\n\nPreviously learned preferences (confirm, refine, or drop these based "
                 "on the new evidence):\n"
                 + "\n".join(f"- {p}" for p in existing_profile["preferences"]))

    prompt = (
        "Below are translation edits a person made to AI-generated English translations of "
        "Chinese text. Identify their CONSISTENT stylistic preferences -- patterns that "
        "recur across multiple edits, not one-off fixes.\n\n"
        "Look for things like: preferred sentence length, more literal vs more natural "
        "phrasing, whether they keep more or fewer romanized Chinese terms, how they handle "
        "honorifics and forms of address, punctuation habits, register/formality, and "
        "whether they tighten or expand.\n\n"
        "Be conservative. Only report a preference if you can see it in at least two or "
        "three separate edits. If the edits are mostly one-off corrections with no pattern, "
        "say so rather than inventing rules.\n\n"
        'Return ONLY JSON: {"preferences": ["concrete instruction phrased as a rule a '
        'translator could follow", ...], "summary": "one-sentence characterization", '
        '"confidence": "low" | "medium" | "high"}. No preamble, no markdown fences.'
        + prior + f"\n\nEdits:\n{pairs}"
    )

    text = call_llm_json(engine, prompt, max_tokens=1500, fallback="{}", usage_cb=usage_cb)
    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"preferences": [], "summary": "Could not analyze.", "confidence": "none"}
    return {
        "preferences": data.get("preferences", []),
        "summary": data.get("summary", ""),
        "confidence": data.get("confidence", "low"),
    }


def profile_to_prompt_block(profile: dict) -> str:
    """Renders a learned profile as prompt text. Returns empty string if
    there's nothing worth injecting, so callers can concatenate safely."""
    if not profile or not profile.get("preferences"):
        return ""
    rules = "\n".join(f"- {p}" for p in profile["preferences"])
    return (
        "LEARNED STYLE PREFERENCES (derived from this person's own edits to previous "
        "translations -- follow these unless they conflict with the term glossary, which "
        f"always wins):\n{rules}\n"
    )


def diff_summary(ai_version: str, user_version: str) -> dict:
    """Cheap local signal about a single edit -- no API call. Useful for
    showing at-a-glance what kind of change was made."""
    ai_words = len(ai_version.split())
    user_words = len(user_version.split())
    delta = user_words - ai_words
    if delta <= -3:
        kind = "shortened"
    elif delta >= 3:
        kind = "expanded"
    else:
        kind = "rephrased"
    return {"kind": kind, "word_delta": delta,
            "ai_words": ai_words, "user_words": user_words}


def summarize_edit_tendencies(edit_samples) -> dict:
    """Local statistics across all recorded edits -- gives an immediate
    sense of your habits without waiting for (or paying for) analysis."""
    if not edit_samples:
        return {"total": 0}
    kinds = {"shortened": 0, "expanded": 0, "rephrased": 0}
    total_delta = 0
    for s in edit_samples:
        d = diff_summary(s["ai_version"] or "", s["user_version"] or "")
        kinds[d["kind"]] += 1
        total_delta += d["word_delta"]
    return {
        "total": len(edit_samples),
        "shortened": kinds["shortened"],
        "expanded": kinds["expanded"],
        "rephrased": kinds["rephrased"],
        "avg_word_delta": round(total_delta / len(edit_samples), 1),
    }


