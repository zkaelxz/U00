"""anime_whisper.py -- the opt-in Japanese-only anime-whisper transcription backend.

Personal, non-commercial use only. Registered in asr_backend.BACKENDS as "anime_whisper";
asr_backend's constants and audio loader are used through the module at call time, because
asr_backend imports this module. What the backend is and why it has its own decode path:
AnimeWhisperBackend's docstring and "anime-whisper in the app" in docs/asr-experiments.md.
"""


# anime-whisper backend: lines are the speech spans of a finer detector than the one that
# builds the decoding windows, so two utterances inside one 30 s window stay two lines.
ANIME_WHISPER_LANGUAGE = "ja"
ANIME_FINE_VAD_KWARGS = {"threshold": 0.35, "min_speech_ms": 0, "pad_ms": 100}
ANIME_FINE_MERGE_GAP_S = 0.6
# Whisper's encoder takes 30 s of 100 frame/s features.
_ENCODER_FRAMES = 3000


def place_words_in_spans(words, spans) -> list:
    """Lines from aligned words: each word goes into the span that holds its midpoint
    (the nearest span when it falls in a gap), and a line is that span narrowed to its
    first and last word. When the words lie outside the span (the gap case) or have no
    duration, the narrowed span would be the wrong time, so a line takes its words' own
    first start and last end, or keeps the span flagged timing_uncertain when no word
    has a duration. words: {"word", "start", "end"} dicts in time order. Returns
    {"start", "end", "text", "words"} dicts; a line's text is its words joined, so the
    stored words always spell it exactly."""
    from types import SimpleNamespace
    from segment_splitting import tighten_to_words
    spans = list(spans)
    if not spans:
        return []

    def span_index(word):
        mid = (word["start"] + word["end"]) / 2
        for n, (start, end) in enumerate(spans):
            if start <= mid <= end:
                return n
        return min(range(len(spans)),
                   key=lambda n: min(abs(mid - spans[n][0]), abs(mid - spans[n][1])))

    grouped = {}
    for word in words:
        grouped.setdefault(span_index(word), []).append(word)
    lines = []
    for n in sorted(grouped):
        group = grouped[n]
        text = "".join(w["word"] for w in group).strip()
        if not text:
            continue
        line = {"text": text, "words": group}
        spoken = [w for w in group if w["end"] > w["start"]]
        if spoken:
            own_start, own_end = min(w["start"] for w in spoken), max(w["end"] for w in spoken)
            start, end = tighten_to_words(
                spans[n][0], spans[n][1],
                [SimpleNamespace(start=w["start"], end=w["end"]) for w in spoken])
            if not (start < own_end and own_start < end):
                start, end = own_start, own_end
        else:
            start, end = spans[n]
            line.update(flag="timing_uncertain",
                        flag_note="anime-whisper gave these words no duration; the line covers the speech span.")
        lines.append({"start": start, "end": end, **line})
    return lines


class AnimeWhisperBackend:
    """litagin/anime-whisper, Japanese only, opt-in (asr_backend_choice "anime_whisper").
    Personal, non-commercial use only: the training data forbids commercial use of models
    trained on it, whatever the model card's licence says. The weights are never shipped;
    whisper_models downloads one pinned CTranslate2 build on first use.

    WHY IT HAS ITS OWN PATH instead of core.transcribe_for_timing: the model writes one
    leading timestamp and never a closing one, so faster-whisper's segment times are
    unusable, and word_timestamps=True inside transcribe() makes it seek back and
    re-decode, inventing text. So: (1) decode whole windows of at most 30 s with
    timestamps off, (2) force-align each window's tokens with the model's own
    cross-attention (CTranslate2 align, heads calibrated in whisper_models), (3) put each
    word into the fine Silero speech span that holds it. Evidence: docs/asr-experiments.md.

    No initial prompt is ever sent, not even the title's glossary: the model card says it
    makes the model hallucinate."""
    name = "anime_whisper"
    task_label = "Anime-whisper"

    def transcribe(self, audio_path, language, use_gpu=False, progress_cb=None,
                   cancel_check=None, vad_fn=None, stage_cb=None, on_device=None,
                   on_gpu_fallback=None, detector="standard", on_notice=None,
                   repeat_guard=False, **_unused):
        """Segments as {"start", "end", "text", "words"}. Batch size, mixed languages,
        timing refinement and the prompt belong to the other backends and are ignored.
        repeat_guard adds core.WHISPER_REPEAT_GUARD_KWARGS to the decode, only when the
        title turned it on. progress_cb(fraction) and cancel_check() are as for
        Qwen3ASRVadBackend.transcribe; on_device(task, "GPU"|"CPU") and
        on_gpu_fallback(task, reason) report where the model loaded."""
        import asr_backend
        import vad_segments
        import whisper_models
        from core import WHISPER_REPEAT_GUARD_KWARGS, filter_hallucinated_segments
        if language != ANIME_WHISPER_LANGUAGE:
            raise ValueError(
                "anime-whisper is only for Japanese titles -- use another transcription "
                "backend for this language.")

        def check_cancel():
            if cancel_check:
                cancel_check()

        if stage_cb:
            stage_cb("Loading audio (CPU)")
        audio = asr_backend.load_audio_16k(audio_path)
        sr = 16000
        if vad_fn is None and detector in ("asmr", "auto_asmr"):
            import asmr_vad
            vad_fn = asmr_vad.vad_fn_or_fallback(on_notice if detector == "asmr" else None)
        if stage_cb:
            stage_cb("Finding speech (CPU)")
        windows = vad_segments.cap_spans(
            vad_segments.merge_close(
                vad_segments.speech_spans(audio, sr, vad_fn=vad_fn, **asr_backend.LONG_VAD_KWARGS),
                gap_s=asr_backend.LONG_MERGE_GAP_S),
            audio, sr, max_s=asr_backend.LONG_WINDOW_S, search_window_s=asr_backend.LONG_CUT_SEARCH_S)
        fine_spans = vad_segments.merge_close(
            vad_segments.speech_spans(audio, sr, vad_fn=vad_fn, **ANIME_FINE_VAD_KWARGS),
            gap_s=ANIME_FINE_MERGE_GAP_S)
        if not windows:
            return []
        check_cancel()
        if stage_cb:
            stage_cb("Loading the anime-whisper model")
        model = whisper_models.load_whisper_model(whisper_models.ANIME_WHISPER_KEY,
                                                  use_gpu=use_gpu)
        info = whisper_models.get_whisper_device_info(whisper_models.ANIME_WHISPER_KEY,
                                                      use_gpu=use_gpu)
        if on_gpu_fallback and info.get("gpu_error"):
            on_gpu_fallback(self.task_label, info["gpu_error"])
        if on_device:
            on_device(self.task_label, "GPU" if info.get("device") == "cuda" else "CPU")

        extra = WHISPER_REPEAT_GUARD_KWARGS if repeat_guard else {}

        def attempt(model):
            texts = self._decode(
                model, audio, windows, extra,
                lambda frac: (check_cancel(), progress_cb and progress_cb(frac * 0.8)))
            found = []
            for n, (window, (text, tokens)) in enumerate(zip(windows, texts)):
                check_cancel()
                if text:
                    found.extend(self._lines_for_window(model, audio, sr, window, text, tokens,
                                                        fine_spans))
                if progress_cb:
                    progress_cb(0.8 + 0.2 * (n + 1) / len(windows))
            return found

        try:
            lines = attempt(model)
        except Exception as exc:
            # CTranslate2 loads CUDA lazily, so a broken GPU setup shows up here, at the
            # first inference, not when the model was built.
            if not (use_gpu and info.get("device") == "cuda" and whisper_models.is_gpu_error(exc)):
                raise
            if on_gpu_fallback:
                on_gpu_fallback(self.task_label, whisper_models.short_reason(exc))
            if on_device:
                on_device(self.task_label, "CPU")
            lines = attempt(whisper_models.load_whisper_model(
                whisper_models.ANIME_WHISPER_KEY, use_gpu=False))
        lines.sort(key=lambda line: line["start"])
        # A line with nothing to read (only "…" or "。") is not a subtitle; kana-only
        # breaths and laughs are letters and stay.
        lines = [line for line in lines if any(ch.isalnum() for ch in line["text"])]
        return filter_hallucinated_segments(lines)

    @staticmethod
    def _decode(model, audio, windows, extra_kwargs, progress):
        """[(text, tokens)] per window, from one transcribe() call whose clip_timestamps
        make faster-whisper decode each window on its own (no text carried over). A segment
        belongs to the last window that starts at or before it."""
        clips = [t for window in windows for t in (window.start_s, window.end_s)]
        segments, _info = model.transcribe(
            audio, language=ANIME_WHISPER_LANGUAGE, beam_size=5, without_timestamps=True,
            word_timestamps=False, condition_on_previous_text=False, vad_filter=False,
            clip_timestamps=clips, **extra_kwargs)
        starts = [window.start_s for window in windows]
        parts = [([], []) for _ in windows]
        for seg in segments:
            n = max((i for i, start in enumerate(starts) if start <= seg.start + 0.05), default=0)
            parts[n][0].append(seg.text)
            parts[n][1].extend(seg.tokens or [])
            progress((n + 1) / len(windows))
        return [("".join(texts).strip(), tokens) for texts, tokens in parts]

    def _lines_for_window(self, model, audio, sr, window, text, tokens, fine_spans):
        try:
            words = self._align(model, audio, sr, window, tokens)
        except Exception as exc:
            from whisper_models import is_gpu_error
            if is_gpu_error(exc):
                raise   # transcribe() reruns the whole job on the CPU
            import applog
            applog.get_logger().warning(
                f"anime-whisper alignment failed, keeping the window as one line: "
                f"{type(exc).__name__}")
            words = []
        if words:
            # Clamped: a fine span can reach into the neighbouring window, whose own words
            # would then produce an overlapping line.
            inside = [(max(span.start_s, window.start_s), min(span.end_s, window.end_s))
                      for span in fine_spans]
            inside = [span for span in inside if span[1] > span[0]]
            lines = place_words_in_spans(words, inside or [(window.start_s, window.end_s)])
            if lines:
                return lines
        return [{"start": window.start_s, "end": window.end_s, "text": text,
                 "flag": "timing_uncertain",
                 "flag_note": "anime-whisper could not time this text; the line covers the whole speech window."}]

    @staticmethod
    def _align(model, audio, sr, window, tokens):
        """Words as {"word", "start", "end"} in audio time, from the model's own
        cross-attention over this window (CTranslate2 align through faster-whisper's
        find_alignment, which also splits Japanese tokens into characters)."""
        from faster_whisper.audio import pad_or_trim
        from faster_whisper.tokenizer import Tokenizer
        tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual,
                              task="transcribe", language=ANIME_WHISPER_LANGUAGE)
        text_tokens = [t for t in tokens if t < tokenizer.eot]
        if not text_tokens:
            return []
        samples = audio[int(window.start_s * sr):int(window.end_s * sr)]
        features = model.feature_extractor(samples)
        # The extractor appends a frame of padding that holds no audio.
        num_frames = min(_ENCODER_FRAMES, max(1, features.shape[-1] - 1))
        encoder_output = model.encode(pad_or_trim(features, _ENCODER_FRAMES))
        aligned = model.find_alignment(tokenizer, [text_tokens], encoder_output, num_frames)
        words = []
        for w in (aligned[0] if aligned else []):
            start = min(window.end_s, window.start_s + float(w["start"]))
            end = min(window.end_s, window.start_s + float(w["end"]))
            words.append({"word": w["word"], "start": start, "end": max(start, end)})
        return words
