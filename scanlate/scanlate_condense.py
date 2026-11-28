"""
scanlate_condense.py -- shortens the English of signs and speech balloons whose text is too long
for the space it sits in, so it can be set at a readable size.
"""
import json
import re

from scanlate import scanlate_tuning
# A long English sentence on a sign whose Chinese is a few characters: the band is one line high, and
# the typesetter would shrink it until it can't be read. A small balloon with a long line has the same
# trouble. Only the text itself is rewritten.
_NOTE = (" Keep every fact: each name, number, place and what happens. Drop only filler words and "
         "soft phrasing, add nothing, and keep the tone of {tone}.")
_KINDS = {"sign": ("signs", "a sign or notice", "the sign's band"),
          "bubble": ("speech balloons", "the speaker, in natural complete sentences someone would "
                                        "say aloud, never a headline or fragment", "the balloon")}
# Speech is trimmed, not cut to a telegram: a balloon keeps at least this share of its text, and a
# line that would need more cut than that stays at the small size it fits at.
_MIN_KEPT_SHARE = {"sign": 0.0, "bubble": 0.6}


# A budget this small is a sound word or an exclamation ("Guh...") on a tiny band: there is
# nothing to condense without losing it, and the call would be wasted.
_SMALLEST_USEFUL_BUDGET = 12


def condense_long_texts(bubbles: list, measure, ask, kind: str = "sign") -> list:
    """Replaces, in place, the translated text of each region of `kind` ("sign" or "bubble") that
    `measure` says would be set below the readable size with the shorter text `ask` returns for
    it. `measure(bubble)` is the size the text would be set at, or None when there is no space to
    measure; `ask({key: (text, max_chars)})` is {key: shorter text}. A shortened text is used only when it is shorter than
    the original and not empty. Returns the changed bubbles."""
    comfort = scanlate_tuning.TYPESET_COMFORT_PX
    wanted = {}
    for key, b in enumerate(bubbles):
        text = (b.get("translated_text") or "").strip()
        if b.get("skip") or (b.get("kind") or "bubble") != kind or not text:
            continue
        fitted = measure(b)
        if fitted is None or fitted >= comfort:
            continue
        # Room for text grows with the square of the size, so this is the share that fits at the
        # readable size.
        budget = int(len(text) * (fitted / comfort) ** 2)
        if budget < _MIN_KEPT_SHARE[kind] * len(text):
            continue
        if budget >= _SMALLEST_USEFUL_BUDGET:
            wanted[str(key)] = (text, budget)
    if not wanted:
        return []
    changed = []
    for key, short in (ask(wanted) or {}).items():
        short = short.strip() if isinstance(short, str) else ""
        if key in wanted and short and len(short) < len(wanted[key][0]):
            bubbles[int(key)]["translated_text"] = short
            changed.append(bubbles[int(key)])
    return changed


def ask_for_shorter(engine, source_name: str, wanted: dict, usage_cb=None, kind: str = "sign") -> dict:
    """{key: shorter English} from one engine call for the texts in `wanted` ({key: (text,
    max_chars)}); answers are matched by key, and anything not an exact key is dropped."""
    from engine_backends.llm_tasks import call_llm_json
    from engine_backends.line_text import extract_first_json_value
    payload = json.dumps({k: {"text": t, "max_chars": n} for k, (t, n) in wanted.items()},
                         ensure_ascii=False)
    things, tone, space = _KINDS[kind]
    prompt = (f"These are English texts for {things} in a {source_name} comic. Each is too long for "
              f"{space}, so rewrite each to at most its max_chars characters." + _NOTE.format(tone=tone)
              + f"\n\n{things.capitalize()} (JSON):\n{payload}\n\n"
              'Return ONLY a JSON object: {"shortened": {"<key>": "<shorter English>", ...}}. '
              "Use each key as given. No preamble, no markdown fences.")
    text = call_llm_json(engine, prompt, max_tokens=min(max(1500, 200 * len(wanted)), 8192),
                         fallback=None, usage_cb=usage_cb)
    if not text:
        return {}
    data = extract_first_json_value(re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip(),
                                    expect=dict)
    answer = data.get("shortened") if isinstance(data, dict) else None
    return {str(k): v for k, v in answer.items()} if isinstance(answer, dict) else {}
