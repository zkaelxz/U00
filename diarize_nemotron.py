"""NVIDIA Nemotron-3-Diarization inference: audio file -> speaker turns (opt-in alternative to pyannote)."""
from lib import model_offline

MODEL_ID = "nvidia/Nemotron-3-Diarization"
MODEL_LABEL = "nemotron-3-diarization"
SAMPLE_RATE = 16000
MISSING_PACKAGES_NOTICE = ("Nemotron speaker detection needs the transformers and librosa packages, "
                           "which aren't installed.")


def turns_from_segments(segments) -> list[dict]:
    """The model's {Start, End, Speaker} dicts as the app's turn dicts. Labels
    match pyannote's SPEAKER_NN so saved turns and merge_speakers need no change."""
    turns = []
    for seg in segments:
        start, end = float(seg["Start"]), float(seg["End"])
        if end > start:
            turns.append({"start": start, "end": end, "speaker": f"SPEAKER_{int(seg['Speaker']):02d}"})
    return sorted(turns, key=lambda t: (t["start"], t["end"]))


def diarize(audio_path: str, use_gpu: bool = False, on_progress=None) -> list[dict]:
    """Turns for the whole file. Raises RuntimeError with a plain sentence when
    a needed package is missing, so the caller can fall back to pyannote."""
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
    return turns_from_segments(segments)
