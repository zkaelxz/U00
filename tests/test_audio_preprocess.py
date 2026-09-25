"""
tests/test_audio_preprocess.py -- audio_preprocess.py, vocal separation
via Demucs or audio-separator (Mel-Band RoFormer).

Neither backend is installed in this sandbox (no GPU, no network, heavy
model download) -- each is faked at its own Python API boundary, the
same way other tests here fake faster_whisper.WhisperModel or
paddleocr.PaddleOCR: this exercises the real error handling, stem
selection and path plumbing, without needing the actual neural network.

Step 4g added real per-chunk progress and a genuine mid-run cancel by
chunking the input audio *outside* the backend (neither exposes a
callback of its own) and recombining the results with a crossfade. That
chunking always goes through soundfile now (same "prefer soundfile over
torchaudio for a plain WAV" reasoning as Step 4c's diarize.py fix), so
every test that runs a real separation call needs an actual (tiny,
fixture) WAV file on disk rather than a placeholder path string, and
needs soundfile importable -- skipped cleanly via importorskip where a
core-only install wouldn't have it.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import audio_preprocess


def _write_wav(path, seconds, samplerate=8000, freq=220.0):
    """A short, real sine-wave fixture -- small and fast, but real audio
    data soundfile can read back, unlike a bare placeholder path."""
    import numpy as np
    import soundfile as sf
    n = int(seconds * samplerate)
    t = np.linspace(0, seconds, n, endpoint=False, dtype="float32")
    data = (0.1 * np.sin(2 * 3.14159265 * freq * t)).astype("float32")
    sf.write(path, data, samplerate)
    return data, samplerate


def _install_fake_demucs(monkeypatch, stems=("drums", "bass", "other", "vocals"),
                          separate_exc=None, vocals_gain=1.0):
    """FakeSeparator.separate_audio_file reads the chunk file it's given
    via soundfile and returns a "vocals" stem that's the same waveform
    (scaled by vocals_gain) -- a stand-in for the real model, close
    enough to a passthrough to verify the chunking/crossfade machinery
    reassembles a coherent signal."""
    import soundfile as sf
    captured = {"separate_paths": []}

    class FakeSeparator:
        def __init__(self, model="htdemucs"):
            captured["model"] = model
            self.samplerate = 8000

        def separate_audio_file(self, path):
            captured["separate_paths"].append(path)
            if separate_exc:
                raise separate_exc
            data, _ = sf.read(path, dtype="float32", always_2d=True)
            return "origin", {s: (data * vocals_gain if s == "vocals" else data) for s in stems}

    def fake_save_audio(tensor, path, samplerate=None):
        sf.write(path, tensor, samplerate or 8000)

    fake_api = types.ModuleType("demucs.api")
    fake_api.Separator = FakeSeparator
    fake_api.save_audio = fake_save_audio
    fake_demucs = types.ModuleType("demucs")
    fake_demucs.api = fake_api
    monkeypatch.setitem(sys.modules, "demucs", fake_demucs)
    monkeypatch.setitem(sys.modules, "demucs.api", fake_api)
    return captured


def _install_fake_audio_separator(monkeypatch, vocals_only=True, separate_exc=None):
    import soundfile as sf
    captured = {"separate_paths": []}

    class FakeSeparator:
        def __init__(self, output_dir, model_file_dir, output_single_stem):
            captured["output_dir"] = output_dir
            captured["model_file_dir"] = model_file_dir

        def load_model(self, model_filename):
            captured["model"] = model_filename

        def separate(self, path):
            captured["separate_paths"].append(path)
            if separate_exc:
                raise separate_exc
            if not vocals_only:
                return []
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            out_name = f"chunk_{len(captured['separate_paths']):04d}_(Vocals).wav"
            sf.write(os.path.join(captured["output_dir"], out_name), data, sr)
            return [out_name]

    fake_module = types.ModuleType("audio_separator.separator")
    fake_module.Separator = FakeSeparator
    fake_pkg = types.ModuleType("audio_separator")
    fake_pkg.separator = fake_module
    monkeypatch.setitem(sys.modules, "audio_separator", fake_pkg)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", fake_module)
    return captured


class TestSeparateVocalsDemucs:
    def test_writes_the_vocals_stem_to_out_path(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        captured = _install_fake_demucs(monkeypatch)
        in_path, out_path = str(tmp_path / "audio.wav"), str(tmp_path / "vocals.wav")
        _write_wav(in_path, seconds=1.0)

        result = audio_preprocess.separate_vocals_demucs(in_path, out_path)

        assert result == out_path
        assert os.path.exists(out_path)
        # A 1s file at the default 30s chunk size is exactly one chunk.
        assert len(captured["separate_paths"]) == 1

    def test_uses_the_requested_model(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        captured = _install_fake_demucs(monkeypatch)
        in_path = str(tmp_path / "audio.wav")
        _write_wav(in_path, seconds=0.5)
        audio_preprocess.separate_vocals_demucs(in_path, str(tmp_path / "vocals.wav"),
                                                model="htdemucs_ft")
        assert captured["model"] == "htdemucs_ft"

    def test_missing_demucs_raises_a_clear_actionable_error(self, monkeypatch):
        # No fixture file needed -- the ImportError fires before any
        # chunking/audio-reading logic runs.
        monkeypatch.setitem(sys.modules, "demucs", None)
        monkeypatch.setitem(sys.modules, "demucs.api", None)
        with pytest.raises(audio_preprocess.VocalSeparationError, match="pip install demucs"):
            audio_preprocess.separate_vocals_demucs("/fake/audio.wav", "/fake/vocals.wav")

    def test_separation_failure_is_wrapped_not_raised_raw(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        _install_fake_demucs(monkeypatch, separate_exc=RuntimeError("out of memory"))
        in_path = str(tmp_path / "audio.wav")
        _write_wav(in_path, seconds=0.5)
        with pytest.raises(audio_preprocess.VocalSeparationError, match="out of memory"):
            audio_preprocess.separate_vocals_demucs(in_path, str(tmp_path / "vocals.wav"))

    def test_a_model_with_no_vocals_stem_raises_a_clear_error(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        _install_fake_demucs(monkeypatch, stems=("drums", "bass"))
        in_path = str(tmp_path / "audio.wav")
        _write_wav(in_path, seconds=0.5)
        with pytest.raises(audio_preprocess.VocalSeparationError, match="vocals stem"):
            audio_preprocess.separate_vocals_demucs(in_path, str(tmp_path / "vocals.wav"))

    def test_separate_vocals_demucs_constructs_its_separator_exactly_once(self, monkeypatch, tmp_path):
        """A per-chunk model reload would be a real performance
        regression against the old single-call path -- Separator(...)
        must be constructed exactly once regardless of chunk count.
        Forces several real chunks by wrapping _separate_vocals_chunked
        with a small chunk_seconds -- separate_vocals_demucs itself
        doesn't expose that as a public parameter."""
        pytest.importorskip("soundfile")
        import soundfile as sf
        constructions = []

        class CountingSeparator:
            def __init__(self, model="htdemucs"):
                constructions.append(model)
                self.samplerate = 8000

            def separate_audio_file(self, path):
                data, _ = sf.read(path, dtype="float32", always_2d=True)
                return "origin", {"vocals": data}

        fake_api = types.ModuleType("demucs.api")
        fake_api.Separator = CountingSeparator
        fake_api.save_audio = lambda tensor, path, samplerate=None: sf.write(path, tensor, samplerate or 8000)
        fake_demucs = types.ModuleType("demucs")
        fake_demucs.api = fake_api
        monkeypatch.setitem(sys.modules, "demucs", fake_demucs)
        monkeypatch.setitem(sys.modules, "demucs.api", fake_api)

        real_chunked = audio_preprocess._separate_vocals_chunked
        monkeypatch.setattr(
            audio_preprocess, "_separate_vocals_chunked",
            lambda *a, **kw: real_chunked(*a, **{**kw, "chunk_seconds": 0.2, "overlap_seconds": 0.02}))

        in_path = str(tmp_path / "audio.wav")
        _write_wav(in_path, seconds=1.0, samplerate=8000)
        audio_preprocess.separate_vocals_demucs(in_path, str(tmp_path / "vocals.wav"))
        assert len(constructions) == 1  # several chunks, one Separator


class TestSeparateVocalsAudioSeparator:
    def test_writes_the_vocals_stem_to_out_path(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        captured = _install_fake_audio_separator(monkeypatch)
        in_path, out_path = str(tmp_path / "audio.wav"), str(tmp_path / "vocals.wav")
        _write_wav(in_path, seconds=0.5, samplerate=8000)

        result = audio_preprocess.separate_vocals_audio_separator(in_path, out_path)

        assert result == out_path
        assert os.path.exists(out_path)
        assert len(captured["separate_paths"]) == 1

    def test_missing_audio_separator_raises_a_clear_actionable_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "audio_separator", None)
        monkeypatch.setitem(sys.modules, "audio_separator.separator", None)
        with pytest.raises(audio_preprocess.VocalSeparationError, match="pip install audio-separator"):
            audio_preprocess.separate_vocals_audio_separator("/fake/audio.wav", "/fake/vocals.wav")

    def test_separation_failure_is_wrapped_not_raised_raw(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        _install_fake_audio_separator(monkeypatch, separate_exc=RuntimeError("gpu oom"))
        in_path = str(tmp_path / "audio.wav")
        _write_wav(in_path, seconds=0.3, samplerate=8000)
        with pytest.raises(audio_preprocess.VocalSeparationError, match="gpu oom"):
            audio_preprocess.separate_vocals_audio_separator(in_path, str(tmp_path / "vocals.wav"))

    def test_no_vocals_output_raises_a_clear_error(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        _install_fake_audio_separator(monkeypatch, vocals_only=False)
        in_path = str(tmp_path / "audio.wav")
        _write_wav(in_path, seconds=0.3, samplerate=8000)
        with pytest.raises(audio_preprocess.VocalSeparationError, match="wrote no vocals stem"):
            audio_preprocess.separate_vocals_audio_separator(in_path, str(tmp_path / "vocals.wav"))


class TestSeparateVocalsChunked:
    """Step 4g: the actual new machinery -- chunking the input outside
    whichever backend is used, reporting progress per chunk, checking
    for a cancel before each chunk starts, and recombining with a
    crossfade. Tested here directly against _separate_vocals_chunked
    with a tiny chunk_seconds, so a short fixture exercises real
    multi-chunk behavior without needing a large, slow fixture (the
    public separate_vocals_demucs/separate_vocals_audio_separator
    wrappers don't expose chunk_seconds -- it's an internal tuning knob,
    not a user-facing setting the roadmap asked for)."""

    @staticmethod
    def _identity_process_chunk(chunk_path, chunk_out_path):
        import soundfile as sf
        data, sr = sf.read(chunk_path, dtype="float32", always_2d=True)
        sf.write(chunk_out_path, data, sr)

    def test_a_short_file_within_one_chunk_round_trips_unchanged(self, tmp_path):
        pytest.importorskip("soundfile")
        import soundfile as sf
        in_path, out_path = str(tmp_path / "in.wav"), str(tmp_path / "out.wav")
        data, sr = _write_wav(in_path, seconds=0.5, samplerate=8000)
        seen = []
        audio_preprocess._separate_vocals_chunked(
            in_path, out_path, self._identity_process_chunk,
            chunk_seconds=1.0, overlap_seconds=0.1, progress_cb=lambda f: seen.append(f))
        assert seen == [1.0]
        out_data, out_sr = sf.read(out_path, dtype="float32", always_2d=True)
        assert out_sr == sr
        assert out_data.shape[0] == data.shape[0]

    def test_a_longer_file_is_split_into_multiple_chunks_with_increasing_progress(self, tmp_path):
        pytest.importorskip("soundfile")
        import soundfile as sf
        in_path, out_path = str(tmp_path / "in.wav"), str(tmp_path / "out.wav")
        data, sr = _write_wav(in_path, seconds=1.0, samplerate=8000)
        seen = []
        audio_preprocess._separate_vocals_chunked(
            in_path, out_path, self._identity_process_chunk,
            chunk_seconds=0.3, overlap_seconds=0.05, progress_cb=lambda f: seen.append(f))
        assert len(seen) > 1
        assert seen == sorted(seen)          # strictly increasing
        assert len(seen) == len(set(seen))   # no repeats
        assert seen[-1] == 1.0
        out_data, out_sr = sf.read(out_path, dtype="float32", always_2d=True)
        # Crossfade blending shouldn't meaningfully change the overall
        # length -- close to the original, not wildly off.
        assert abs(out_data.shape[0] - data.shape[0]) < sr * 0.1

    def test_chunked_output_is_equivalent_to_a_single_whole_file_call(self, tmp_path):
        """Exit condition: the chunked path must produce output
        equivalent to running the same (fake) backend once over the
        whole file -- checked here as a waveform-length/RMS sanity
        check, since there's no real model in this sandbox to compare
        actual separation quality against."""
        pytest.importorskip("soundfile")
        import numpy as np
        import soundfile as sf
        in_path = str(tmp_path / "in.wav")
        _write_wav(in_path, seconds=1.0, samplerate=8000)

        single_out = str(tmp_path / "single.wav")
        self._identity_process_chunk(in_path, single_out)
        single_data, sr = sf.read(single_out, dtype="float32", always_2d=True)

        chunked_out = str(tmp_path / "chunked.wav")
        audio_preprocess._separate_vocals_chunked(
            in_path, chunked_out, self._identity_process_chunk,
            chunk_seconds=0.25, overlap_seconds=0.05)
        chunked_data, _ = sf.read(chunked_out, dtype="float32", always_2d=True)

        assert abs(len(chunked_data) - len(single_data)) < sr * 0.1
        assert float(np.sqrt(np.mean(chunked_data ** 2))) == pytest.approx(
            float(np.sqrt(np.mean(single_data ** 2))), rel=0.15)

    def test_cancel_check_stops_before_the_next_chunk_starts(self, tmp_path):
        pytest.importorskip("soundfile")
        in_path, out_path = str(tmp_path / "in.wav"), str(tmp_path / "out.wav")
        _write_wav(in_path, seconds=1.0, samplerate=8000)
        calls = []

        def _process_chunk(chunk_path, chunk_out_path):
            calls.append(chunk_path)
            self._identity_process_chunk(chunk_path, chunk_out_path)

        seen_checks = {"n": 0}

        def _cancel_check():
            seen_checks["n"] += 1
            return seen_checks["n"] > 2  # cancel right before the 3rd chunk

        with pytest.raises(audio_preprocess.VocalSeparationCancelled):
            audio_preprocess._separate_vocals_chunked(
                in_path, out_path, _process_chunk,
                chunk_seconds=0.2, overlap_seconds=0.02, cancel_check_cb=_cancel_check)
        assert len(calls) == 2  # never started a 3rd chunk
        assert not os.path.exists(out_path)  # nothing partial written to the real out_path

    def test_a_real_separation_failure_still_propagates_as_is(self, tmp_path):
        pytest.importorskip("soundfile")
        in_path, out_path = str(tmp_path / "in.wav"), str(tmp_path / "out.wav")
        _write_wav(in_path, seconds=0.3, samplerate=8000)

        def _boom(chunk_path, chunk_out_path):
            raise audio_preprocess.VocalSeparationError("model exploded")

        with pytest.raises(audio_preprocess.VocalSeparationError, match="model exploded"):
            audio_preprocess._separate_vocals_chunked(in_path, out_path, _boom, chunk_seconds=1.0)


class TestSeparateVocalsCancelNeverFallsBackToTheOtherBackend:
    """A cancel is a deliberate stop, not a failure -- separate_vocals()'s
    own "auto" retry-the-other-backend logic must never treat
    VocalSeparationCancelled as something worth retrying."""

    def test_a_cancel_from_the_first_backend_is_not_retried_on_the_second(self, monkeypatch):
        calls = []

        def _cancelling_backend(audio_path, out_path, progress_cb=None, cancel_check_cb=None):
            calls.append("audio_separator")
            raise audio_preprocess.VocalSeparationCancelled("stopped")

        def _should_never_run(audio_path, out_path, progress_cb=None, cancel_check_cb=None):
            calls.append("demucs")
            raise audio_preprocess.VocalSeparationError("should never be reached")

        monkeypatch.setitem(audio_preprocess._BACKENDS, "audio_separator", _cancelling_backend)
        monkeypatch.setitem(audio_preprocess._BACKENDS, "demucs", _should_never_run)

        with pytest.raises(audio_preprocess.VocalSeparationCancelled):
            audio_preprocess.separate_vocals("/fake/audio.wav", "/fake/vocals.wav", backend="auto")
        assert calls == ["audio_separator"]


class TestModelDirRespectsPortableModeOverride:
    """Step 10: BAIHE_AUDIO_SEP_MODEL_DIR (set by portable.py's
    activate_portable_mode() under portable mode) overrides the default
    ~/.cache/audio-separator-models -- _MODEL_DIR is a module-level
    constant, so this reimports the module with the env var already set,
    the same as it would be by the time a real process reaches this
    import."""

    def test_env_var_set_overrides_the_default(self, monkeypatch):
        import importlib
        monkeypatch.setenv("BAIHE_AUDIO_SEP_MODEL_DIR", "/portable/model_cache/audio-separator-models")
        try:
            importlib.reload(audio_preprocess)
            assert audio_preprocess._MODEL_DIR == "/portable/model_cache/audio-separator-models"
        finally:
            monkeypatch.delenv("BAIHE_AUDIO_SEP_MODEL_DIR", raising=False)
            importlib.reload(audio_preprocess)

    def test_no_env_var_falls_back_to_the_home_cache_dir(self, monkeypatch):
        import importlib
        monkeypatch.delenv("BAIHE_AUDIO_SEP_MODEL_DIR", raising=False)
        importlib.reload(audio_preprocess)
        assert audio_preprocess._MODEL_DIR == os.path.join(
            os.path.expanduser("~"), ".cache", "audio-separator-models")
