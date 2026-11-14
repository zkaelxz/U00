"""NVIDIA Nemotron-3-Diarization inference: audio file -> speaker turns and voice prints (opt-in alternative to pyannote)."""
from typing import Callable

from diarize import CONFIDENCE_BIN_SECONDS
from lib import model_offline

MODEL_ID = "nvidia/Nemotron-3-Diarization"
MODEL_LABEL = "nemotron-3-diarization"
SAMPLE_RATE = 16000
PYANNOTE_REPO = "pyannote/speaker-diarization-community-1"
# Voice prints must come from the embedding model community-1 uses, or they would not be
# comparable with the prints enrolled voices were saved from.
MIN_PRINT_TURN_SECONDS = 1.0
MAX_PRINT_SPEECH_SECONDS = 600.0
PRINT_PIECE_SECONDS = 30.0
MISSING_PACKAGES_NOTICE = ("Nemotron speaker detection needs the transformers and librosa packages, "
                           "which aren't installed.")


def turns_from_segments(segments, probs=None, frame_seconds: float = 0.01) -> list[dict]:
    """The model's {Start, End, Speaker} dicts as the app's turn dicts. Labels
    match pyannote's SPEAKER_NN so saved turns and merge_speakers need no change.
    With probs (frames x speakers, the model's per-frame speaker probabilities)
    each turn also gets "confidence" (confidence_bins), which merge_speakers uses
    to leave unsure lines blank."""
    turns = []
    for seg in segments:
        start, end = float(seg["Start"]), float(seg["End"])
        if end > start:
            turn = {"start": start, "end": end, "speaker": f"SPEAKER_{int(seg['Speaker']):02d}"}
            if probs is not None:
                turn["confidence"] = confidence_bins(probs, start, end, int(seg["Speaker"]),
                                                     frame_seconds)
            turns.append(turn)
    return sorted(turns, key=lambda t: (t["start"], t["end"]))


def confidence_bins(probs, start: float, end: float, speaker: int, frame_seconds: float) -> list:
    """The turn's own speaker's mean probability per CONFIDENCE_BIN_SECONDS from the
    turn's start (the last bin may be shorter), to two decimals. Binned rather than
    kept per frame, which would make the saved turns file ten times bigger."""
    first, last = round(start / frame_seconds), round(end / frame_seconds)
    per_bin = max(1, round(CONFIDENCE_BIN_SECONDS / frame_seconds))
    own = probs[first:last, speaker]
    return [round(float(own[i:i + per_bin].mean()), 2) for i in range(0, len(own), per_bin)]


def embeddings_from_turns(audio, sample_rate: int, turns, embed: Callable) -> dict:
    """{speaker: [float, ...]}: per speaker, the duration-weighted mean of embed(piece)
    over its longest turns of at least a second (until about ten minutes of speech), each
    turn cut into pieces of at most half a minute to bound memory. Matches how the
    pyannote run's centroids compare (cosine 0.97-0.98 for the main speakers on a 3 h
    title). A speaker with no turn that long gets no print."""
    import numpy as np
    by_speaker: dict[str, list] = {}
    for t in turns:
        if t["end"] - t["start"] >= MIN_PRINT_TURN_SECONDS:
            by_speaker.setdefault(t["speaker"], []).append(t)
    prints = {}
    for speaker, spans in by_speaker.items():
        total, acc, used = 0.0, None, 0.0
        for t in sorted(spans, key=lambda t: t["start"] - t["end"]):
            if used >= MAX_PRINT_SPEECH_SECONDS:
                break
            start = t["start"]
            while start < t["end"] and used < MAX_PRINT_SPEECH_SECONDS:
                end = min(t["end"], start + PRINT_PIECE_SECONDS)
                piece = audio[int(start * sample_rate):int(end * sample_rate)]
                start = end
                if len(piece) < sample_rate * 0.5:
                    continue
                vec = np.asarray(embed(piece), dtype="float64")
                weight = len(piece) / sample_rate
                acc = vec * weight if acc is None else acc + vec * weight
                total += weight
                used += weight
        if acc is not None and total > 0:
            prints[speaker] = [float(x) for x in acc / total]
    return prints


def _pyannote_embedder(use_gpu: bool):
    """A function numpy audio (16 kHz mono) -> embedding vector, from the embedding folder
    of the already downloaded community-1 snapshot; None when that isn't on this PC (it is
    never downloaded here: voice prints are a bonus, not worth a download)."""
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding
    try:
        folder = snapshot_download(PYANNOTE_REPO, local_files_only=True)
    except Exception:
        return None
    device = torch.device("cuda" if use_gpu and torch.cuda.is_available() else "cpu")
    model = PretrainedSpeakerEmbedding(f"{folder}/embedding", device=device)

    def embed(piece):
        wave = torch.from_numpy(np.ascontiguousarray(piece, dtype="float32"))[None, None, :]
        return model(wave)[0]
    return embed


def diarize(audio_path: str, use_gpu: bool = False, on_progress=None) -> tuple[list[dict], dict]:
    """(turns, voice prints) for the whole file. Raises RuntimeError with a plain sentence
    when a needed package is missing, so the caller can fall back to pyannote. The prints
    are {} when they can't be made (pyannote or its embedding model isn't on this PC, or
    the step fails): the turns alone are still a good result."""
    say = on_progress or (lambda frac, message: None)
    try:
        import librosa
        import torch
        from transformers import AutoModelForAudioFrameClassification, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(MISSING_PACKAGES_NOTICE) from exc
    local_only = model_offline.active()
    say(0.02, "Loading Nemotron speaker model...")
    processor = AutoProcessor.from_pretrained(MODEL_ID, local_files_only=local_only)
    model = AutoModelForAudioFrameClassification.from_pretrained(
        MODEL_ID, local_files_only=local_only,
        device_map="cuda" if use_gpu and torch.cuda.is_available() else "cpu")
    say(0.1, "Detecting speakers (Nemotron)...")
    audio, _ = librosa.load(audio_path, sr=SAMPLE_RATE, mono=True)
    inputs = processor(audio, sampling_rate=SAMPLE_RATE).to(model.device, dtype=model.dtype)
    with torch.inference_mode():
        logits = model(**inputs).logits
    segments = processor.extract_speaker_dict(logits, inputs.attention_mask)[0]
    # The same sigmoid extract_speaker_dict thresholds at 0.5, so each turn is exactly
    # the run of frames where its speaker's probability is above that.
    probs = logits[0].float().sigmoid().cpu().numpy()
    extractor = processor.feature_extractor
    turns = turns_from_segments(segments, probs, extractor.hop_length / extractor.sampling_rate)
    # Freed before the embedding model loads so the two never share the GPU.
    del model, inputs, logits, probs
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    say(0.8, "Making voice prints...")
    try:
        embed = _pyannote_embedder(use_gpu)
        prints = embeddings_from_turns(audio, SAMPLE_RATE, turns, embed) if embed and turns else {}
    except Exception:
        prints = {}
    return turns, prints
