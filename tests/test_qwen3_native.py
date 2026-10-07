"""
tests/test_qwen3_native.py -- qwen3_native (Qwen3-ASR and Qwen3-ForcedAligner on
transformers' own classes), the call shapes asr_backend/forced_align rely on,
the optional vocabulary hint, and its saved setting. Everything is faked: no
network, GPU, real models or real transformers/torch.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asr_backend as ab
import forced_align as fa
import qwen3_native
from core import Line
from services import vocabulary_hint_service as vh


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class _NoGrad:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_torch(monkeypatch):
    torch = types.ModuleType("torch")
    torch.bfloat16, torch.float16 = "bfloat16", "float16"
    torch.inference_mode = lambda: _NoGrad()
    torch.cuda = types.SimpleNamespace(is_bf16_supported=lambda: True)
    monkeypatch.setitem(sys.modules, "torch", torch)
    return torch


@pytest.fixture
def fake_audio(monkeypatch):
    """load_audio_16k returns the path, so each request shows which clip it was."""
    monkeypatch.setattr(qwen3_native, "load_audio_16k", lambda path: f"audio:{path}")


class FakeInputs(dict):
    """The processor's BatchFeature: pop() and .to(device, dtype=) as used."""
    def to(self, device, dtype=None):
        self.moved_to = (device, dtype)
        return self


class FakeASRProcessor:
    unused_input_names = ["num_audio_tokens"]

    def __init__(self, parsed):
        self.parsed = parsed
        self.requests = []
        self.decoded = None

    def apply_transcription_request(self, audio, language=None, **kwargs):
        self.requests.append({"audio": audio, "language": language, "kwargs": kwargs})
        return FakeInputs(input_ids=_Ids(2), num_audio_tokens=1)

    def decode(self, generated, return_format):
        self.decoded = (generated, return_format)
        return self.parsed


class _Ids:
    """A tensor-like (batch, length) holder: .shape and slicing by columns."""
    def __init__(self, length, rows=1):
        self.shape = (rows, length)


class FakeGenerated:
    shape = (1, 5)

    def __getitem__(self, key):
        return ("generated", key)


class FakeASRModel:
    device, dtype = "cpu", "bfloat16"

    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return FakeGenerated()


def _asr(parsed):
    processor = FakeASRProcessor(parsed)
    model = FakeASRModel()
    return qwen3_native.NativeQwen3ASR(model, processor), processor, model


# ---------------------------------------------------------------------------
# transformers version messages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version,ok", [
    ("5.15.0", True), ("5.19.0", True), ("6.0.0", True), ("5.14.1", False),
    ("5.13.0", False), ("4.57.6", False), ("5.15.0.dev0", True), ("5.15rc1", True)])
def test_the_minimum_transformers_is_5_15(monkeypatch, version, ok):
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: version)
    assert (qwen3_native.transformers_problem() is None) is ok


def test_a_missing_transformers_has_a_plain_message(monkeypatch):
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: None)
    assert qwen3_native.transformers_problem() == (
        "Qwen3-ASR needs transformers 5.15 or newer, which isn't installed; "
        "install it in Diagnostics.")


def test_a_too_old_transformers_says_to_update_in_diagnostics(monkeypatch):
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "4.57.6")
    message = qwen3_native.transformers_problem("Qwen3 forced alignment")
    assert message == ("Qwen3 forced alignment needs transformers 5.15 or newer (this is 4.57.6); "
                       "update it in Diagnostics.")
    assert "pip install qwen-asr" not in message


def test_require_raises_an_import_error_subclass_never_a_bare_traceback(monkeypatch):
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "4.0.0")
    with pytest.raises(ImportError) as err:
        qwen3_native.require_transformers()
    assert isinstance(err.value, qwen3_native.TransformersUnavailableError)
    assert str(err.value).endswith("update it in Diagnostics.")


def test_loading_with_an_old_transformers_fails_before_touching_the_model(monkeypatch, fake_torch):
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "4.57.6")
    ab._asr_model_cache.clear()
    with pytest.raises(qwen3_native.TransformersUnavailableError, match="update it in Diagnostics"):
        ab.load_qwen3_asr(use_gpu=False)
    with pytest.raises(qwen3_native.TransformersUnavailableError, match="forced alignment needs"):
        fa.load_qwen3_aligner(use_gpu=False)


def test_the_dtype_is_bfloat16_and_float16_on_a_gpu_without_bfloat16(fake_torch):
    assert qwen3_native.pick_dtype(fake_torch, use_gpu=False) == "bfloat16"
    assert qwen3_native.pick_dtype(fake_torch, use_gpu=True) == "bfloat16"
    fake_torch.cuda = types.SimpleNamespace(is_bf16_supported=lambda: False)
    assert qwen3_native.pick_dtype(fake_torch, use_gpu=True) == "float16"
    assert qwen3_native.pick_dtype(fake_torch, use_gpu=False) == "bfloat16"


def test_audio_loading_gives_16k_mono_float32(monkeypatch):
    import numpy as np
    stereo = np.stack([np.ones(32000), np.zeros(32000)], axis=1).astype("float32")
    sf = types.ModuleType("soundfile")
    sf.read = lambda path, dtype, always_2d: (stereo, 32000)
    monkeypatch.setitem(sys.modules, "soundfile", sf)
    audio = qwen3_native.load_audio_16k("x.wav")
    assert audio.dtype == np.float32 and audio.ndim == 1
    assert len(audio) == 16000 and float(audio[0]) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Qwen3-ASR transcribe()
# ---------------------------------------------------------------------------

def test_a_forced_language_and_no_hint_sends_exactly_the_old_request(fake_torch, fake_audio):
    model, processor, fake = _asr([{"language": None, "transcription": "你好"}])
    out = model.transcribe(audio="/a.wav", language="Chinese")
    assert processor.requests == [{"audio": ["audio:/a.wav"], "language": "Chinese", "kwargs": {}}]
    assert processor.decoded[1] == "parsed"
    assert [(r.text, r.language) for r in out] == [("你好", "Chinese")]
    assert fake.calls[0]["max_new_tokens"] == qwen3_native.MAX_NEW_TOKENS


def test_an_empty_prompt_is_never_sent(fake_torch, fake_audio):
    model, processor, _ = _asr([{"language": None, "transcription": "x"}])
    model.transcribe(audio="/a.wav", language="Japanese", prompt="")
    model.transcribe(audio="/a.wav", language="Japanese", prompt=None)
    assert [r["kwargs"] for r in processor.requests] == [{}, {}]


def test_a_prompt_goes_to_the_processor_as_prompt(fake_torch, fake_audio):
    model, processor, _ = _asr([{"language": None, "transcription": "x"}])
    model.transcribe(audio="/a.wav", language="Chinese", prompt="Vocabulary: 小明, 师姐")
    assert processor.requests[0]["kwargs"] == {"prompt": "Vocabulary: 小明, 师姐"}


def test_auto_detect_reports_the_language_the_model_heard(fake_torch, fake_audio):
    model, processor, _ = _asr([{"language": "Japanese", "transcription": "こんにちは"}])
    (result,) = model.transcribe(audio="/a.wav", language=None)
    assert processor.requests[0]["language"] is None
    assert (result.text, result.language) == ("こんにちは", "Japanese")


def test_a_batch_returns_one_result_per_clip_in_input_order(fake_torch, fake_audio):
    parsed = [{"language": None, "transcription": t} for t in ("one", "two", "three")]
    model, processor, _ = _asr(parsed)
    out = model.transcribe(audio=["/1.wav", "/2.wav", "/3.wav"], language="Korean")
    assert processor.requests[0]["audio"] == ["audio:/1.wav", "audio:/2.wav", "audio:/3.wav"]
    assert [r.text for r in out] == ["one", "two", "three"]


def test_a_result_count_that_does_not_match_the_clips_raises(fake_torch, fake_audio):
    model, _, _ = _asr([{"language": None, "transcription": "only one"}])
    with pytest.raises(RuntimeError, match="1 results for 2 clips"):
        model.transcribe(audio=["/1.wav", "/2.wav"], language="Chinese")


def test_a_missing_transcription_becomes_empty_text(fake_torch, fake_audio):
    model, _, _ = _asr([{"language": None, "transcription": None}])
    assert model.transcribe(audio="/a.wav", language="Chinese")[0].text == ""


def test_only_the_new_tokens_are_decoded_and_unused_inputs_are_dropped(fake_torch, fake_audio):
    model, processor, fake = _asr([{"language": None, "transcription": "x"}])
    model.transcribe(audio="/a.wav", language="Chinese")
    assert "num_audio_tokens" not in fake.calls[0]
    assert processor.decoded[0] == ("generated", (slice(None), slice(2, None)))


# ---------------------------------------------------------------------------
# asr_backend on top of it: batching, hint pass-through, cancel, GPU fallback
# ---------------------------------------------------------------------------

class StrictModel:
    """transcribe(audio, language) only: a request with the hint off must not
    carry a `prompt` argument at all."""
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, language):
        self.calls.append((audio, language))
        items = audio if isinstance(audio, list) else [audio]
        return [types.SimpleNamespace(text=os.path.basename(p), language=language) for p in items]


class HintModel(StrictModel):
    def transcribe(self, audio, language, prompt=None):
        self.calls.append((audio, language, prompt))
        items = audio if isinstance(audio, list) else [audio]
        return [types.SimpleNamespace(text=os.path.basename(p), language=language) for p in items]


@pytest.fixture
def sliced(monkeypatch):
    monkeypatch.setattr(ab, "extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())


def _segments(n):
    return [{"start": i * 2.0, "end": i * 2.0 + 2.0, "text": "w"} for i in range(n)]


def test_hint_off_calls_the_model_without_a_prompt_argument(monkeypatch, sliced):
    model = StrictModel()
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda **k: model)
    ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(3), batch_size=2)
    ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(1), prompt="")
    assert len(model.calls) == 3 and all(len(c) == 2 for c in model.calls)


def test_the_hint_reaches_every_batch_and_the_one_by_one_retry(monkeypatch, sliced):
    model = HintModel()
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda **k: model)
    ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(3), batch_size=2,
                                    prompt="Vocabulary: 小明")
    assert [c[2] for c in model.calls] == ["Vocabulary: 小明", "Vocabulary: 小明"]


def test_a_failed_batch_retries_one_by_one_with_the_same_hint(monkeypatch, sliced):
    class Oom(HintModel):
        def transcribe(self, audio, language, prompt=None):
            if isinstance(audio, list):
                self.calls.append(("batch", prompt))
                raise RuntimeError("CUDA out of memory")
            return super().transcribe(audio, language, prompt)
    model = Oom()
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda **k: model)
    out = ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(2),
                                          batch_size=2, prompt="Vocabulary: x")
    assert [c[-1] for c in model.calls] == ["Vocabulary: x"] * 3
    assert [s["text"] for s in out] == ["seg_0.wav", "seg_1.wav"]


def test_progress_is_reported_after_each_batch(monkeypatch, sliced):
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda **k: StrictModel())
    seen = []
    ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(4), batch_size=2,
                                    progress_cb=seen.append)
    assert seen == pytest.approx([0.5, 1.0])


def test_the_vad_backend_passes_the_hint_to_the_segment_backend(monkeypatch):
    import vad_segments
    seen = {}

    class FakeSegmentBackend:
        def __init__(self, model_size="1.7B"):
            pass

        def transcribe(self, audio_path, language, spans, **kwargs):
            seen.update(kwargs)
            return [{"start": s["start"], "end": s["end"], "text": "t"} for s in spans]
    monkeypatch.setattr(ab, "Qwen3ASRBackend", FakeSegmentBackend)
    monkeypatch.setattr(ab, "load_audio_16k", lambda p: [0.0] * 16000)
    monkeypatch.setattr(vad_segments, "speech_spans",
                        lambda audio, sr, vad_fn=None: [vad_segments.Span(0.0, 1.0)])
    ab.Qwen3ASRVadBackend().transcribe("/a.wav", "zh", prompt="Vocabulary: x")
    assert seen["prompt"] == "Vocabulary: x"


def test_the_mixed_language_path_sends_the_hint_and_keeps_the_heard_language(monkeypatch):
    import mixed_language
    seen = []

    class MixedModel:
        def transcribe(self, audio, language, prompt=None):
            seen.append((language, prompt))
            return [types.SimpleNamespace(text="こんにちは", language="Japanese")]
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda **k: MixedModel())
    monkeypatch.setattr(ab, "extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())
    span = types.SimpleNamespace(start_s=0.0, end_s=1.0)
    window = types.SimpleNamespace(start_s=0.0, end_s=1.0)
    monkeypatch.setattr(mixed_language, "transcribe_spans",
                        lambda spans, language, run_span, rerun, **k: run_span(spans[0])[0])
    out = ab.Qwen3ASRVadBackend()._transcribe_mixed(
        "/a.wav", "zh", [span], [window], False, None, None, prompt="Vocabulary: x")
    assert seen == [(None, "Vocabulary: x")] and out[0]["text"] == "こんにちは"


# ---------------------------------------------------------------------------
# Forced aligner
# ---------------------------------------------------------------------------

class FakeAlignerProcessor:
    unused_input_names = []

    def __init__(self, items):
        self.items = items
        self.prepared = None
        self.decoded_with = None

    def prepare_forced_aligner_inputs(self, audio, transcript, language=None):
        self.prepared = (audio, transcript, language)
        words = [[it["text"] for it in self.items]]
        return FakeInputs(input_ids="ids"), words

    def decode_forced_alignment(self, logits, input_ids, word_lists, timestamp_token_id):
        self.decoded_with = (logits, input_ids, word_lists, timestamp_token_id)
        return [self.items]


class FakeAlignerModel:
    device, dtype = "cpu", "bfloat16"
    config = types.SimpleNamespace(timestamp_token_id=151705)

    def __call__(self, **kwargs):
        return types.SimpleNamespace(logits="LOGITS")


def _aligner(items):
    processor = FakeAlignerProcessor(items)
    return qwen3_native.NativeQwen3Aligner(FakeAlignerModel(), processor), processor


def test_align_returns_units_with_text_start_and_end(fake_torch, fake_audio):
    aligner, processor = _aligner([{"text": "你", "start_time": 0.08, "end_time": 0.4},
                                   {"text": "好", "start_time": 0.4, "end_time": 0.88}])
    (units,) = aligner.align(audio="/c.wav", text="你好", language="Chinese")
    assert [(u.text, u.start_time, u.end_time) for u in units] == [
        ("你", 0.08, 0.4), ("好", 0.4, 0.88)]
    assert processor.prepared == (["audio:/c.wav"], ["你好"], "Chinese")
    assert processor.decoded_with[0] == "LOGITS" and processor.decoded_with[3] == 151705


@pytest.mark.parametrize("language,package", [("Japanese", "nagisa"), ("Korean", "soynlp")])
def test_japanese_and_korean_need_their_word_splitter(monkeypatch, fake_torch, fake_audio,
                                                       language, package):
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == package else real(name, *a, **k))
    aligner, _ = _aligner([])
    with pytest.raises(qwen3_native.TransformersUnavailableError) as err:
        aligner.align(audio="/c.wav", text="x", language=language)
    assert package in str(err.value) and "install it in Diagnostics" in str(err.value)


def test_chinese_and_english_need_no_extra_package(monkeypatch, fake_torch, fake_audio):
    import importlib.util
    monkeypatch.setattr(importlib.util, "find_spec", lambda *a, **k: None)
    aligner, _ = _aligner([{"text": "hi", "start_time": 0.0, "end_time": 0.5}])
    assert aligner.align(audio="/c.wav", text="hi", language="English")[0][0].text == "hi"


def _native_aligner_run(monkeypatch, items):
    """align_with_qwen3 with a real NativeQwen3Aligner over fakes: one coarse
    line per user line, everything else (chunking, repair, flags) is real."""
    aligner, _ = _aligner(items)
    monkeypatch.setattr(fa, "load_qwen3_aligner", lambda use_gpu=False, **_: aligner)
    monkeypatch.setattr(fa, "_extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())
    monkeypatch.setattr(qwen3_native, "load_audio_16k", lambda path: "audio")
    monkeypatch.setitem(sys.modules, "torch", _fake_torch_module())
    segs = [{"start": 0.0, "end": 4.0, "text": "你好再见"}]
    return fa.align_with_qwen3("/a.wav", ["你好", "再见"], segs, language="zh")


def _fake_torch_module():
    torch = types.ModuleType("torch")
    torch.inference_mode = lambda: _NoGrad()
    return torch


def test_aligned_lines_get_their_own_times_and_no_flag(monkeypatch):
    items = [{"text": t, "start_time": s, "end_time": e}
             for t, s, e in (("你", 0.0, 0.5), ("好", 0.5, 1.0), ("再", 2.0, 2.5), ("见", 2.5, 3.0))]
    lines = _native_aligner_run(monkeypatch, items)
    assert [(ln.zh, ln.start, ln.end, ln.flag) for ln in lines] == [
        ("你好", 0.0, 1.0, None), ("再见", 2.0, 3.0, None)]


def test_zero_length_aligned_units_are_flagged_timing_uncertain(monkeypatch):
    items = [{"text": t, "start_time": s, "end_time": e}
             for t, s, e in (("你", 0.0, 0.5), ("好", 0.5, 1.0), ("再", 3.0, 3.0), ("见", 3.0, 3.0))]
    lines = _native_aligner_run(monkeypatch, items)
    flagged = [ln for ln in lines if ln.flag == "timing_uncertain"]
    assert [ln.zh for ln in flagged] == ["再见"]
    assert flagged[0].flag_note
    assert lines[0].flag is None


def test_the_aligner_supports_english_on_top_of_the_asr_languages():
    assert fa.ALIGNER_LANGUAGE_NAMES["en"] == "English"
    assert set(fa.LANGUAGE_NAMES) == {"zh", "ja", "ko"}


# ---------------------------------------------------------------------------
# Language names vs codes: the backends send names, the processor resolves codes too
# ---------------------------------------------------------------------------

def test_the_backend_sends_language_names_not_codes(monkeypatch, sliced):
    model = StrictModel()
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda **k: model)
    for code, name in (("zh", "Chinese"), ("ja", "Japanese"), ("ko", "Korean")):
        ab.Qwen3ASRBackend().transcribe("/a.wav", code, whisper_segments=_segments(1))
        assert model.calls[-1][1] == name


def test_an_unsupported_language_is_refused_before_loading_a_model(monkeypatch, sliced):
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda **k: pytest.fail("loaded a model"))
    with pytest.raises(ValueError, match="doesn't cover language='fr'"):
        ab.Qwen3ASRBackend().transcribe("/a.wav", "fr", whisper_segments=_segments(1))


# ---------------------------------------------------------------------------
# Vocabulary hint builder
# ---------------------------------------------------------------------------

_series_names = iter(f"S{i}" for i in range(10_000))


def _title(db, **fields):
    series = db.create_series(next(_series_names))
    did = db.create_drama(title_en="T", series_id=series, **fields)
    return did, series


def test_no_characters_or_glossary_means_no_prompt(isolated_db):
    did, _ = _title(isolated_db, vocabulary_hint=1)
    assert vh.build_vocabulary_hint(did) == ""
    assert vh.hint_for_run(isolated_db.get_drama(did)) is None


def test_the_hint_lists_character_names_then_glossary_terms_and_aliases(isolated_db):
    did, series = _title(isolated_db, vocabulary_hint=1)
    isolated_db.upsert_character(did, "SPEAKER_00", character_name="小明")
    isolated_db.upsert_series_character(series, "师姐", aliases="大师姐|阿姐")
    isolated_db.upsert_glossary_term(series, "青云宗", "Qingyun Sect", aliases="青云门")
    hint = vh.build_vocabulary_hint(did)
    assert hint == "Vocabulary: 小明, 师姐, 大师姐, 阿姐, 青云宗, 青云门"
    assert vh.hint_for_run(isolated_db.get_drama(did)) == hint


def test_speaker_labels_blanks_phrases_and_duplicates_are_left_out(isolated_db):
    did, series = _title(isolated_db, vocabulary_hint=1)
    isolated_db.upsert_character(did, "SPEAKER_00", character_name="SPEAKER_01")
    isolated_db.upsert_character(did, "SPEAKER_01", character_name="  ")
    isolated_db.upsert_character(did, "SPEAKER_02", character_name="Aria")
    isolated_db.upsert_series_character(series, "aria", aliases="")
    isolated_db.upsert_glossary_term(series, "x" * (vh.MAX_TERM_CHARS + 1), "long phrase")
    isolated_db.upsert_glossary_term(series, "龙", "dragon")
    assert vh.build_vocabulary_hint(did) == "Vocabulary: Aria, 龙"


def test_the_hint_is_capped_in_terms_and_in_characters(isolated_db):
    did, series = _title(isolated_db, vocabulary_hint=1)
    for i in range(vh.MAX_TERMS + 25):
        isolated_db.upsert_glossary_term(series, f"名{i:03d}", "t")
    hint = vh.build_vocabulary_hint(did)
    assert hint.startswith(vh.PREFIX)
    assert len(hint.removeprefix(vh.PREFIX).split(", ")) <= vh.MAX_TERMS
    long_did, long_series = _title(isolated_db, vocabulary_hint=1)
    for i in range(30):
        isolated_db.upsert_glossary_term(long_series, f"{'w' * 25}{i:02d}", "t")
    capped = vh.build_vocabulary_hint(long_did)
    assert len(capped) <= vh.MAX_CHARS
    assert capped.count(",") >= 1


def test_the_hint_never_carries_settings_keys_or_paths(isolated_db):
    did, series = _title(isolated_db, vocabulary_hint=1, source_url="https://example.com/secret?k=1")
    isolated_db.upsert_glossary_term(series, "龙", "dragon", notes="/home/me/secret.txt key=sk-abc")
    hint = vh.build_vocabulary_hint(did)
    assert hint == "Vocabulary: 龙"
    assert "secret" not in hint and "sk-" not in hint and "example.com" not in hint


def test_a_title_with_the_switch_off_gets_no_prompt_even_with_names(isolated_db):
    did, series = _title(isolated_db)
    isolated_db.upsert_glossary_term(series, "龙", "dragon")
    drama = isolated_db.get_drama(did)
    assert drama.get("vocabulary_hint") is None
    assert vh.hint_for_run(drama) is None
    isolated_db.update_drama(did, vocabulary_hint=0)
    assert vh.hint_for_run(isolated_db.get_drama(did)) is None


def test_an_unknown_title_gives_an_empty_hint(isolated_db):
    assert vh.build_vocabulary_hint(99999) == ""


# ---------------------------------------------------------------------------
# The saved setting, run settings, service plumbing and CLI parity
# ---------------------------------------------------------------------------

def test_the_setting_is_off_by_default_and_round_trips(isolated_db):
    from services import transcribe_service
    did, _ = _title(isolated_db)
    assert transcribe_service.get_transcribe_config(did)["vocabulary_hint"] is False
    assert isolated_db.get_drama(did)["vocabulary_hint"] is None
    assert transcribe_service.update_transcribe_config(did, vocabulary_hint=True)["vocabulary_hint"]
    assert isolated_db.get_drama(did)["vocabulary_hint"] == 1
    # None leaves it as saved; False turns it back off.
    assert transcribe_service.update_transcribe_config(did, whisper_fast_mode=True)["vocabulary_hint"]
    assert not transcribe_service.update_transcribe_config(did, vocabulary_hint=False)["vocabulary_hint"]


def test_the_config_route_reads_and_saves_the_setting(isolated_db):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    did, _ = _title(isolated_db)
    client = TestClient(create_app(ApiSettings()), base_url="http://127.0.0.1:8600",
                        client=("127.0.0.1", 50000), raise_server_exceptions=False)
    path = f"/api/transcribe/dramas/{did}/config"
    got = client.get(path)
    assert got.status_code == 200 and got.json()["vocabulary_hint"] is False
    saved = client.post(path, json={"vocabulary_hint": True})
    assert saved.status_code == 200 and saved.json()["vocabulary_hint"] is True
    assert client.get(path).json()["vocabulary_hint"] is True


def test_run_settings_record_the_hint_as_a_boolean_only():
    import raw_transcript
    assert "vocabulary_hint" in raw_transcript.SETTINGS_KEYS
    on = raw_transcript.build_run_settings(vocabulary_hint="Vocabulary: secret-name")
    off = raw_transcript.build_run_settings()
    assert on["vocabulary_hint"] is True and off["vocabulary_hint"] is False
    assert "secret-name" not in repr(on)
    assert set(on) == set(raw_transcript.SETTINGS_KEYS)


def _start(monkeypatch, isolated_db, *, hint, backend, names=True):
    from services import transcribe_service
    import importlib.util
    import background_jobs
    monkeypatch.setattr(importlib.util, "find_spec", lambda *a, **k: object())
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "5.19.0")
    sys.path.insert(0, os.path.dirname(__file__))
    from test_transcribe_service import _drama_with_audio, _capture_worker_start
    did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", asr_backend_choice=backend)
    series = isolated_db.create_series(next(_series_names))
    isolated_db.update_drama(did, series_id=series, **({"vocabulary_hint": hint} if hint is not None else {}))
    if names:
        isolated_db.upsert_glossary_term(series, "龙", "dragon")
    captured = _capture_worker_start(monkeypatch)
    transcribe_service.start_transcribe_run(did)
    background_jobs._jobs.pop(f"transcribe_{did}", None)
    return captured


@pytest.mark.parametrize("hint,backend,names,expected", [
    (1, "qwen3_asr", True, "Vocabulary: 龙"),
    (1, "qwen3_asr_vad", True, "Vocabulary: 龙"),
    (1, "qwen3_asr_long", True, "Vocabulary: 龙"),
    (None, "qwen3_asr", True, None),
    (0, "qwen3_asr", True, None),
    (1, "qwen3_asr", False, None),
    (1, "whisper", True, None)])
def test_a_run_gets_the_hint_only_when_on_for_a_qwen3_backend_with_names(
        monkeypatch, isolated_db, hint, backend, names, expected):
    captured = _start(monkeypatch, isolated_db, hint=hint, backend=backend, names=names)
    assert captured["qwen_prompt"] == expected


def test_the_worker_forwards_the_hint_to_the_pipeline(monkeypatch, tmp_path):
    import queue
    import background_jobs
    from services import transcribe_service
    seen = {}
    monkeypatch.setattr(transcribe_service, "_transcribe_pipeline",
                        lambda *a, **k: seen.update(k) or {"failed_reason": "empty"})
    monkeypatch.setattr(background_jobs, "start_own_process_group", lambda: None)
    q = queue.Queue()
    transcribe_service._transcribe_worker(
        "a.wav", "whisper", None, "zh", "simplified", "small", 5, 300, 0.5, False, "auto",
        False, False, False, "", False, "qwen3_asr", "whisper_diff", None, 1, False, False,
        2.0, 0.35, False, False, "normal", "Vocabulary: 龙", str(tmp_path / "scratch"), q)
    assert seen["qwen_prompt"] == "Vocabulary: 龙"


def test_the_pipeline_passes_the_hint_to_both_qwen3_backends(monkeypatch, isolated_db):
    import background_jobs
    import core as core_module
    monkeypatch.setattr(core_module, "load_whisper_model", lambda *a, **k: object())
    from services import transcribe_service
    sys.path.insert(0, os.path.dirname(__file__))
    from test_transcribe_service import _drama_with_audio, _seed_running_job
    did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
    monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                        lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "w"}])
    prompts = []

    class FakeBackend:
        def transcribe(self, *args, **kwargs):
            prompts.append(kwargs.get("prompt"))
            return [{"start": 0.0, "end": 1.0, "text": "t"}]
    monkeypatch.setattr(ab, "Qwen3ASRBackend", FakeBackend)
    monkeypatch.setattr(ab, "get_backend", lambda name: FakeBackend())
    for choice in ("qwen3_asr", "qwen3_asr_vad"):
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        transcribe_service._transcribe_pipeline(
            transcribe_service._ThreadReporter(job_id), os.path.join(ddir, "audio.wav"), "whisper",
            None, "zh", "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False,
            None, "", False, choice, "whisper_diff", qwen_prompt="Vocabulary: 龙")
        background_jobs._jobs.pop(job_id, None)
    assert prompts == ["Vocabulary: 龙", "Vocabulary: 龙"]


def test_cli_transcribe_saves_the_flag_and_defaults_to_the_saved_choice(monkeypatch):
    import cli
    from services import transcribe_service
    saved = []
    monkeypatch.setattr(transcribe_service, "update_transcribe_config",
                        lambda did, **fields: saved.append(fields))
    monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                        lambda *a, **k: {"job_id": "transcribe_1"})
    monkeypatch.setattr(cli, "_wait_for_job", lambda *a, **k: ("ok", "", {"line_count": 0}))
    parser_args = {}

    def run(*flags):
        sys.argv = ["cli", "transcribe", "--id", "1", *flags]
        saved.clear()
        monkeypatch.setattr(cli, "cmd_transcribe", lambda args: parser_args.update(vars(args)))
        cli.main()
        return parser_args["vocab_hint"]
    assert run("--vocab-hint") is True
    assert run("--no-vocab-hint") is False
    assert run() is None            # nothing passed: the title's saved choice stands


def test_cli_passes_only_an_explicit_choice_to_the_saved_config(monkeypatch):
    import argparse
    import cli
    from services import transcribe_service
    saved = []
    monkeypatch.setattr(transcribe_service, "update_transcribe_config",
                        lambda did, **fields: saved.append(fields))
    monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                        lambda *a, **k: {"job_id": "transcribe_1"})
    monkeypatch.setattr(cli, "_wait_for_job", lambda *a, **k: ("ok", "", {"line_count": 0}))
    base = dict(id=1, whisper_size=None, asr_backend=None, beam_size=None, min_silence_ms=None,
                min_pause=None, vad_threshold=None, sensitivity=None, separation_backend=None,
                separate_vocals=None, language=None, chinese_script=None, diarize=False,
                num_speakers=None, min_speakers=None, max_speakers=None, transcript=None,
                initial_prompt=None, extra_names=None)
    cli.cmd_transcribe(argparse.Namespace(**base, vocab_hint=True))
    assert saved == [{"vocabulary_hint": True, **{k: None for k in saved[0] if k != "vocabulary_hint"}}]
    saved.clear()
    cli.cmd_transcribe(argparse.Namespace(**base, vocab_hint=None))
    assert saved == []


def test_the_requirement_service_messages(monkeypatch):
    import importlib.util
    from services import qwen3_requirements_service as req
    from services.service_errors import DependencyUnavailableError
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a, **k: None if name == "torch" else real(name, *a, **k))
    with pytest.raises(DependencyUnavailableError, match="needs torch"):
        req.require_qwen3_packages("Qwen3-ASR")
    monkeypatch.setattr(importlib.util, "find_spec", lambda *a, **k: object())
    monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "5.15.0")
    req.require_qwen3_packages("Qwen3-ASR")          # new enough: no error
    assert req.import_failure_message(ImportError("No module named 'x'")) == req.MISSING_QWEN_MESSAGE
    assert req.import_failure_message(
        qwen3_native.TransformersUnavailableError("update it")) == "update it"


# ---------------------------------------------------------------------------
# Diagnostics tables
# ---------------------------------------------------------------------------

def test_diagnostics_registers_the_real_requirements():
    import diagnostics
    deps = diagnostics.OPTIONAL_DEPENDENCIES
    assert "qwen-asr" not in deps
    assert deps["transformers"][0] == "transformers" and "5.15" in deps["transformers"][1]
    assert deps["nagisa"][0] == "nagisa" and deps["soynlp"][0] == "soynlp"
    assert "qwen-asr" not in diagnostics.KNOWN_EXACT_PINS
    task = next(t for t in diagnostics.INSTALL_TASKS if t["id"] == "alt_asr")
    assert {"transformers", "nagisa", "soynlp", "torch"} <= set(task["packages"])
    assert "qwen-asr" not in task["packages"]


def test_the_incompatibility_notes_for_chatterbox_and_tada_are_kept():
    import diagnostics
    deps = diagnostics.OPTIONAL_DEPENDENCIES
    assert "Qwen3-ASR" in deps["chatterbox-tts"][1] and "5.2.0" in deps["chatterbox-tts"][1]
    assert "Qwen3-ASR" in deps["hume-tada"][1] and "below 5" in deps["hume-tada"][1]
    assert "Qwen3-ASR" not in deps["omnivoice"][1]


def test_the_moss_note_no_longer_says_it_conflicts_with_qwen3_and_stays_not_offered():
    import diagnostics
    moss = diagnostics.OPTIONAL_DEPENDENCIES["moss-transcribe-diarize"]
    assert "can't share" not in moss[1] and moss[2] == "experimental"
    reason = diagnostics.NOT_OFFERED_FOR_INSTALL["moss-transcribe-diarize"]
    assert "5.6" in reason and "Qwen3" not in reason
    assert diagnostics.known_install_limitation_reason("moss-transcribe-diarize")


def test_the_model_row_names_the_download_size_and_that_old_weights_are_not_reused():
    import diagnostics
    row = next(r for r in diagnostics.get_model_engine_versions() if r["name"] == "Qwen3-ASR")
    assert row["package"] == "transformers"
    assert "4.1 GB" in row["help"] and "1.6 GB" in row["help"] and "1.8 GB" in row["help"]
    assert "aren't reused" in row["help"]
    assert qwen3_native.APPROX_DOWNLOAD_GB == {"1.7B": 4.1, "0.6B": 1.6, "aligner": 1.8}


def test_the_huggingface_hub_limit_stays_conditional_on_the_installed_transformers_cap():
    import diagnostics
    known = diagnostics.KNOWN_UPGRADE_LIMITATIONS["huggingface-hub"]
    assert known["blocked_from"] == 2 and known["while_required_below_by"] == "transformers"
    assert "5.19" in known["reason"]
