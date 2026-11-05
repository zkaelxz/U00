"""services/difficult_audio_service.py -- the per-title "Difficult audio" option for a
transcription run: which of its three parts (levelled copy, Whisper skip rule, sensitive
detector; docs/asr-experiments.md) the chosen recognizer honours, and the temporary levelled
copy itself. The Qwen3 recognizers that find speech with their own detector get a fourth
treatment instead of the Whisper ones: no detector at all, fixed windows.

The sensitive detector part is decided by the caller (sensitivity_preset.difficult_audio_view).
Imported by the transcription worker process, so it takes plain values and reads no database.
"""
import os
from dataclasses import dataclass, field

import audio_preprocess
import sensitivity_preset

LEVELLED_NAME = "leveled.wav"
# Set in the run's timing values when the title has the option on, so neither the worker nor
# the pipeline needs an argument of its own for it.
FLAG = "difficult_audio"
# Also set when Separate vocals first is on: the separation model should hear the original
# audio, so the levelled copy is not made.
NO_LEVELLING = "difficult_audio_no_levelling"
# The recognizers whose first pass is Whisper (Qwen3-ASR then re-transcribes its segments).
WHISPER_BACKENDS = ("whisper", "qwen3_asr")
# The Qwen3 recognizers whose speech detector is what drops quiet or shouted speech.
DETECTOR_BACKENDS = ("qwen3_asr_vad", "qwen3_asr_long")
# Set in the timing values handed to the pipeline when those recognizers should hear fixed
# windows instead of the detector's spans.
FIXED_WINDOWS = "difficult_audio_fixed_windows"
# 8 s heard the quiet lines and timed them better than 15 s on the one clip measured
# (docs/asr-experiments.md); the forced aligner then places the words inside each window.
FIXED_WINDOW_S = 8.0


@dataclass
class Prepared:
    """What the worker transcribes: the audio, the run's timing values (FLAG kept only when
    the Whisper skip rule applies) and plain notes for the result."""
    audio_path: str
    timing: dict | None = None
    notes: list = field(default_factory=list)

    def annotate(self, outcome: dict) -> dict:
        """Adds the notes to a finished run's warning text."""
        if self.notes and not outcome.get("failed_reason"):
            outcome["coverage_warning"] = " ".join(
                self.notes + [outcome.get("coverage_warning") or ""]).strip()
        return outcome


def mark(timing: dict, drama) -> dict:
    """The run's timing values, with FLAG set for a title that has the option on."""
    if not drama.get("difficult_audio"):
        return timing
    return {**timing, FLAG: True, NO_LEVELLING: bool(drama.get("separate_vocals_first"))}


def prepare(rep, audio_path, scratch_dir, transcript_mode, asr_backend_choice, use_groq,
            mixed_languages, timing, own_group=False) -> Prepared:
    """For a run whose timing values carry FLAG: the levelled copy goes in `scratch_dir`
    (the stored file at `audio_path` is not touched) and the returned timing keeps FLAG
    only where the Whisper skip rule applies (and gains FIXED_WINDOWS for the Qwen3 recognizers
    with their own detector). Anything that stops the levelling (no ffmpeg,
    it cannot start or fails, too little disk space, Separate vocals first) only skips it,
    with a note. A cancel needs no handling here: the worker is killed as a whole process
    group, ffmpeg included (it is started with own_group=False), and the parent then
    removes the scratch folder. A caller that is not such a worker (the Benchmark Lab runs in a
    thread) passes own_group=True, and `rep.cancelled()` then stops ffmpeg itself."""
    prepared = Prepared(audio_path, timing)
    if not (timing or {}).get(FLAG):
        return prepared
    prepared.timing = {k: v for k, v in timing.items() if k not in (FLAG, NO_LEVELLING)}
    if transcript_mode != "whisper" or use_groq:
        prepared.notes.append(
            "Difficult audio only applies when the app transcribes the audio itself "
            "(not Groq or a supplied transcript); it was ignored.")
        return prepared
    if asr_backend_choice in DETECTOR_BACKENDS and not mixed_languages:
        prepared.timing = {**prepared.timing, FIXED_WINDOWS: True}
        prepared.notes.append(
            "Difficult audio heard the audio in fixed windows with no speech detector, so quiet "
            "or shouted speech is not skipped. Line times come from the forced aligner; check "
            "lines over silence, which can be invented.")
    elif asr_backend_choice not in WHISPER_BACKENDS:
        prepared.notes.append(
            "Difficult audio levelled the sound only: this recognizer finds speech with its own "
            "detector, so the sensitive detector and Whisper's skip rule do not apply.")
    elif mixed_languages:
        prepared.notes.append(
            "Difficult audio levelled the sound and used the sensitive detector, but Whisper's "
            "skip rule is unchanged while per-span language detection is on.")
    else:
        prepared.timing = timing
    if timing.get(NO_LEVELLING):
        prepared.notes.append(
            "Difficult audio did not level the sound because Separate vocals first is on "
            "(the separation model works best on the original audio).")
        return prepared
    rep.progress(0.0, "Levelling the audio...")
    try:
        prepared.audio_path = audio_preprocess.level_audio(
            audio_path, os.path.join(scratch_dir, LEVELLED_NAME), cancel=rep.cancelled,
            own_group=own_group)
    except audio_preprocess.AudioLevelingUnavailable as exc:
        prepared.notes.append(str(exc))
    return prepared


def rehear_kwargs(drama) -> dict:
    """whisper_kwargs for hearing one line again: a title with the option on must not
    pass its lines through the speech detector that dropped them in the full run, or the
    re-transcription of a line the title already has finds no speech."""
    return whisper_kwargs({FLAG: True}) if drama.get("difficult_audio") else {}


def whisper_kwargs(timing) -> dict:
    """The extra keywords for core.transcribe_for_timing: none unless the run is a Difficult
    audio run, so every other run calls it exactly as before."""
    if not (timing or {}).get(FLAG):
        return {}
    return {"difficult_audio": True,
            "no_speech_threshold": timing.get("difficult_no_speech_threshold",
                                              sensitivity_preset.DIFFICULT_NO_SPEECH_THRESHOLD),
            "log_prob_threshold": timing.get("difficult_log_prob_threshold",
                                             sensitivity_preset.DIFFICULT_LOG_PROB_THRESHOLD)}
