"""
A run started away from the PC never downloads a model: every route that can
reach a model loader refuses a household caller (here: an admin session on the
household listener, which is never "the PC") before any job starts when the
model is not on disk, lets it run once the model is there, and never checks the
owner at the PC. The checks read local caches only.
"""

import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from services import auth_service
from services import model_readiness_service as mrs
from services.service_errors import ConflictError, ForbiddenError
from tests.test_api_household_listener import _admin_client, _admin_session, _h, _household_client

MESSAGE = "This model is not downloaded yet. Ask the owner of the main PC to download it first."


@pytest.fixture
def caches(tmp_path, monkeypatch):
    """Every model cache the checks read, empty, under tmp_path."""
    import audio_preprocess
    paths = {name: tmp_path / name for name in ("hf", "torch", "modelscope", "paddlex", "sep")}
    for p in paths.values():
        p.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(paths["hf"]))
    monkeypatch.setenv("TORCH_HOME", str(paths["torch"]))
    monkeypatch.setenv("MODELSCOPE_CACHE", str(paths["modelscope"]))
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(paths["paddlex"]))
    monkeypatch.setattr(audio_preprocess, "MODEL_DIR", str(paths["sep"]))
    return paths


def _hf_repo(caches, repo_id, files):
    base = caches["hf"] / ("models--" + repo_id.replace("/", "--"))
    (base / "refs").mkdir(parents=True)
    (base / "refs" / "main").write_text("abc123")
    snap = base / "snapshots" / "abc123"
    for name, content in files.items():
        (snap / name).parent.mkdir(parents=True, exist_ok=True)
        (snap / name).write_text(content)
    return snap


def _whisper(caches, size="large-v3-turbo"):
    snap = caches["hf"] / f"models--Systran--faster-whisper-{size}" / "snapshots" / "r1"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")


def _transformers_repo(repo_id):
    return lambda caches: _hf_repo(caches, repo_id, {"config.json": "{}", "model.safetensors": "x"})


def _aligner(caches):
    _transformers_repo("Qwen/Qwen3-ForcedAligner-0.6B-hf")(caches)


def _pyannote(caches):
    _hf_repo(caches, "pyannote/speaker-diarization-community-1",
             {"config.yaml": "x", "embedding/pytorch_model.bin": "x"})


def _sensevoice(caches):
    folder = caches["modelscope"] / "models" / "iic" / "SenseVoiceSmall"
    folder.mkdir(parents=True)
    (folder / "model.pt").write_bytes(b"x")


def _paddle_ch(caches):
    for name in ("PP-OCRv5_server_det", "PP-OCRv5_server_rec", "PP-LCNet_x1_0_textline_ori"):
        (caches["paddlex"] / "official_models" / name).mkdir(parents=True)


def _no_loaders(monkeypatch):
    """A refused run must not get as far as any loader."""
    import asr_backend
    import dub
    import forced_align
    import whisper_models

    def boom(*a, **k):
        raise AssertionError("a model loader ran")
    monkeypatch.setattr(whisper_models, "load_whisper_model", boom)
    monkeypatch.setattr(asr_backend, "load_qwen3_asr", boom)
    monkeypatch.setattr(forced_align, "load_qwen3_aligner", boom)
    monkeypatch.setattr(dub, "_get_omnivoice_model", boom)


def _owned_drama(**fields):
    session = _admin_session()
    owner = auth_service.find_user_by_email("owner@example.com")["id"]
    fields.setdefault("source_language", "zh")
    did = db.create_drama(title_en="T", owner_user_id=owner, **fields)
    return did, session


# (url template, body, service module, function the route starts the job with, populate)
ROUTES = [
    ("/api/transcribe/dramas/{did}/run", {}, "transcribe_service", "start_transcribe_run", _whisper),
    ("/api/transcribe/dramas/{did}/autotune", {}, "autotune_service", "start_autotune_run", _whisper),
    ("/api/transcribe/dramas/{did}/lines/1/retranscribe", {}, "transcribe_service",
     "start_retranscribe_line", _whisper),
    ("/api/transcribe/dramas/{did}/retranscribe-lines", {"line_ids": [1]},
     "retranscribe_many_service", "start_retranscribe_many", _whisper),
    ("/api/transcribe/dramas/{did}/compare-transcription/run",
     {"selection": {"kind": "line_ids", "line_ids": [1]}},
     "compare_transcription_service", "start_compare", _whisper),
    ("/api/transcribe/dramas/{did}/retime/run", {"line_ids": [1]}, "retime_service",
     "start_retime", _aligner),
    ("/api/restructure/dramas/{did}/resplit", {"expected_line_ids": [], "align_to_audio": True},
     "restructure_service", "resplit_long_lines", _aligner),
    ("/api/diarization/dramas/{did}/run", None, "diarization_service",
     "start_diarization_run", _pyannote),
    ("/api/dub/dramas/{did}/run", {}, "dub_service", "start_dub_run",
     _transformers_repo("k2-fsa/OmniVoice")),
    ("/api/scanlate/dramas/{did}/run", {"engine": "ollama"}, "scanlate_run_service", "start_run",
     _paddle_ch),
    ("/api/review-jobs/dramas/{did}/fix-flagged", {"engine": "ollama"}, "review_jobs_service",
     "start_fix_flagged", _whisper),
    ("/api/review-extras/dramas/{did}/sensevoice", None, "review_extras_service",
     "start_sensevoice", _sensevoice),
    ("/api/live/sessions", {"url": "https://example.com/v", "engine": "ollama",
                            "whisper_size": "small"},
     "live_service", "start_session", lambda c: _whisper(c, "small")),
]


class _Reached(ConflictError):
    """Raised by a stubbed job start: the route got past every check."""


def _stub(monkeypatch, module, function):
    import importlib
    started = []

    def start(*a, **k):
        started.append(1)
        raise _Reached("reached")
    monkeypatch.setattr(importlib.import_module(f"services.{module}"), function, start)
    return started


def _reached(r):
    return r.status_code == 409 and r.json()["error"]["message"] == "reached"


def _post(client, url, body, session=None):
    # A body-less POST from the PC carries the header the React client sends.
    headers = _h(session) if session else {"X-Baihe-Local": "1"}
    return client.post(url, json=body, headers=headers) if body is not None \
        else client.post(url, headers=headers)


def _route_id(case):
    return case[0].split("{did}")[-1] or case[0]


@pytest.mark.parametrize("case", ROUTES, ids=_route_id)
def test_household_refused_when_model_missing(case, isolated_db, caches, monkeypatch):
    url, body, module, function, _populate = case
    started = _stub(monkeypatch, module, function)
    _no_loaders(monkeypatch)
    did, session = _owned_drama(audio_filename="a.wav")
    r = _post(_household_client(), url.format(did=did), body, session)
    assert r.status_code == 403, r.text
    assert r.json()["error"] == {"code": "model_not_downloaded", "message": MESSAGE}
    assert started == []


@pytest.mark.parametrize("case", ROUTES, ids=_route_id)
def test_household_runs_once_model_on_disk(case, isolated_db, caches, monkeypatch):
    url, body, module, function, populate = case
    started = _stub(monkeypatch, module, function)
    did, session = _owned_drama(audio_filename="a.wav")
    populate(caches)
    r = _post(_household_client(), url.format(did=did), body, session)
    assert _reached(r), r.text
    assert started == [1]


@pytest.mark.parametrize("case", ROUTES, ids=_route_id)
def test_owner_at_pc_never_refused(case, isolated_db, caches, monkeypatch):
    url, body, module, function, _populate = case
    started = _stub(monkeypatch, module, function)
    did, _session = _owned_drama(audio_filename="a.wav")
    r = _post(_admin_client(), url.format(did=did), body)
    assert _reached(r), r.text
    assert started == [1]


def test_queue_transcribe_item_refused_then_added(isolated_db, caches):
    did, session = _owned_drama(transcript_mode="whisper")
    client = _household_client()
    r = client.post("/api/queue/items", json={"drama_id": did, "step": "transcribe"},
                    headers=_h(session))
    assert r.status_code == 403 and r.json()["error"]["code"] == "model_not_downloaded"
    assert client.get("/api/queue", headers=_h(session)).json()["items"] == []
    _whisper(caches)
    r = client.post("/api/queue/items", json={"drama_id": did, "step": "transcribe"},
                    headers=_h(session))
    assert r.status_code == 200 and len(r.json()["items"]) == 1


def test_household_non_model_jobs_unaffected(isolated_db, caches, monkeypatch):
    started = _stub(monkeypatch, "speech_coverage_service", "start_speech_coverage")
    did, session = _owned_drama(transcript_mode="whisper")
    client = _household_client()
    r = client.post(f"/api/transcribe/dramas/{did}/speech-coverage", json={}, headers=_h(session))
    assert _reached(r) and started == [1]
    r = client.post("/api/queue/items", json={"drama_id": did, "step": "export"},
                    headers=_h(session))
    assert r.status_code == 200
    # Resplit without aligning to the audio loads nothing.
    started = _stub(monkeypatch, "restructure_service", "resplit_long_lines")
    r = client.post(f"/api/restructure/dramas/{did}/resplit", json={"expected_line_ids": []},
                    headers=_h(session))
    assert _reached(r) and started == [1]


# --- require_ready ----------------------------------------------------------

def test_require_ready_skips_every_check_at_the_pc():
    def never(): raise AssertionError("checked the owner")
    mrs.require_ready(True, [never])


def test_require_ready_refuses_with_a_plain_message():
    with pytest.raises(mrs.ModelNotDownloadedError) as info:
        mrs.require_ready(False, [lambda: True, lambda: False])
    assert isinstance(info.value, ForbiddenError)
    assert info.value.message == MESSAGE and "/" not in info.value.message
    mrs.require_ready(False, [lambda: True])


# --- Cache checks -----------------------------------------------------------

def test_hf_model_ready_needs_config_and_every_shard(caches):
    repo = "org/sharded"
    assert mrs.hf_model_ready(repo) is False
    index = {"weight_map": {"a": "model-1.safetensors", "b": "model-2.safetensors"}}
    snap = _hf_repo(caches, repo, {"config.json": "{}", "model-1.safetensors": "x",
                                   "model.safetensors.index.json": json.dumps(index)})
    assert mrs.hf_model_ready(repo) is False
    (snap / "model-2.safetensors").write_text("x")
    assert mrs.hf_model_ready(repo) is True
    os.remove(snap / "config.json")
    assert mrs.hf_model_ready(repo) is False


def test_hf_model_ready_reads_the_snapshot_main_points_at(caches):
    repo = "org/single"
    snap = _hf_repo(caches, repo, {"config.json": "{}", "model.safetensors": "x"})
    assert mrs.hf_model_ready(repo) is True
    (snap.parent.parent / "refs" / "main").write_text("other")
    assert mrs.hf_model_ready(repo) is False


def test_whisper_ready_honours_a_local_model_folder(caches, tmp_path):
    assert mrs.whisper_ready("large-v3-turbo") is False
    assert mrs.whisper_ready("large-v3-turbo", str(tmp_path)) is True
    _whisper(caches)
    assert mrs.whisper_ready("large-v3-turbo") is True


def test_diarization_ready_takes_either_pipeline(caches):
    assert mrs.diarization_ready() is False
    _hf_repo(caches, "pyannote/speaker-diarization-3.1", {"config.yaml": "x"})
    assert mrs.diarization_ready() is False
    _hf_repo(caches, "pyannote/segmentation-3.0", {"pytorch_model.bin": "x"})
    _hf_repo(caches, "pyannote/wespeaker-voxceleb-resnet34-LM", {"pytorch_model.bin": "x"})
    assert mrs.diarization_ready() is True


def test_separation_ready_by_backend(caches, monkeypatch):
    import zipfile
    import audio_preprocess
    monkeypatch.setattr(audio_preprocess, "MIN_CHECKPOINT_BYTES", 1)
    assert mrs.separation_ready("audio_separator") is False
    assert mrs.separation_ready("demucs") is False
    with zipfile.ZipFile(caches["sep"] / audio_preprocess.MEL_ROFORMER_VOCAL_MODEL, "w") as z:
        z.writestr("w", "x")
    assert mrs.separation_ready("audio_separator") is True
    checkpoints = caches["torch"] / "hub" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / mrs._HTDEMUCS_CHECKPOINT).write_bytes(b"x")
    assert mrs.separation_ready("demucs") is True


# --- What a transcription loads --------------------------------------------

def _needs(monkeypatch, drama_fields, **kwargs):
    """Names of the checks for_transcribe asks for."""
    for name in ("whisper_ready", "qwen_asr_ready", "aligner_ready", "diarization_ready",
                 "word_aligner_ready", "separation_ready", "_paddle_ready"):
        monkeypatch.setattr(mrs, name, (lambda n: lambda *a: n)(name))
    did = db.create_drama(title_en="T", source_language="ja", **drama_fields)
    out = []
    for need in mrs.for_transcribe(did, **kwargs):
        out.append(need())
    return out


def test_for_transcribe_qwen_long_needs_asr_and_aligner_not_whisper(isolated_db, monkeypatch):
    needs = _needs(monkeypatch, {"transcript_mode": "whisper", "asr_backend_choice": "qwen3_asr_long"})
    assert needs == ["qwen_asr_ready", "aligner_ready"]


def test_for_transcribe_whisper_extras(isolated_db, monkeypatch):
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_key", lambda name: "hf_x")
    needs = _needs(monkeypatch, {"transcript_mode": "whisper", "separate_vocals_first": 1,
                                 "realign_long_segments": 1}, run_diarize=True)
    assert needs == ["separation_ready", "whisper_ready", "word_aligner_ready",
                     "diarization_ready"]


def test_for_transcribe_groq_needs_no_whisper(isolated_db, monkeypatch):
    assert _needs(monkeypatch, {"transcript_mode": "whisper", "use_groq": 1}) == []
