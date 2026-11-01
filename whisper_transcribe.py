"""
whisper_transcribe.py -- speech-to-text for timing: the local faster-whisper
path, the opt-in Groq cloud path and the Whisper prompt/sensitivity settings.
"""

import json
import os
import re

import whisper_models
from segment_splitting import _word_dicts, settle_overlaps, tighten_to_words
from whisper_hallucination import STOCK_PHRASE_ISOLATION_SEC, filter_hallucinated_segments




# ---------------------------------------------------------------------------
# Transcribe audio for timing (faster-whisper)
# ---------------------------------------------------------------------------

# Bounds of the "Speech-splitting sensitivity" (min_silence_ms) setting, enforced
# for the saved value and for auto-tune candidates. Keep in sync with
# frontend/src/pages/workspace/sourceForm.ts. 100 ms is about three Silero VAD
# windows, so shorter values stop being distinguishable from the detector's noise.
MIN_SILENCE_MS_MIN = 100
MIN_SILENCE_MS_MAX = 3000
# Auto-tune's default candidate min_silence_duration_ms values -- spans the
# "Speech-splitting sensitivity" slider's real range meaningfully (300 is the
# app's default, services/transcribe_service._DEFAULT_TUNING; 3000 the
# slider's max) without an unbounded number of full re-transcriptions.
DEFAULT_AUTOTUNE_CANDIDATES_MS = [300, 800, 1500]


# Stops Whisper's repeated-phrase loops after music or silence;
# filter_hallucinated_segments is the backstop.
WHISPER_ANTI_LOOP_KWARGS = {"condition_on_previous_text": False}
# The per-title repeat guard. Off by default: a 3-token CJK sequence is often
# one common particle (的, の), so the ban rewrites or cuts real speech.
WHISPER_REPEAT_GUARD_KWARGS = {"no_repeat_ngram_size": 3, "repetition_penalty": 1.1}


# Seconds of silence inside a segment's word timings above which faster-whisper
# drops that segment as a likely hallucination. Off by default: it dropped real
# fast or quiet CJK lines.
DEFAULT_HALLUCINATION_SILENCE_SEC = 0.0


# A segment at least this long holding fewer letters per second than this is Whisper having
# jumped over speech: on shouted or slurred audio it can close a segment 20-80 s after it
# opened with a few words in it. _rehear_sparse hears such stretches again on their own.
SPARSE_SEGMENT_MIN_SEC = 6.0
SPARSE_MAX_CHARS_PER_SEC = 1.5
# Word padding can put a re-heard segment's edge a little outside the stretch it came from.
_REHEAR_EDGE_SEC = 0.5


def _letters(text: str) -> int:
    return sum(ch.isalnum() for ch in text or "")


def _is_sparse(seg: dict) -> bool:
    length = seg["end"] - seg["start"]
    return length >= SPARSE_SEGMENT_MIN_SEC and _letters(seg["text"]) / length < SPARSE_MAX_CHARS_PER_SEC


def _segment_dicts(segments, prompt_keys: set, on_segment=None, on_kept=None) -> list:
    """faster-whisper segments as {start, end, text[, words]} dicts, times narrowed to the
    spoken words; on_segment(s) is called for every segment, kept or not, on_kept(dict)
    for each one kept."""
    result = []
    for s in segments:
        text = s.text.strip()
        # A segment with no letter, digit or CJK character (a lone "[" from
        # a cut-off sound tag, "...", a dash) is not a subtitle.
        if any(ch.isalnum() for ch in text) and _alnum_key(text) not in prompt_keys:
            start, end = tighten_to_words(s.start, s.end, getattr(s, "words", None))
            seg = {"start": start, "end": end, "text": text}
            words = _word_dicts(getattr(s, "words", None))
            if words:
                seg["words"] = words
            result.append(seg)
            if on_kept:
                on_kept(seg)
        if on_segment:
            on_segment(s)
    return result


# A resumable run hands its finished segments on in units of about this much audio.
CHECKPOINT_UNIT_SECONDS = 120.0


def _shifted(seg: dict, offset: float) -> dict:
    """A copy of a segment dict `offset` seconds later (a decode that started `offset` into the file).
    Always a copy: a unit handed to on_unit must not change when settle_overlaps edits the list in place."""
    out = {**seg, "start": seg["start"] + offset, "end": seg["end"] + offset}
    if "words" in seg:
        out["words"] = [{**w, "start": w["start"] + offset, "end": w["end"] + offset} for w in seg["words"]]
    return out


def _spoken_end(seg: dict) -> float:
    """Where the speech of a stored segment stops: its last word, since the segment's own end
    carries the padding tighten_to_words adds, which can reach into the next line's first word."""
    words = seg.get("words")
    return words[-1]["end"] if words else seg["end"]


class _Resume:
    """What a decode that continues after earlier units needs: where it starts, the segments
    already in hand, and the batching of its own new segments into units for on_unit."""

    def __init__(self, segments, on_unit):
        self.segments = list(segments or [])
        self.at = max((_spoken_end(s) for s in self.segments), default=0.0)
        self.on_unit = on_unit
        self.unit = []
        self.reported_end = self.at

    def restart(self):
        """A decode starting over (the CPU retry after a GPU failure part way): what was
        gathered for the next unit is dropped, and audio already reported is not reported twice."""
        self.unit.clear()

    def give_up(self):
        """The earlier text cannot be continued from: the whole file is decoded and no units are
        reported, so the stored ones stay valid for the next try."""
        self.__init__(None, None)

    def keep(self, seg):
        seg = _shifted(seg, self.at)
        if seg["end"] <= self.reported_end:
            return
        self.unit.append(seg)
        if seg["end"] - self.unit[0]["start"] >= CHECKPOINT_UNIT_SECONDS:
            self.on_unit(list(self.unit))
            self.reported_end = seg["end"]
            self.unit.clear()

    def merged(self, tail_segments):
        self.unit.clear()
        return self.segments + [_shifted(s, self.at) for s in tail_segments]


def _relaxed_kwargs(model, no_speech_threshold, log_prob_threshold) -> dict:
    """Difficult audio's relaxed skip thresholds. Each is passed only to a faster-whisper that
    knows it; the batched pipeline of fast mode is a different method that is not probed."""
    # Imported here: db.py's import check copies core.py alone into a temp folder.
    from sensitivity_preset import DIFFICULT_LOG_PROB_THRESHOLD, DIFFICULT_NO_SPEECH_THRESHOLD
    relaxed = {
        "no_speech_threshold": (DIFFICULT_NO_SPEECH_THRESHOLD
                                if no_speech_threshold is None else no_speech_threshold),
        "log_prob_threshold": (DIFFICULT_LOG_PROB_THRESHOLD
                               if log_prob_threshold is None else log_prob_threshold)}
    return {k: float(v) for k, v in relaxed.items() if _accepts_transcribe_kwarg(model, k)}


_WAV_HEADER_BYTES = 128


def _cut_tail(audio_path: str, resume):
    """The audio from where `resume` stops in a temp wav, or None when nothing is left after it
    (or, after resume.give_up(), when there is nothing to continue). A resumed decode cuts the
    file instead of passing faster-whisper clip_timestamps, which turns the speech detector off
    for the whole call."""
    import tempfile

    import audio_extract
    if not resume.at:
        return None
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        # No upper end: an end past the file is clamped by ffmpeg, and the real length is not known here.
        audio_extract.extract_audio_slice(audio_path, resume.at, resume.at + 365 * 86400.0, path,
                                          timeout=audio_extract.EXTRACT_AUDIO_TIMEOUT_SECONDS)
        if os.path.getsize(path) > _WAV_HEADER_BYTES:
            return path
    except (OSError, audio_extract.FfmpegError, audio_extract.FfmpegTimeout):
        resume.give_up()   # no ffmpeg, a full disk or a hang
    except BaseException:
        os.unlink(path)
        raise
    os.unlink(path)
    return None


def _rehear_sparse(model, audio_path, kwargs: dict, segments: list, prompt_keys: set) -> list:
    """`segments` with each sparse one (_is_sparse) replaced by what Whisper hears in that
    stretch alone, when that holds more text. The stretch is decoded without the speech
    detector: it is the detector's cutting, which stitches the kept speech together, that
    lets Whisper skip a stretch, and a detector that scored the speech as unlikely would drop
    it again. A faster-whisper without clip_timestamps leaves the segments as they are."""
    sparse = [s for s in segments if _is_sparse(s)]
    if not sparse or not _accepts_transcribe_kwarg(model, "clip_timestamps"):
        return segments
    retry = {k: v for k, v in kwargs.items() if k != "vad_parameters"}
    retry.update(vad_filter=False, clip_timestamps=[t for s in sparse for t in (s["start"], s["end"])])
    heard, _info = model.transcribe(audio_path, **retry)
    heard = _segment_dicts(heard, prompt_keys)
    result = []
    for seg in segments:
        if seg in sparse:
            inside = [h for h in heard if h["start"] >= seg["start"] - _REHEAR_EDGE_SEC
                      and h["end"] <= seg["end"] + _REHEAR_EDGE_SEC]
            if _letters("".join(h["text"] for h in inside)) > _letters(seg["text"]):
                result.extend(inside)
                continue
        result.append(seg)
    return settle_overlaps(result)


# Whisper writes Mandarin in Traditional characters with no punctuation about as
# often as in Simplified; a sentence in the wanted script and with punctuation,
# shown as text it has already written, steers it to both.
CHINESE_SCRIPT_HINTS = {"simplified": "以下是普通话的句子。", "traditional": "以下是普通話的句子。"}


def script_hint(language: str, chinese_script=None) -> str:
    return CHINESE_SCRIPT_HINTS.get(chinese_script or "", "") if language == "zh" else ""


def whisper_prompt(initial_prompt: str, language: str, chinese_script=None) -> str:
    """The text Whisper is primed with: for a zh title whose chinese_script is
    "simplified" or "traditional", its CHINESE_SCRIPT_HINTS sentence, then the names."""
    prompt = (initial_prompt or "").strip()
    hint = script_hint(language, chinese_script)
    return hint + prompt if hint and hint not in prompt else prompt


def _alnum_key(text: str) -> str:
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _add_prompt(model, kwargs: dict, initial_prompt: str, language: str, chinese_script,
                every_window: bool = True) -> set:
    """Puts the prompt (whisper_prompt) into the transcribe kwargs; returns the
    texts a segment is dropped for matching, since a primed model sometimes
    writes the script hint back over noise. Without a hint nothing is dropped:
    a prompt of one name has the same letters as a line that only calls it.

    every_window False (Live's carried context, whose newest words matter
    most) keeps initial_prompt."""
    prompt = whisper_prompt(initial_prompt, language, chinese_script)
    if prompt:
        # With condition_on_previous_text off, initial_prompt only reaches the
        # first 30-second window, and faster-whisper keeps its tail when it is
        # too long; hotwords is shown to every window and keeps its head, where
        # the names are.
        hotwords = every_window and _accepts_transcribe_kwarg(model, "hotwords")
        kwargs["hotwords" if hotwords else "initial_prompt"] = prompt
    hint = script_hint(language, chinese_script)
    return {_alnum_key(hint), _alnum_key(prompt)} if hint else set()


def build_initial_prompt(terms, max_terms: int = 40) -> str:
    """
    Builds a hint string of proper nouns for Whisper's `initial_prompt`.

    Whisper conditions on this text, which makes a large difference for
    exactly the words it otherwise gets wrong: character names, sects,
    place names. Chinese is especially unforgiving here because a
    misheard name is usually still a valid word, so nothing looks wrong
    until you read the translation.

    `terms` accepts glossary rows or plain strings, so a glossary built
    from the novel can feed straight back into transcription. A glossary
    row's `aliases` (pipe-separated alt spellings/transliterations
    of term_original) are primed too, not just the canonical original --
    whichever spelling Whisper actually latches onto still helps.
    """
    names = []
    for t in (terms or []):
        if isinstance(t, dict):
            term = t.get("term_original")
            if term and str(term).strip():
                names.append(str(term).strip())
            for alias in re.split(r"[|,，、]", t.get("aliases") or ""):
                alias = alias.strip()
                if alias:
                    names.append(alias)
        else:
            term = t
            if term and str(term).strip():
                names.append(str(term).strip())
    if not names:
        return ""
    return "、".join(names[:max_terms]) + "。"


def _accepts_transcribe_kwarg(model, name):
    """False for a faster-whisper release that predates `name`, which would
    otherwise fail the whole run with a TypeError."""
    import inspect
    try:
        params = inspect.signature(model.transcribe).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


# Only the opening is looked at: the check must cost a few seconds, and the
# start of a title is where a wrong language shows.
LANGUAGE_CHECK_WINDOW_S = 120
LANGUAGE_CHECK_MIN_PROBABILITY = 0.80
CHECKED_LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean", "en": "English"}


def _decode_opening(audio_path, seconds):
    """16 kHz mono float samples of the first `seconds` of the file. Cutting the opening
    first keeps the check at a second or two however long the drama is."""
    import os
    import tempfile

    import audio_extract
    from asr_backend import load_audio_16k
    fd, clip = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        audio_extract.extract_audio_slice(audio_path, 0.0, seconds, clip)
        return load_audio_16k(clip)
    finally:
        os.unlink(clip)


def language_mismatch_notice(model, audio_path, title_language):
    """One sentence when the opening speech is confidently another language than
    the title's, else None. Never raises: a failed check must not fail the run."""
    chosen = CHECKED_LANGUAGE_NAMES.get(title_language)
    if chosen is None:
        return None
    try:
        audio = _decode_opening(audio_path, LANGUAGE_CHECK_WINDOW_S)
        # vad_filter makes the 30 s window start at the first speech, not at silence.
        detected, probability, _all = model.detect_language(audio, vad_filter=True)
    except Exception:
        return None
    heard = CHECKED_LANGUAGE_NAMES.get(detected)
    if heard is None or detected == title_language or probability < LANGUAGE_CHECK_MIN_PROBABILITY:
        return None
    return f"The audio sounds like {heard}, but this title is set to {chosen}."


def transcribe_for_timing(audio_path: str, model_size: str = "medium", language: str = "zh",
                           use_gpu: bool = False, local_model_path: str | None = None,
                           hf_token: str | None = None, initial_prompt: str = "",
                           beam_size: int = 5, min_silence_duration_ms: int = 2000,
                           vad_threshold: float = 0.5, filter_hallucination_repeats: int = 4,
                           on_gpu_fallback=None, progress_cb=None, fast_mode: bool = False,
                           hallucination_silence_sec: float = DEFAULT_HALLUCINATION_SILENCE_SEC,
                           repeat_guard: bool = False, sensitivity_preset: str = "normal",
                           stock_isolation_sec: float = STOCK_PHRASE_ISOLATION_SEC,
                           difficult_audio: bool = False, no_speech_threshold: float = None,
                           log_prob_threshold: float = None, speech_pad_ms: int = None,
                           on_notice=None, chinese_script: str = None, prompt_every_window: bool = True,
                           resume_segments=None, on_unit=None):
    """
    speech_pad_ms: padding faster-whisper keeps around each detected speech stretch; None
    leaves its own default (400 ms) in place.

    initial_prompt: proper nouns to prime recognition with -- see
    build_initial_prompt(). Costs nothing and is the single biggest free
    accuracy win for names.

    beam_size: higher searches more alternatives before committing.
    5 is faster-whisper's default; 8-10 is measurably better on difficult
    audio at a real speed cost.

    The voice-activity detector faster-whisper runs before transcription
    IS Silero VAD (faster_whisper/vad.py is adapted directly from
    snakers4/silero-vad) -- not a separate technology worth swapping in,
    confirmed by reading faster-whisper's own source. What's actually
    missing is exposing more of Silero's own tunable parameters, which
    only min_silence_duration_ms was until this was added:

    min_silence_duration_ms: the voice-activity detector's default (2000ms)
    merges any two stretches of speech separated by LESS than 2 seconds of
    silence into one continuous segment. For content with back-to-back
    dialogue, internal-monologue narration, or quick exchanges -- pauses
    well under 2s between distinct lines -- this default routinely merges
    several real lines into one oversized segment, which then gets treated
    as a single subtitle line with only its first sentence's text. Lowering
    this (500-1000ms) splits those apart at real pauses instead. Too low
    and it starts splitting mid-sentence on natural speech pauses, so this
    is a genuine tradeoff, not a strictly-better default.

    vad_threshold: Silero's own speech-probability cutoff (0.0-1.0,
    default 0.5) -- how confident it must be that a stretch of audio is
    speech before keeping it. Lower (0.3-0.4) catches quiet/mumbled
    dialogue or a distant speaker that the default cuts as silence, at
    the cost of more background noise/music misclassified as speech.
    Higher (0.6-0.7) is the fix for the opposite failure: a noisy or
    music-heavy source getting non-speech transcribed as phantom lines.
    Same genuine tradeoff shape as min_silence_duration_ms -- there's no
    value that's strictly better for every source.

    filter_hallucination_repeats: passed to filter_hallucinated_segments()
    (see its own docstring) -- collapses a run of this many or more
    consecutive segments with identical text, a well-known Whisper
    failure mode on silence/music that its own per-segment decoding
    thresholds don't catch. Set to 0 or None to disable entirely.

    stock_isolation_sec: the silence a credit line ("Thanks for watching") needs on
    each side to be dropped; see filter_hallucinated_segments.

    hallucination_silence_sec: faster-whisper's hallucination_silence_threshold --
    a segment with a silent gap this long inside its word timings is
    skipped as a likely hallucination. 0 or None turns it off. Not passed
    when faster-whisper is too old to know it, and ignored by faster-whisper
    itself in fast_mode (its batched pipeline hard-codes it off).

    on_gpu_fallback: optional callback invoked with the original exception
    if a requested GPU run fails at actual inference time and this
    transparently retries on CPU -- so the caller can tell the person
    their GPU didn't actually get used, since CPU is meaningfully slower
    and silently downgrading without saying so would be confusing.

    progress_cb: optional callback invoked with a 0.0-1.0 fraction as
    segments come in. faster-whisper's `transcribe()` returns a lazy
    generator -- it doesn't process the whole file up front -- so this can
    report real progress instead of a spinner that never moves, which is
    the difference between a stuck-looking app and a working one on a
    multi-hour file. Progress is estimated from how far into the audio the
    latest segment ends (`info.duration` is faster-whisper's own total
    length estimate); silently reports nothing if that's unavailable.

    fast_mode: opt-in -- runs faster-whisper's BatchedInferencePipeline,
    which decodes several VAD chunks at once (roughly 4x faster on a GPU).
    Same settings, same output shape; uses more VRAM while it runs.

    resume_segments / on_unit: see _Resume and _cut_tail. Ignored in fast_mode.

    on_notice: called with one sentence when the opening speech is confidently another
    language than `language` (see language_mismatch_notice); the run goes on unchanged.

    difficult_audio: turns the speech detector off (except in fast_mode) and relaxes
    Whisper's own skip rule (no_speech_threshold / log_prob_threshold, None = the preset's
    numbers) so low-confidence screamed or whispered speech is kept. Off, the detector runs
    and neither threshold is passed, so faster-whisper's defaults apply. The hallucination
    filters below still run on what comes back. A long segment with almost no text is heard
    again on its own (_rehear_sparse).
    """
    # Imported here: db.py's import check copies core.py alone into a temp folder.
    from sensitivity_preset import decode_kwargs
    model = whisper_models.load_whisper_model(model_size, use_gpu=use_gpu, local_model_path=local_model_path,
                                hf_token=hf_token)
    if on_notice and (notice := language_mismatch_notice(model, audio_path, language)):
        on_notice(notice)
    kwargs = {
        # Silero scores shouted or slurred speech below any usable threshold; fast mode's
        # batched pipeline needs its chunks.
        "language": language, "vad_filter": not (difficult_audio and not fast_mode), "beam_size": beam_size,
        "vad_parameters": {"min_silence_duration_ms": min_silence_duration_ms,
                            "threshold": vad_threshold},
        "word_timestamps": whisper_models.supports_word_timestamps(model_size),
        **decode_kwargs(sensitivity_preset, WHISPER_ANTI_LOOP_KWARGS, WHISPER_REPEAT_GUARD_KWARGS, repeat_guard),
    }
    if speech_pad_ms is not None:
        kwargs["vad_parameters"]["speech_pad_ms"] = int(speech_pad_ms)
    prompt_keys = _add_prompt(model, kwargs, initial_prompt, language, chinese_script, prompt_every_window)
    if hallucination_silence_sec and not fast_mode and _accepts_transcribe_kwarg(
            model, "hallucination_silence_threshold"):
        kwargs["hallucination_silence_threshold"] = float(hallucination_silence_sec)
    if difficult_audio and not fast_mode:
        kwargs.update(_relaxed_kwargs(model, no_speech_threshold, log_prob_threshold))

    resume = _Resume(None if fast_mode else resume_segments, on_unit)

    def _collect(m, segments, info):
        duration = (getattr(info, "duration", None) or 0) + resume.at
        resume.restart()
        result = _segment_dicts(segments, prompt_keys, progress_cb and (
            lambda s: progress_cb(min((s.end + resume.at) / duration, 1.0) if duration else 0.0)),
            on_kept=resume.keep if resume.on_unit and not fast_mode else None)
        result = resume.merged(result)
        settle_overlaps(result)
        if not fast_mode:
            result = _rehear_sparse(m, audio_path, kwargs, result, prompt_keys)
        if filter_hallucination_repeats:
            # A default run calls the filter exactly as it always has.
            extra = ({} if stock_isolation_sec == STOCK_PHRASE_ISOLATION_SEC
                     else {"stock_isolation_sec": stock_isolation_sec})
            result = filter_hallucinated_segments(result, filter_hallucination_repeats, **extra)
        return result

    tail = _cut_tail(audio_path, resume)
    if resume.at and tail is None:
        return _collect(model, [], None)   # the earlier run had decoded to the end

    def _run(m):
        if fast_mode:
            from faster_whisper import BatchedInferencePipeline
            return BatchedInferencePipeline(model=m).transcribe(audio_path, **kwargs)
        return m.transcribe(tail or audio_path, **kwargs)

    try:
        try:
            segments, _info = _decoded(_run, model)
            return _collect(model, segments, _info)
        except Exception as exc:
            # ctranslate2 defers CUDA init until this exact point -- a broken
            # or missing CUDA install (mismatched toolkit version, a missing
            # cublas64_12.dll, no CUDA-capable device) can ONLY be caught
            # here, never at model construction, no matter how that's wrapped.
            # See load_whisper_model's own GPU->CPU fallback, which protects
            # a different (earlier, rarer) failure point and cannot catch this.
            if not (use_gpu and whisper_models.is_gpu_error(exc)):
                raise
            if on_gpu_fallback:
                on_gpu_fallback(exc)
        model = segments = None    # their last references, so the GPU copy can be freed
        return _retry_on_cpu(model_size, local_model_path, hf_token, _run, _collect)
    finally:
        if tail:
            os.unlink(tail)


def _retry_on_cpu(model_size, local_model_path, hf_token, run, collect):
    """transcribe_for_timing's CPU retry. Called after its except block: inside
    it the traceback holds the decode's frames, and through them the GPU model
    and its VRAM, for the whole (much slower) CPU run."""
    whisper_models.forget_gpu_model(model_size, local_model_path)
    cpu_model = whisper_models.load_whisper_model(model_size, use_gpu=False,
                                                  local_model_path=local_model_path,
                                                  hf_token=hf_token)
    segments, info = run(cpu_model)
    return collect(cpu_model, segments, info)


UNREADABLE_AUDIO = ("Could not read audio from this file. It may be damaged, or not an audio "
                    "or video file.")


class UnreadableAudioError(ValueError):
    """The file could not be decoded. PyAV's own text ("[Errno 1094995529] Invalid
    data found when processing input") is what the job would otherwise show."""


def _decoded(run, model):
    """run(model), with a file faster-whisper cannot decode (it decodes the whole
    file with PyAV before transcribing) turned into UnreadableAudioError."""
    try:
        return run(model)
    except Exception as exc:
        # av.error's OSError kinds (a missing or locked file) are not the file's content.
        if type(exc).__module__.split(".")[0] == "av" and not isinstance(exc, OSError):
            raise UnreadableAudioError(UNREADABLE_AUDIO) from None
        raise


GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_DEFAULT_MODEL = "whisper-large-v3-turbo"
# verbose_json segments for an hour of speech are a few MB; Groq's own upload
# limit keeps a single file far below what would fill this.
GROQ_RESPONSE_MAX_BYTES = 32 * 1024 * 1024
GROQ_ERROR_MAX_BYTES = 64 * 1024


class GroqTranscriptionError(RuntimeError):
    """Raised when Groq's cloud transcription API can't be reached or
    returns an error, so callers can show a useful message instead of a
    raw requests/HTTP exception."""


# Far past any file Groq accepts (25 MB of audio); a later time is garbage.
_GROQ_MAX_SECONDS = 7 * 24 * 3600.0


def _groq_segments(segments) -> list:
    """Groq's segments as [{"start", "end", "text"}], blank ones left out. A
    reply of another shape is a GroqTranscriptionError, not a KeyError deep in
    the job. A segment timed NaN, Infinity (json.loads accepts both), below
    zero, backwards or absurdly late is dropped: those times would crash or
    garble every subtitle export later."""
    shape = "Groq's reply didn't have the expected segments."
    if not isinstance(segments, list):
        raise GroqTranscriptionError(shape)
    result = []
    for seg in segments:
        if not isinstance(seg, dict) or not isinstance(seg.get("text"), str):
            raise GroqTranscriptionError(shape)
        raw = (seg.get("start"), seg.get("end"))
        if not all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in raw):
            raise GroqTranscriptionError(shape)
        try:
            start, end = float(raw[0]), float(raw[1])
        except OverflowError:   # an integer too large for a float
            raise GroqTranscriptionError(shape) from None
        text = seg["text"].strip()
        if text and 0 <= start <= end <= _GROQ_MAX_SECONDS:
            result.append({"start": start, "end": end, "text": text})
    return result


def transcribe_with_groq(audio_path: str, language: str, api_key: str,
                         model: str = GROQ_DEFAULT_MODEL, progress_cb=None):
    """
    An opt-in, paid cloud alternative to transcribe_for_timing's
    local faster-whisper path -- sends the whole file to Groq's hosted
    Whisper Large-v3-Turbo API (the same model family this app defaults
    to locally, at ~$0.04/hour of audio) and returns the identical
    [{"start", "end", "text"}, ...] segment shape, so the result flows
    into the exact same downstream pipeline (alignment, diarization
    hand-off) as a local Whisper result -- callers don't need to know
    which one produced it.

    One blocking HTTP call, not a stream -- progress_cb (if given) is
    only ever called once, with 1.0, right before returning; there's no
    partial-progress signal available during the request itself.
    """
    import os as _os
    from engine_backends import shared
    from lib import http

    too_big = "Groq's reply was too large or too slow to read."
    unreachable = "Couldn't reach Groq's API."
    not_json = "Groq's reply wasn't valid JSON."
    try:
        with open(audio_path, "rb") as f:
            resp = http.post(
                GROQ_TRANSCRIBE_URL, timeout=600, max_bytes=GROQ_RESPONSE_MAX_BYTES,
                max_error_bytes=GROQ_ERROR_MAX_BYTES,
                guard=None, headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (_os.path.basename(audio_path), f)},
                data={"model": model, "language": language,
                      "response_format": "verbose_json", "timestamp_granularities[]": "segment"})
    except (http.ResponseTooLarge, http.ResponseTooSlow):
        raise GroqTranscriptionError(too_big) from None
    except http.FetchError:
        # lib.http's own text says "the page", which misleads for an API call.
        raise GroqTranscriptionError(unreachable) from None
    if resp.status != 200:
        raise GroqTranscriptionError(shared.redact_secrets(
            f"Groq API returned {resp.status}: "
            f"{resp.body.decode('utf-8', 'replace')[:300]}"))
    try:
        data = json.loads(resp.body)
    except ValueError:
        raise GroqTranscriptionError(not_json) from None
    if not isinstance(data, dict):
        raise GroqTranscriptionError(not_json)
    result = _groq_segments(data.get("segments", []))
    if progress_cb:
        progress_cb(1.0)
    # The local path filters its output the same way (transcribe_for_timing).
    return filter_hallucinated_segments(result)
