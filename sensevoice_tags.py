"""
sensevoice_tags.py -- optional audio-derived emotion and sound-event tags
per line, from SenseVoiceSmall (https://github.com/FunAudioLLM/SenseVoice),
run alongside Whisper transcription.

SenseVoiceSmall labels each clip with one of 7 emotions (happy, sad, angry,
neutral, fearful, disgusted, surprised) and audio events (background music,
speech, applause, laughter, crying, sneezing, breathing, coughing) as part
of transcribing it. That's a second, AUDIO-derived signal, distinct from
emotion.py's text-based detection -- the two can disagree (a flat line
read sarcastically, laughter under a sad line), so the Workspace shows them
side by side rather than letting one silently override the other. Only the
text-based tags feed translation; these are stored separately
(drama_dir/audio_tags.json) and never merged into them.

Licensing: SenseVoice's code is MIT, but the model weights are under the
separate FunASR Model Open Source License
(https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE).

Requires: `pip install funasr` (model downloads on first use).
"""

import json
import os
import re
import tempfile

MODEL_ID = "iic/SenseVoiceSmall"
LICENSE_NOTE = ("SenseVoice code is MIT-licensed; its model weights are under the separate "
                "FunASR Model Open Source License -- check it before any commercial use.")
AUDIO_TAGS_FILE = "audio_tags.json"

EMOTIONS = ("happy", "sad", "angry", "neutral", "fearful", "disgusted", "surprised")
EVENTS = ("BGM", "Speech", "Applause", "Laughter", "Cry", "Sneeze", "Breath", "Cough")
EVENT_LABELS = {"BGM": "background music", "Speech": "speech", "Applause": "applause",
                "Laughter": "laughter", "Cry": "crying", "Sneeze": "sneeze",
                "Breath": "breathing", "Cough": "cough"}

_TOKEN = re.compile(r"<\|([^|]+)\|>")

# emotion.py's text registers that have a real audio counterpart. The rest
# (sarcastic, dry_humor, formal, evasive, ...) describe HOW something is
# meant, which audio emotion can't confirm or contradict -- sarcasm often
# sounds happy, which is exactly why both are shown.
_COMPARABLE_TEXT_EMOTIONS = {
    "neutral": "neutral", "angry": "angry", "suppressed_anger": "angry", "sad": "sad",
    "anxious": "fearful", "warm": "happy", "playful": "happy", "flirtatious": "happy",
}


class SenseVoiceUnavailable(RuntimeError):
    """funasr isn't installed, or the model couldn't load."""


def parse_rich_text(text: str) -> dict:
    """SenseVoice's raw output, e.g. "<|zh|><|HAPPY|><|Laughter|><|withitn|>好啊",
    -> {"emotion": "happy" | None, "events": ["Laughter"]}. Unknown or
    non-emotion/event tokens (language, text-normalisation) are ignored."""
    emotion, events = None, []
    for token in _TOKEN.findall(text or ""):
        if token.lower() in EMOTIONS:
            emotion = token.lower()
        elif token in EVENTS and token not in events:
            events.append(token)
    return {"emotion": emotion, "events": events}


def _load_model(use_gpu: bool):
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise SenseVoiceUnavailable("Audio emotion tags need: pip install funasr") from exc
    try:
        return AutoModel(model=MODEL_ID, device="cuda:0" if use_gpu else "cpu",
                         disable_update=True)
    except Exception as exc:
        raise SenseVoiceUnavailable(f"Couldn't load {MODEL_ID}: {exc}") from exc


def tag_lines(audio_path: str, lines, use_gpu: bool = False, progress_cb=None,
              cancel_check=None) -> dict:
    """{line_id: {"emotion", "events"}} for every line with a permanent id.
    Each line's own time slice is run through SenseVoice; results come back
    keyed by line id (a wav.scp list), never matched up by position."""
    from core import extract_audio_slice
    model = _load_model(use_gpu)
    lines = [ln for ln in lines if getattr(ln, "id", None) is not None and ln.end > ln.start]
    if not lines:
        return {}
    with tempfile.TemporaryDirectory(prefix="baihe_sensevoice_") as tmp:
        scp = os.path.join(tmp, "wav.scp")
        with open(scp, "w", encoding="utf-8") as f:
            for i, ln in enumerate(lines):
                if cancel_check:
                    cancel_check()
                clip = os.path.join(tmp, f"line_{ln.id}.wav")
                extract_audio_slice(audio_path, ln.start, ln.end, clip)
                f.write(f"line_{ln.id} {clip}\n")
                if progress_cb:
                    progress_cb(0.5 * (i + 1) / len(lines))
        results = model.generate(input=scp, language="auto", use_itn=False)
    tags = {}
    for r in results or []:
        key = str(r.get("key", ""))
        if key.startswith("line_") and key[5:].isdigit():
            tags[int(key[5:])] = parse_rich_text(r.get("text", ""))
    if progress_cb:
        progress_cb(1.0)
    return tags


def save_audio_tags(drama_dir: str, tags: dict) -> str:
    os.makedirs(drama_dir, exist_ok=True)
    path = os.path.join(drama_dir, AUDIO_TAGS_FILE)
    from core import atomic_write
    atomic_write(path, json.dumps({str(k): v for k, v in tags.items()},
                                  ensure_ascii=False, indent=2))
    return path


def load_audio_tags(drama_dir: str) -> dict:
    path = os.path.join(drama_dir, AUDIO_TAGS_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return {int(k): v for k, v in json.load(f).items()}
    except (OSError, ValueError, AttributeError):
        return {}


def side_by_side(lines, text_emotions: dict, audio_tags: dict) -> list:
    """One row per line that has either kind of tag: its text-based emotion
    (emotion.py, keyed by current line idx) and its audio emotion/events
    (keyed by permanent line id), each in its own column -- never merged.
    `disagree` marks lines where both exist, are comparable, and differ."""
    rows = []
    for ln in lines:
        text_tag = (text_emotions or {}).get(ln.idx)
        audio = (audio_tags or {}).get(getattr(ln, "id", None))
        if not text_tag and not audio:
            continue
        text_emotion = (text_tag or {}).get("emotion")
        audio_emotion = (audio or {}).get("emotion")
        rows.append({
            "line": ln.idx + 1,
            "text": ln.zh,
            "text_emotion": text_emotion or "",
            "audio_emotion": audio_emotion or "",
            "audio_events": ", ".join(EVENT_LABELS.get(e, e) for e in (audio or {}).get("events", [])
                                      if e != "Speech"),
            "disagree": bool(audio_emotion and _COMPARABLE_TEXT_EMOTIONS.get(text_emotion)
                             and _COMPARABLE_TEXT_EMOTIONS[text_emotion] != audio_emotion),
        })
    return rows
