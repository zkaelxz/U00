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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diarize


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
                   fake_waveform="FAKE_WAVEFORM", fake_sample_rate=16000):
    """Builds a fake pyannote.audio module whose Pipeline.from_pretrained
    only accepts one specific auth kwarg name -- raising TypeError for
    the other, the same way a real version-mismatched install would.
    Also stubs torchaudio (not installed in this sandbox -- no GPU, no
    network) since diarize() always pre-loads the audio through it now.

    wrap_4x_output: when True, the fake pipeline's call returns a
    _FakeDiarizeOutput4x wrapping the result (pyannote.audio 4.x's
    actual return shape) instead of the Annotation-like result directly
    (3.x's shape)."""
    import types

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

    fake_torchaudio = types.ModuleType("torchaudio")
    fake_torchaudio.load = lambda path: (fake_waveform, fake_sample_rate)
    sys.modules["torchaudio"] = fake_torchaudio

    return calls


class TestDiarizeTokenKwargCompatibility:
    def test_uses_new_token_kwarg_when_accepted(self):
        calls = _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert calls["kwarg_used"] == "token"
        assert result == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    def test_falls_back_to_use_auth_token_for_older_installs(self):
        """The exact reported bug: an install where only the OLD kwarg
        name works. This must not raise -- it should fall back and
        actually complete the diarization run."""
        calls = _stub_pyannote("use_auth_token", turns=[(0.0, 2.0, "SPEAKER_00"),
                                                          (2.0, 4.0, "SPEAKER_01")])
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert calls["kwarg_used"] == "use_auth_token"
        assert len(result) == 2
        assert result[1]["speaker"] == "SPEAKER_01"

    def test_num_speakers_passed_through(self):
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
        calls = _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00"),
                                                (1.0, 2.0, "SPEAKER_01")],
                               wrap_4x_output=True)
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert calls["kwarg_used"] == "token"
        assert result == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
                           {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"}]

    def test_still_handles_the_3x_plain_annotation_shape(self):
        """Same call, unwrapped result -- must keep working unchanged."""
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")], wrap_4x_output=False)
        result = diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert result == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]


class TestDiarizePreloadsAudioWithTorchaudio:
    """Step 4b regression coverage for a real reported crash: passing a
    bare audio-path string to pipeline() makes pyannote.audio 4.x decode
    it through torchcodec, which this app never installs. diarize() must
    instead pre-load the audio itself with torchaudio.load() (already a
    pinned dependency, already used the same way in word_align.py) and
    hand pyannote a {"waveform", "sample_rate"} dict."""

    def test_pipeline_receives_a_waveform_dict_not_the_bare_path(self):
        calls = _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")],
                               fake_waveform="FAKE_WAVEFORM", fake_sample_rate=16000)
        diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert calls["audio_arg"] == {"waveform": "FAKE_WAVEFORM", "sample_rate": 16000}

    def test_torchaudio_load_is_called_with_the_given_audio_path(self, monkeypatch):
        import types
        load_calls = []

        def fake_load(path):
            load_calls.append(path)
            return "FAKE_WAVEFORM", 16000
        fake_torchaudio = types.ModuleType("torchaudio")
        fake_torchaudio.load = fake_load
        monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)

        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)  # _stub_pyannote overwrites it
        diarize.diarize("/fake/audio.wav", "hf_xxx")
        assert load_calls == ["/fake/audio.wav"]


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


class TestLabelLinesWithSpeakers:
    def test_mutates_lines_in_place(self):
        from core import Line
        lines = [Line(idx=0, start=0.0, end=1.0, zh="a"),
                 Line(idx=1, start=5.0, end=6.0, zh="b")]
        segments = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
                    {"start": 4.0, "end": 7.0, "speaker": "SPEAKER_01"}]
        diarize.label_lines_with_speakers(lines, segments)
        assert lines[0].speaker == "SPEAKER_00"
        assert lines[1].speaker == "SPEAKER_01"


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
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        assert diarize.diarize("/fake/audio.wav", "hf_xxx") == \
            [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")])
        segments, model = diarize.diarize("/fake/audio.wav", "hf_xxx", return_model=True)
        assert model and segments == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]

    def test_return_embeddings_true_with_a_3x_style_result_gives_an_empty_dict(self):
        _stub_pyannote("token", turns=[(0.0, 1.0, "SPEAKER_00")], wrap_4x_output=False)
        segments, embeddings = diarize.diarize("/fake/audio.wav", "hf_xxx", return_embeddings=True)
        assert segments == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
        assert embeddings == {}

    def test_return_model_and_embeddings_together(self):
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
