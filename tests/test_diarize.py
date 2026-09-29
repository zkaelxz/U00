"""
tests/test_diarize.py -- diarize.py.

Regression coverage for a real reported crash: `Pipeline.from_pretrained()
got an unexpected keyword argument 'use_auth_token'`. Newer pyannote.audio
(3.1+) renamed that kwarg to `token`, following huggingface_hub's own
rename, and rejects the old name outright with a TypeError rather than
just deprecation-warning on it. diarize() now tries `token=` first and
falls back to `use_auth_token=` for older installs that don't know the
new name either.
"""
import sys
import os
import queue

import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diarize


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    """_stub_pyannote() below replaces sys.modules entries directly
    (a plain function, not a fixture, so it can be called with no
    monkeypatch in scope) -- without this, a stub it installs (soundfile
    in particular, now that Step 4g's vocal-separation chunking also
    needs the real module elsewhere in the same test process) leaks into
    every test that runs after it, in this file or any other."""
    originals = {name: sys.modules.get(name) for name in ("soundfile", "pyannote", "pyannote.audio")}
    yield
    for name, mod in originals.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


class _FakeDiarizationResult:
    def __init__(self, turns):
        self._turns = turns

    def itertracks(self, yield_label=True):
        for start, end, speaker in self._turns:
            yield type("Turn", (), {"start": start, "end": end})(), None, speaker

    def labels(self):
        return sorted({speaker for _, _, speaker in self._turns})


class _FakeDiarizeOutput4x:
    """Mimics pyannote.audio 4.x's DiarizeOutput dataclass: pipeline(audio)
    returns this wrapper instead of an Annotation directly, with the
    actual Annotation-like result on .speaker_diarization. Calling
    .itertracks() on THIS object (as 3.x code would, unchanged) raises
    AttributeError, which is exactly the real reported break.

    speaker_embeddings: Step 8's optional per-speaker voice fingerprints,
    one row per label in .speaker_diarization.labels() order -- only
    present on a real pyannote 4.x DiarizeOutput, never on 3.x's plain
    Annotation, which is exactly what extract_speaker_embeddings() checks
    for via getattr."""
    def __init__(self, annotation, speaker_embeddings=None):
        self.speaker_diarization = annotation
        if speaker_embeddings is not None:
            self.speaker_embeddings = speaker_embeddings


def _stub_pyannote(accepted_kwarg, turns, wrap_4x_output=False,
                   fake_frames=None, fake_sample_rate=16000):
    """Builds a fake pyannote.audio module whose Pipeline.from_pretrained
    only accepts one specific auth kwarg name -- raising TypeError for
    the other, the same way a real version-mismatched install would.
    Also stubs soundfile (not installed in this sandbox -- no GPU, no
    network) since diarize() always pre-loads the audio through it now.

    wrap_4x_output: when True, the fake pipeline's call returns a
    _FakeDiarizeOutput4x wrapping the result (pyannote.audio 4.x's
    actual return shape) instead of the Annotation-like result directly
    (3.x's shape).

    fake_frames: the numpy array soundfile.read() should return, shaped
    (frames, channels) the way its own always_2d=True does -- defaults
    to one silent mono frame for tests that don't care about the exact
    waveform."""
    import types
    import numpy as np

    if fake_frames is None:
        fake_frames = np.zeros((1, 1), dtype="float32")

    calls = {"kwarg_used": None, "audio_arg": None}

    class FakePipeline:
        @staticmethod
        def from_pretrained(model_name, **kwargs):
            if accepted_kwarg not in kwargs:
                bad = next(iter(kwargs))
                raise TypeError(
                    f"Pipeline.from_pretrained() got an unexpected keyword argument '{bad}'")
            calls["kwarg_used"] = accepted_kwarg

            def _call(audio, num_speakers=None):
                calls["audio_arg"] = audio
                result = _FakeDiarizationResult(turns)
                return _FakeDiarizeOutput4x(result) if wrap_4x_output else result
            return _call

    fake_module = types.ModuleType("pyannote.audio")
    fake_module.Pipeline = FakePipeline
    sys.modules["pyannote.audio"] = fake_module
    # Also register the parent package so `from pyannote.audio import Pipeline` resolves.
    parent = types.ModuleType("pyannote")
    parent.audio = fake_module
    sys.modules["pyannote"] = parent

    fake_soundfile = types.ModuleType("soundfile")
    fake_soundfile.read = lambda path, dtype="float32", always_2d=True: (fake_frames, fake_sample_rate)
    sys.modules["soundfile"] = fake_soundfile

    return calls


class TestDiarizeTokenKwargCompatibility:
    def test_uses_new_token_kwarg_when_accepted(self):
        pytest.importorskip("torch")
        calls = _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert calls["kwarg_used"] == "token"
        assert result == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    def test_falls_back_to_use_auth_token_for_older_installs(self):
        """The exact reported bug: an install where only the OLD kwarg
        name works. This must not raise -- it should fall back and
        actually complete the diarization run."""
        pytest.importorskip("torch")
        calls = _stub_pyannote("use_auth_token", turns=[(0.0, 2.0, "SPEAKER_00"),
                                                          (2.0, 4.0, "SPEAKER_01")])
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert calls["kwarg_used"] == "use_auth_token"
        assert len(result) == 2
        assert result[1]["speaker"] == "SPEAKER_01"

    def test_num_speakers_passed_through(self):
        pytest.importorskip("torch")
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        # Just confirming this doesn't raise with num_speakers set --
        # the fake pipeline accepts and ignores it, same as the real one
        # would use it as a hint.
        result = diarize.diarize("/fake/audio.wav", "hf_xxx", num_speakers=2)
        assert result == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]


class TestDiarizePyannote4CompatibleOutputShape:
    """Regression coverage for a real breaking change: pyannote.audio 4.x's
    pipeline(audio) call returns a DiarizeOutput dataclass instead of an
    Annotation, so the old code's diarization.itertracks(...) broke with
    an AttributeError (DiarizeOutput has no itertracks). diarize() now
    unwraps .speaker_diarization when present, falling through to the
    result itself when it isn't (3.x's shape)."""

    def test_handles_the_4x_diarizeoutput_wrapper_shape(self):
        pytest.importorskip("torch")
        calls = _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00"),
                                                (1.0, 2.0, "SPEAKER_01")],
                               wrap_4x_output=True)
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert calls["kwarg_used"] == "token"
        assert result == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
                           {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"}]

    def test_still_handles_the_3x_plain_annotation_shape(self):
        """Same call, unwrapped result -- must keep working unchanged."""
        pytest.importorskip("torch")
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")], wrap_4x_output=False)
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert result == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]


class TestDiarizePreloadsAudioWithSoundfile:
    """Step 4b passed a bare audio-path string to pipeline(), which makes
    pyannote.audio 4.x decode it through torchcodec (never an installed
    dependency) -- fixed there by pre-loading with torchaudio.load().
    Step 4c replaces THAT with soundfile.read(): torchaudio's own
    audio-loading path is being phased out upstream, so pinning it to an
    older version is a losing long-term position, and pinning it forever
    is not sustainable either. Every audio_path reaching diarize() is
    always this app's own normalized audio.wav (see
    core.extract_audio_from_video/extract_audio_slice, both plain 16kHz
    mono PCM WAV), which soundfile (libsndfile-based, no compiled-per-
    FFmpeg-version binary of its own, much less fragile than torchcodec
    on Windows) reads directly. word_align.py's own separate, legitimate
    torchaudio use (Meta's MMS forced-alignment model) is untouched."""

    def test_pipeline_receives_a_waveform_tensor_built_from_soundfile_and_the_right_sample_rate(self):
        torch = pytest.importorskip("torch")
        import numpy as np
        frames = np.array([[0.1], [0.2], [0.3]], dtype="float32")  # 3 frames, 1 channel
        calls = _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")],
                               fake_frames=frames, fake_sample_rate=16000)
        diarize.diarize("/fake/audio.wav", "hf_xxx")
        audio_arg = calls["audio_arg"]
        assert audio_arg["sample_rate"] == 16000
        assert isinstance(audio_arg["waveform"], torch.Tensor)
        assert audio_arg["waveform"].shape == (1, 3)  # (channels, frames)
        assert torch.equal(audio_arg["waveform"], torch.from_numpy(frames.T))

    def test_soundfile_read_is_called_with_the_given_audio_path_and_always_2d(self, monkeypatch):
        pytest.importorskip("torch")
        import types
        import numpy as np
        load_calls = []

        def fake_read(path, dtype="float32", always_2d=True):
            load_calls.append((path, dtype, always_2d))
            return np.zeros((1, 1), dtype="float32"), 16000
        fake_soundfile = types.ModuleType("soundfile")
        fake_soundfile.read = fake_read

        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        monkeypatch.setitem(sys.modules, "soundfile", fake_soundfile)  # overwrite _stub_pyannote's own fake
        diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert load_calls == [("/fake/audio.wav", "float32", True)]


class TestDiarizeSubprocessWorker:
    """Step 4d: diarize_subprocess_worker() is the entry point
    background_jobs.start_process_job() runs in its own OS process, so a
    real mid-run Cancel can terminate it (pyannote's pipeline call has no
    cooperative-cancellation checkpoint of its own). Tested here as a
    plain function call against the same fixture pipeline used
    elsewhere in this file -- background_jobs.py's own tests cover the
    actual multiprocessing.Process/cancel machinery -- confirming it
    produces exactly what a direct diarize() call would, and reports an
    exception instead of raising into the (real, separate) process."""

    def test_matches_a_direct_diarize_call_on_success(self):
        pytest.importorskip("torch")
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")])
        direct_segments, direct_model, direct_embeddings = diarize.diarize(
            "/fake/audio.wav", "hf_xxx", num_speakers=2, return_model=True, return_embeddings=True)

        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")])
        result_queue = queue.Queue()
        diarize.diarize_subprocess_worker("/fake/audio.wav", "hf_xxx", 2, result_queue)
        outcome = result_queue.get_nowait()

        assert outcome == ("ok", {"segments": direct_segments, "model": direct_model,
                                  "embeddings": direct_embeddings, "device": "cpu"})

    def test_reports_an_exception_instead_of_raising(self):
        pytest.importorskip("torch")

        def _boom(model_name, **kwargs):
            raise RuntimeError("pipeline exploded")

        import types
        fake_module = types.ModuleType("pyannote.audio")
        fake_module.Pipeline = types.SimpleNamespace(from_pretrained=_boom)
        sys.modules["pyannote.audio"] = fake_module
        parent = types.ModuleType("pyannote")
        parent.audio = fake_module
        sys.modules["pyannote"] = parent

        result_queue = queue.Queue()
        diarize.diarize_subprocess_worker("/fake/audio.wav", "hf_xxx", None, result_queue)
        outcome = result_queue.get_nowait()
        assert outcome == ("error", "RuntimeError", "pipeline exploded")


class TestAssignSpeakerToLine:
    def test_picks_speaker_with_most_overlap(self):
        segments = [{"start": 0.0, "end": 5.0, "speaker": "SPEAKER_00"},
                    {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_01"}]
        assert diarize.assign_speaker_to_line(4.0, 6.0, segments) in ("SPEAKER_00", "SPEAKER_01")
        assert diarize.assign_speaker_to_line(0.5, 4.5, segments) == "SPEAKER_00"
        assert diarize.assign_speaker_to_line(6.0, 9.0, segments) == "SPEAKER_01"

    def test_no_overlap_returns_none(self):
        segments = [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
        assert diarize.assign_speaker_to_line(5.0, 6.0, segments) is None

    def test_empty_segments_returns_none(self):
        assert diarize.assign_speaker_to_line(0.0, 1.0, []) is None


class TestExtractSpeakerEmbeddings:
    """Step 8: pyannote.audio 4.x's DiarizeOutput.speaker_embeddings, one
    row per speaker in annotation.labels() order -- mapped to
    {speaker_label: [float, ...]}. Not present at all on 3.x's plain
    Annotation, which extract_speaker_embeddings() must handle as "no
    embeddings" rather than raising."""

    def test_maps_embeddings_to_speaker_labels_in_label_order(self):
        annotation = _FakeDiarizationResult(
            [(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")])
        result = _FakeDiarizeOutput4x(annotation, speaker_embeddings=[[1.0, 0.0], [0.0, 1.0]])
        out = diarize.extract_speaker_embeddings(result, annotation)
        assert out == {"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]}

    def test_pyannote_3x_result_has_no_embeddings_attribute(self):
        annotation = _FakeDiarizationResult([(0.0, 1.0, "SPEAKER_00")])
        # A plain 3.x Annotation-like result -- itself, no wrapper, no
        # .speaker_embeddings at all.
        assert diarize.extract_speaker_embeddings(annotation, annotation) == {}

    def test_a_4x_output_with_no_embeddings_set_is_also_empty(self):
        annotation = _FakeDiarizationResult([(0.0, 1.0, "SPEAKER_00")])
        result = _FakeDiarizeOutput4x(annotation)  # no speaker_embeddings kwarg
        assert diarize.extract_speaker_embeddings(result, annotation) == {}

    def test_malformed_embeddings_degrade_to_empty_rather_than_raising(self):
        annotation = _FakeDiarizationResult(
            [(0.0, 1.0, "SPEAKER_00"), (1.0, 2.0, "SPEAKER_01")])
        # Only one row for two speakers -- an IndexError waiting to happen.
        result = _FakeDiarizeOutput4x(annotation, speaker_embeddings=[[1.0, 0.0]])
        assert diarize.extract_speaker_embeddings(result, annotation) == {}


class TestDiarizeReturnEmbeddings:
    def test_return_embeddings_false_keeps_the_exact_existing_shapes(self):
        pytest.importorskip("torch")
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        assert diarize.diarize("/fake/audio.wav", "hf_xxx") == \
            [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        segments, model = diarize.diarize("/fake/audio.wav", "hf_xxx", return_model=True)
        assert model and segments == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    def test_return_embeddings_true_with_a_3x_style_result_gives_an_empty_dict(self):
        pytest.importorskip("torch")
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")], wrap_4x_output=False)
        segments, embeddings = diarize.diarize("/fake/audio.wav", "hf_xxx", return_embeddings=True)
        assert segments == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
        assert embeddings == {}

    def test_return_model_and_embeddings_together(self):
        pytest.importorskip("torch")
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")], wrap_4x_output=False)
        segments, model, embeddings = diarize.diarize(
            "/fake/audio.wav", "hf_xxx", return_model=True, return_embeddings=True)
        assert model and segments and embeddings == {}


class TestSaveLoadTurnsWithEmbeddings:
    def test_embeddings_round_trip_through_save_and_load(self, tmp_path):
        d = str(tmp_path)
        diarize.save_turns(d, [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
                           embeddings={"SPEAKER_00": [0.1, 0.2, 0.3]})
        assert diarize.load_embeddings(d) == {"SPEAKER_00": [0.1, 0.2, 0.3]}
        assert diarize.load_turns(d) == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    def test_no_embeddings_given_saves_and_loads_an_empty_dict(self, tmp_path):
        d = str(tmp_path)
        diarize.save_turns(d, [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}])
        assert diarize.load_embeddings(d) == {}

    def test_load_embeddings_before_any_run_is_empty_not_an_error(self, tmp_path):
        assert diarize.load_embeddings(str(tmp_path)) == {}


class TestLoadLastSpeakerCount:
    """Step 4f: "Expected number of speakers" always reset to 0 on load,
    even after a real detection run had used a specific count -- the
    value was already being saved (save_turns's own num_speakers field)
    but never read back. This is the read-back half of that fix."""

    def test_returns_the_num_speakers_used_for_the_last_real_run(self, tmp_path):
        d = str(tmp_path)
        diarize.save_turns(d, [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
                           num_speakers=2)
        assert diarize.load_last_speaker_count(d) == 2

    def test_none_when_the_last_run_used_auto_detect(self, tmp_path):
        d = str(tmp_path)
        diarize.save_turns(d, [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
                           num_speakers=None)
        assert diarize.load_last_speaker_count(d) is None

    def test_none_before_any_run_not_an_error(self, tmp_path):
        assert diarize.load_last_speaker_count(str(tmp_path)) is None
