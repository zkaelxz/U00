"""
diarize.py -- speaker diarization: figures out WHO is speaking when, so
lines can be grouped by character. Uses pyannote.audio, which needs:

  1. `pip install pyannote.audio`
  2. A free Hugging Face account + token: https://huggingface.co/settings/tokens
  3. Accepting the model terms at:
     https://huggingface.co/pyannote/speaker-diarization-3.1

This requires internet access on YOUR machine (to download the model
the first time) and works better with a GPU, but runs on CPU too --
just slower. Not run inside this sandbox since it has no network; the
code is here for you to run locally.

pyannote.audio 4.x needs Python 3.10+ and reads audio with ffmpeg
through torchcodec (3.x doesn't need either). Its pipeline(audio) call
also returns a different result type than 3.x -- see the getattr() in
diarize() below for why that's handled rather than assumed away.
"""

import os


def diarize(audio_path: str, hf_token: str, num_speakers: int = None):
    """
    Returns a list of {"start": float, "end": float, "speaker": str}
    covering who spoke when, e.g. "SPEAKER_00", "SPEAKER_01", ...
    """
    from pyannote.audio import Pipeline
    try:
        # pyannote.audio 3.1+ renamed this kwarg from use_auth_token to
        # token (following huggingface_hub's own rename) and newer
        # releases reject use_auth_token outright with a TypeError rather
        # than just deprecation-warning on it.
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", token=hf_token
        )
    except TypeError:
        # Older pyannote.audio installs (pre-3.1) don't accept `token`
        # either -- fall back to the name they actually expect.
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1", use_auth_token=hf_token
        )
    result = pipeline(audio_path, num_speakers=num_speakers)
    # pyannote.audio 4.x's pipeline(audio) returns a DiarizeOutput dataclass
    # (its .speaker_diarization attribute holds the actual Annotation)
    # instead of an Annotation directly, so .itertracks() would otherwise
    # break on 4.x with an AttributeError. 3.x's plain Annotation has no
    # such attribute, so this falls through to using it directly.
    annotation = getattr(result, "speaker_diarization", result)

    segments = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        segments.append({"start": turn.start, "end": turn.end, "speaker": speaker})
    return segments


def assign_speaker_to_line(line_start: float, line_end: float, speaker_segments):
    """Pick the speaker whose segment overlaps most with this line's time range."""
    best_speaker, best_overlap = None, 0.0
    for seg in speaker_segments:
        overlap = min(line_end, seg["end"]) - max(line_start, seg["start"])
        if overlap > best_overlap:
            best_overlap, best_speaker = overlap, seg["speaker"]
    return best_speaker


def label_lines_with_speakers(lines, speaker_segments):
    """Mutates lines in place, setting .speaker on each."""
    for ln in lines:
        ln.speaker = assign_speaker_to_line(ln.start, ln.end, speaker_segments)
    return lines
