"""
scanlate_condense.py -- shortens the English of signs whose text is too long for the band it
sits on, so it can be set at a readable size.
"""
import json
import re

import scanlate_tuning

# A long English sentence on a sign whose Chinese is a few characters: the band is one line high, and
# the typesetter would shrink it until it can't be read. Only the sign's own text is rewritten.
_NOTE = (" Keep every fact: each name, number, place and what happens. Drop only filler words and "
         "soft phrasing, add nothing, and keep the tone of a sign or notice.")


# A budget this small is a sound word or an exclamation ("Guh...") on a tiny band: there is
# nothing to condense without losing it, and the call would be wasted.
_SMALLEST_USEFUL_BUDGET = 12


def condense_long_signs(bubbles: list, measure, ask) -> list:
    """Replaces, in place, the translated text of each sign that `measure` says would be set
    below the readable size with the shorter text `ask` returns for it. `measure(bubble)` is the
    size the text would be set at, or None when the sign has no band to fit; `ask({key: (text,
    max_chars)})` is {key: shorter text}. A shortened text is used only when it is shorter than
    the original and not empty. Returns the changed bubbles."""
    comfort = scanlate_tuning.TYPESET_COMFORT_PX
    wanted = {}
    for key, b in enumerate(bubbles):
        text = (b.get("translated_text") or "").strip()
        if b.get("skip") or b.get("kind") != "sign" or not text:
            continue
        fitted = measure(b)
        if fitted is None or fitted >= comfort:
            continue
        # Room for text grows with the square of the size, so this is the share that fits at the
        # readable size.
        budget = int(len(text) * (fitted / comfort) ** 2)
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


def ask_for_shorter(engine, source_name: str, wanted: dict, usage_cb=None) -> dict:
    """{key: shorter English} from one engine call for the signs in `wanted` ({key: (text,
    max_chars)}); answers are matched by key, and anything not an exact key is dropped."""
    from engine_backends.llm_tasks import call_llm_json
    from engine_backends.shared import extract_first_json_value
    payload = json.dumps({k: {"text": t, "max_chars": n} for k, (t, n) in wanted.items()},
                         ensure_ascii=False)
    prompt = (f"These are English texts for signs in a {source_name} comic. Each is too long for the "
              "sign's band, so rewrite each to at most its max_chars characters." + _NOTE
              + f"\n\nSigns (JSON):\n{payload}\n\n"
              'Return ONLY a JSON object: {"shortened": {"<key>": "<shorter English>", ...}}. '
              "Use each key as given. No preamble, no markdown fences.")
    text = call_llm_json(engine, prompt, max_tokens=min(max(1500, 200 * len(wanted)), 8192),
                         fallback=None, usage_cb=usage_cb)
    if not text:
        return {}
    data = extract_first_json_value(re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip())
    answer = data.get("shortened") if isinstance(data, dict) else None
    return {str(k): v for k, v in answer.items()} if isinstance(answer, dict) else {}
