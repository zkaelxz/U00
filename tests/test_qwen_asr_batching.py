"""Step 103: batched Qwen3-ASR re-transcription (asr_backend.Qwen3ASRBackend
batch_size) and the setting behind it (services/asr_options_service,
api/routers/asr_options_routes). The model is faked: no GPU, network or
real qwen-asr."""
import os

import pytest

import asr_backend as ab


class FakeResult:
    def __init__(self, text):
        self.text = text


def _segments(n, length=2.0):
    return [{"start": i * length, "end": (i + 1) * length, "text": f"whisper {i}"}
            for i in range(n)]


@pytest.fixture
def sliced(monkeypatch, tmp_path):
    """Audio slicing writes an empty file; returns the paths it wrote."""
    written = []

    def fake_slice(audio_path, start, end, out_path):
        open(out_path, "wb").close()
        written.append(out_path)
    monkeypatch.setattr(ab, "extract_audio_slice", fake_slice)
    return written


def _use_model(monkeypatch, model):
    monkeypatch.setattr(ab, "load_qwen3_asr", lambda use_gpu=False, model_size="1.7B": model)


class EchoModel:
    """Returns the slice file name as the text, so each result shows which
    segment it came from; records every call's input size."""
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, language):
        items = audio if isinstance(audio, list) else [audio]
        self.calls.append(len(items))
        return [FakeResult(os.path.basename(p)) for p in items]


def test_default_is_one_segment_per_call(monkeypatch, sliced):
    model = EchoModel()
    _use_model(monkeypatch, model)
    out = ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(3))
    assert model.calls == [1, 1, 1]
    assert [s["text"] for s in out] == ["seg_0.wav", "seg_1.wav", "seg_2.wav"]


def test_batches_segments_and_keeps_each_text_on_its_own_segment(monkeypatch, sliced):
    model = EchoModel()
    _use_model(monkeypatch, model)
    segs = _segments(5)
    out = ab.Qwen3ASRBackend().transcribe("/a.wav", "ja", whisper_segments=segs, batch_size=2)
    assert model.calls == [2, 2, 1]
    assert [s["text"] for s in out] == [f"seg_{i}.wav" for i in range(5)]
    assert [(s["start"], s["end"]) for s in out] == [(s["start"], s["end"]) for s in segs]
    # Every temporary slice is removed after its batch.
    assert sliced and not any(os.path.exists(p) for p in sliced)


def test_oversized_segment_is_left_out_of_batches(monkeypatch, sliced):
    model = EchoModel()
    _use_model(monkeypatch, model)
    segs = [{"start": 0.0, "end": 1.0, "text": "a"},
            {"start": 1.0, "end": 400.0, "text": "merged blob"},
            {"start": 400.0, "end": 401.0, "text": "c"}]
    out = ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=segs, batch_size=4)
    assert model.calls == [2]
    assert [s["text"] for s in out] == ["seg_0.wav", "merged blob", "seg_2.wav"]


def test_a_short_batch_result_falls_back_to_one_call_per_segment(monkeypatch, sliced):
    """Never match results to segments by position when the counts differ."""
    class ShortModel(EchoModel):
        def transcribe(self, audio, language):
            if isinstance(audio, list):
                self.calls.append(len(audio))
                return [FakeResult("only one")]
            return super().transcribe(audio, language)
    model = ShortModel()
    _use_model(monkeypatch, model)
    out = ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(3),
                                          batch_size=3)
    assert model.calls == [3, 1, 1, 1]
    assert [s["text"] for s in out] == ["seg_0.wav", "seg_1.wav", "seg_2.wav"]


def test_batch_size_below_one_means_one(monkeypatch, sliced):
    model = EchoModel()
    _use_model(monkeypatch, model)
    ab.Qwen3ASRBackend().transcribe("/a.wav", "zh", whisper_segments=_segments(2), batch_size=0)
    assert model.calls == [1, 1]


# --- the setting ------------------------------------------------------------

def test_batch_size_setting_defaults_to_one_and_round_trips(isolated_db):
    from services import asr_options_service as svc
    assert svc.get_qwen_asr_batch_size() == 1
    assert svc.set_asr_options(qwen_asr_batch_size=8)["qwen_asr_batch_size"] == 8
    assert svc.get_qwen_asr_batch_size() == 8


@pytest.mark.parametrize("bad", [0, 17, -1, True, "4", 2.5])
def test_batch_size_setting_rejects_out_of_range(isolated_db, bad):
    from services import asr_options_service as svc
    from services.service_errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        svc.set_asr_options(qwen_asr_batch_size=bad)
    assert svc.get_qwen_asr_batch_size() == 1


def test_a_bad_stored_value_reads_as_clamped(isolated_db):
    import db
    from services import asr_options_service as svc
    db.set_app_setting(svc.QWEN_ASR_BATCH_KEY, 99)
    assert svc.get_qwen_asr_batch_size() == 16
    db.set_app_setting(svc.QWEN_ASR_BATCH_KEY, "junk")
    assert svc.get_qwen_asr_batch_size() == 1


# --- the route --------------------------------------------------------------

LOCAL = "http://127.0.0.1:8600"
REMOTE = "https://baihe.example.com"


def _client(base=LOCAL, **kw):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    client = ("127.0.0.1", 50000) if base == LOCAL else ("203.0.113.9", 50000)
    return TestClient(create_app(ApiSettings(**kw)), base_url=base, client=client,
                      raise_server_exceptions=False)


def test_route_reads_and_saves_on_the_pc(isolated_db):
    c = _client()
    r = c.get("/api/settings/asr-options")
    assert r.status_code == 200
    body = r.json()
    assert body["qwen_asr_batch_size"] == 1 and body["moss_experimental"] is False
    assert (body["qwen_asr_batch_min"], body["qwen_asr_batch_max"]) == (1, 16)
    r = c.post("/api/settings/asr-options", json={"qwen_asr_batch_size": 4})
    assert r.status_code == 200 and r.json()["qwen_asr_batch_size"] == 4
    assert c.post("/api/settings/asr-options", json={"qwen_asr_batch_size": 40}).status_code == 422
    assert c.post("/api/settings/asr-options", json={"other": 1}).status_code == 422


def test_route_save_is_refused_away_from_the_pc(isolated_db):
    c = _client(base=REMOTE)
    r = c.post("/api/settings/asr-options", json={"qwen_asr_batch_size": 4})
    assert r.status_code in (403, 404)
    from services import asr_options_service as svc
    assert svc.get_qwen_asr_batch_size() == 1
