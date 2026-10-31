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
import model_pins
from lib import model_offline
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
    # A pinned repo is downloaded at its commit, which writes no ref.
    revision = model_pins.REVISIONS.get(repo_id, "abc123")
    base = caches["hf"] / ("models--" + repo_id.replace("/", "--"))
    (base / "refs").mkdir(parents=True)
    (base / "refs" / "main").write_text(revision)
    snap = base / "snapshots" / revision
    for name, content in files.items():
        (snap / name).parent.mkdir(parents=True, exist_ok=True)
        (snap / name).write_text(content)
    return snap


def _whisper(caches, size=None):
    import whisper_models
    size = size or whisper_models.DEFAULT_WHISPER_SIZE
    repo = whisper_models.faster_whisper_repo(size)
    snap = caches["hf"] / f"models--{repo.replace('/', '--')}" / "snapshots" / model_pins.REVISIONS[repo]
    snap.mkdir(parents=True)
    from lib.ct2_repo import CT2_REQUIRED
    for name in CT2_REQUIRED:
        (snap / name).write_bytes(b"x")


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
        folder = caches["paddlex"] / "official_models" / name
        folder.mkdir(parents=True)
        (folder / "inference.json").write_text("{}")
        (folder / "inference.pdiparams").write_bytes(b"x")


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
    ("/api/transcribe/dramas/{did}/lines/1/retranscribe", {}, "retranscribe_line_service",
     "start_retranscribe_line", _whisper),
    ("/api/transcribe/dramas/{did}/retranscribe-lines", {"line_ids": [1]},
     "retranscribe_many_service", "start_retranscribe_many", _whisper),
    ("/api/transcribe/dramas/{did}/compare-transcription/run",
     {"selection": {"kind": "line_ids", "line_ids": [1]}},
     "compare_transcription_service", "start_compare", _whisper),
    ("/api/transcribe/dramas/{did}/retime/run", {"line_ids": [1]}, "retime_service",
     "start_retime", _aligner),
    ("/api/restructure/dramas/{did}/resplit", {"expected_line_ids": [], "align_to_audio": True},
     "resplit_service", "resplit_long_lines", _aligner),
    ("/api/diarization/dramas/{did}/run", None, "diarization_service",
     "start_diarization_run", _pyannote),
    ("/api/listen/dramas/{did}/run", {}, "listen_service", "start_listen",
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
        # Whether the job this would start loads offline (lib/model_offline.py).
        started.append(model_offline.active())
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
    assert started == [True]   # and it loads offline


@pytest.mark.parametrize("case", ROUTES, ids=_route_id)
def test_owner_at_pc_never_refused(case, isolated_db, caches, monkeypatch):
    url, body, module, function, _populate = case
    started = _stub(monkeypatch, module, function)
    did, _session = _owned_drama(audio_filename="a.wav")
    r = _post(_admin_client(), url.format(did=did), body)
    assert _reached(r), r.text
    assert started == [False]


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


def test_queue_rechecks_a_member_item_when_it_starts(isolated_db, caches):
    """The title's model may change after the item was added."""
    from services import queue_service
    did, session = _owned_drama(transcript_mode="whisper")
    _whisper(caches)
    client = _household_client()
    item = client.post("/api/queue/items", json={"drama_id": did, "step": "transcribe"},
                       headers=_h(session)).json()["items"][0]
    db.update_drama(did, whisper_size="medium")
    with pytest.raises(mrs.ModelNotDownloadedError):
        queue_service._start_item(queue_service._find(queue_service._load(), item["id"]))


def test_household_non_model_jobs_unaffected(isolated_db, caches, monkeypatch):
    started = _stub(monkeypatch, "speech_coverage_service", "start_speech_coverage")
    did, session = _owned_drama(transcript_mode="whisper")
    client = _household_client()
    r = client.post(f"/api/transcribe/dramas/{did}/speech-coverage", json={}, headers=_h(session))
    assert _reached(r) and len(started) == 1
    r = client.post("/api/queue/items", json={"drama_id": did, "step": "export"},
                    headers=_h(session))
    assert r.status_code == 200
    # Resplit without aligning to the audio loads nothing.
    started = _stub(monkeypatch, "resplit_service", "resplit_long_lines")
    r = client.post(f"/api/restructure/dramas/{did}/resplit", json={"expected_line_ids": []},
                    headers=_h(session))
    assert _reached(r) and len(started) == 1


# --- require_ready ----------------------------------------------------------

# require_ready marks the calling context, as it does a request's handler;
# each call here gets its own context so the test thread stays unmarked.

def test_require_ready_never_builds_the_list_at_the_pc():
    def never(*a, **k): raise AssertionError("built the owner's list")
    _in_own_context(lambda: mrs.require_ready(True, never, 1, x=2))


def test_require_ready_refuses_with_a_plain_message():
    with pytest.raises(mrs.ModelNotDownloadedError) as info:
        _in_own_context(lambda: mrs.require_ready(False, lambda: [lambda: True, lambda: False]))
    assert isinstance(info.value, ForbiddenError)
    assert info.value.message == MESSAGE and "/" not in info.value.message
    _in_own_context(lambda: mrs.require_ready(False, lambda n: [lambda: n], True))


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
    _whisper(caches, "large-v3-turbo")
    assert mrs.whisper_ready("large-v3-turbo") is True


def test_diarization_ready_needs_community_1(caches):
    """Offline there is no fallback to 3.1, so a 3.1 download alone does not count."""
    _hf_repo(caches, "pyannote/speaker-diarization-3.1", {"config.yaml": "x"})
    _hf_repo(caches, "pyannote/segmentation-3.0", {"pytorch_model.bin": "x"})
    _hf_repo(caches, "pyannote/wespeaker-voxceleb-resnet34-LM", {"pytorch_model.bin": "x"})
    assert mrs.diarization_ready() is False
    _pyannote(caches)
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
    (checkpoints / audio_preprocess.HTDEMUCS_CHECKPOINT).write_bytes(b"x")
    monkeypatch.setattr(mrs.importlib.util, "find_spec", lambda name: object())
    assert mrs.separation_ready("demucs") is True


def _cache_demucs(caches):
    import audio_preprocess
    checkpoints = caches["torch"] / "hub" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / audio_preprocess.HTDEMUCS_CHECKPOINT).write_bytes(b"x")


def test_cached_demucs_is_ready_while_roformer_is_missing(caches, monkeypatch):
    monkeypatch.setattr(mrs.importlib.util, "find_spec", lambda name: object())
    _cache_demucs(caches)
    assert mrs.separation_ready("demucs") is True
    assert mrs.separation_ready("audio_separator") is False


def test_both_separation_models_missing(caches, monkeypatch):
    monkeypatch.setattr(mrs.importlib.util, "find_spec", lambda name: object())
    assert mrs.separation_ready("demucs") is False
    assert mrs.separation_ready("audio_separator") is False
    assert mrs.separation_ready("auto", None) is False


def test_cached_demucs_without_the_package_is_not_ready(caches, monkeypatch):
    monkeypatch.setattr(mrs.importlib.util, "find_spec", lambda name: None)
    _cache_demucs(caches)
    assert mrs.separation_ready("demucs") is False
    assert mrs.separation_ready("auto", False) is False


def test_offline_auto_uses_cached_demucs_when_roformer_is_missing(caches, monkeypatch):
    monkeypatch.setattr(mrs.importlib.util, "find_spec", lambda name: object())
    _cache_demucs(caches)
    monkeypatch.setattr(mrs.model_offline, "active", lambda: False)
    assert mrs.separation_ready("auto", None) is False
    monkeypatch.setattr(mrs.model_offline, "active", lambda: True)
    assert mrs.separation_ready("auto", None) is True


def test_auto_separation_without_the_gpu_needs_demucs(caches, monkeypatch):
    """audio-separator refuses a CPU run, so "auto" falls back to Demucs."""
    import zipfile
    import audio_preprocess
    monkeypatch.setattr(audio_preprocess, "MIN_CHECKPOINT_BYTES", 1)
    monkeypatch.setattr(mrs.importlib.util, "find_spec", lambda name: object())
    with zipfile.ZipFile(caches["sep"] / audio_preprocess.MEL_ROFORMER_VOCAL_MODEL, "w") as z:
        z.writestr("w", "x")
    assert mrs.separation_ready("auto", None) is True
    assert mrs.separation_ready("auto", False) is False


def test_dub_clip_without_transcript_needs_omnivoice_asr(isolated_db, caches):
    did, _session = _owned_drama()
    _transformers_repo("k2-fsa/OmniVoice")(caches)
    db.upsert_character(did, "A", ref_audio_filename="a.wav", ref_text="你好")
    assert all(need() for need in mrs.for_dub(did))
    db.upsert_character(did, "B", ref_audio_filename="b.wav", ref_text="")
    assert not all(need() for need in mrs.for_dub(did))
    _transformers_repo(mrs._OMNIVOICE_ASR_REPO)(caches)
    assert all(need() for need in mrs.for_dub(did))


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


# --- Offline loading for a run started away from the PC ----------------------

def test_the_offline_mark_ends_with_the_request(isolated_db, caches, monkeypatch):
    """The mark lives in the handler's own context: the owner's next request,
    on a worker thread a member's request used, is not offline."""
    started = _stub(monkeypatch, "retime_service", "start_retime")
    did, session = _owned_drama()
    _aligner(caches)
    url = f"/api/transcribe/dramas/{did}/retime/run"
    for _ in range(3):
        assert _reached(_post(_household_client(), url, {"line_ids": [1]}, session))
        assert _reached(_post(_admin_client(), url, {"line_ids": [1]}))
    assert started == [True, False] * 3
    assert model_offline.active() is False


def _in_own_context(fn):
    import contextvars
    return contextvars.copy_context().run(fn)


def test_a_thread_job_inherits_the_mark(isolated_db):
    from jobs import job_launch
    seen = []

    def start(offline):
        model_offline.set_offline(offline)
        job_launch.start_job(f"offline_probe_{offline}", lambda: seen.append(model_offline.active()))
    for offline in (True, False):
        _in_own_context(lambda: start(offline))
        for _ in range(200):
            if len(seen) == (2 if not offline else 1):
                break
            __import__("time").sleep(0.01)
    assert seen == [True, False]


def test_a_process_job_runs_with_the_offline_environment(isolated_db, monkeypatch):
    """The child sets the libraries' switches in its own environment; its
    on_done (which may start the next stage) runs offline too."""
    import background_jobs as bg
    from jobs import job_launch
    from tests.test_background_jobs import _install_fake_process, _wait_for_status
    _install_fake_process(monkeypatch, run_target_on_start=True)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    for name in model_offline.OFFLINE_ENV:
        os.environ.pop(name, None)
    on_done_offline = []

    def worker(result_queue):
        result_queue.put(("ok", {name: os.environ.get(name) for name in model_offline.OFFLINE_ENV}))

    def start():
        model_offline.set_offline(True)
        job_launch.start_process_job("offline_process_probe", worker, args=(),
                             on_done=lambda job_id, result: on_done_offline.append(
                                 model_offline.active()))
    _in_own_context(start)
    status = _wait_for_status("offline_process_probe", "running")
    assert status["result"] == model_offline.OFFLINE_ENV
    assert on_done_offline == [True]
    bg.clear_job("offline_process_probe")


def test_an_owner_process_job_stays_online(isolated_db, monkeypatch):
    import background_jobs as bg
    from jobs import job_launch
    from tests.test_background_jobs import _install_fake_process, _wait_for_status
    _install_fake_process(monkeypatch, run_target_on_start=True)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop("HF_HUB_OFFLINE", None)
    job_launch.start_process_job("online_process_probe",
                         lambda q: q.put(("ok", os.environ.get("HF_HUB_OFFLINE"))), args=())
    assert _wait_for_status("online_process_probe", "running")["result"] is None
    bg.clear_job("online_process_probe")


def _fake_faster_whisper(monkeypatch):
    import sys
    import types
    import whisper_models
    built = []
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = lambda target, device, compute_type, **k: built.append((target, k)) or object()
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})
    monkeypatch.setattr(whisper_models, "_whisper_device_info", {})
    return built


def test_whisper_loads_offline_only_when_marked(caches, monkeypatch):
    import whisper_models
    built = _fake_faster_whisper(monkeypatch)
    _whisper(caches, "small")
    whisper_models.load_whisper_model("small")
    assert built[-1] == ("small", {})

    def member():
        model_offline.set_offline(True)
        monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})
        whisper_models.load_whisper_model("small", hf_token="hf_x")
    _in_own_context(member)
    # Offline loads the folder at the pin, never a name faster-whisper would look up.
    assert built[-1] == (whisper_models._generic_snapshot("small"), {"local_files_only": True})


def test_offline_whisper_refuses_a_model_not_on_disk(caches, monkeypatch):
    import whisper_models
    built = _fake_faster_whisper(monkeypatch)

    def member():
        model_offline.set_offline(True)
        whisper_models.load_whisper_model("medium")
    with pytest.raises(whisper_models.ModelDownloadError, match="not downloaded yet"):
        _in_own_context(member)
    assert built == []


def test_qwen_and_transformers_loaders_ask_for_local_files_when_marked():
    import qwen3_native
    assert qwen3_native._offline_kwargs() == {}
    assert _in_own_context(lambda: (model_offline.set_offline(True),
                                    qwen3_native._offline_kwargs())[1]) == {"local_files_only": True}


def _fake_pyannote(monkeypatch, loadable):
    import sys
    import types
    tried = []

    class Pipeline:
        @staticmethod
        def from_pretrained(model, token=None):
            tried.append(model)
            if model not in loadable:
                raise OSError("not in the cache")
            return object()
    audio = types.ModuleType("pyannote.audio")
    audio.Pipeline = Pipeline
    monkeypatch.setitem(sys.modules, "pyannote", types.ModuleType("pyannote"))
    monkeypatch.setitem(sys.modules, "pyannote.audio", audio)
    return tried


def test_pyannote_fallback_is_refused_offline(monkeypatch):
    import diarize
    tried = _fake_pyannote(monkeypatch, loadable={"pyannote/speaker-diarization-3.1"})
    assert diarize.load_pipeline("hf")[1] == "pyannote/speaker-diarization-3.1"

    def member():
        model_offline.set_offline(True)
        diarize.load_pipeline("hf")
    tried.clear()
    with pytest.raises(RuntimeError, match="not downloaded yet"):
        _in_own_context(member)
    assert tried == ["pyannote/speaker-diarization-community-1"]


def test_sensevoice_loads_the_cached_folder_offline(caches, monkeypatch):
    import sys
    import types
    import sensevoice_tags
    models = []
    funasr = types.ModuleType("funasr")
    funasr.AutoModel = lambda model, **k: models.append(model) or object()
    monkeypatch.setitem(sys.modules, "funasr", funasr)

    def member():
        model_offline.set_offline(True)
        sensevoice_tags._load_model(False)
    with pytest.raises(sensevoice_tags.SenseVoiceUnavailable, match="not downloaded yet"):
        _in_own_context(member)
    _sensevoice(caches)
    _in_own_context(member)
    assert models == [str(caches["modelscope"] / "models" / "iic" / "SenseVoiceSmall")]
    sensevoice_tags._load_model(False)
    assert models[-1] == sensevoice_tags.MODEL_ID


def test_manga_ocr_loads_the_cached_folder_offline(caches, monkeypatch):
    import sys
    import types
    import ocr
    fetched = []
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(
        snapshot_download=lambda repo, **kw: fetched.append((repo, kw)) or "/hub/manga"))
    assert ocr._manga_ocr_source() == ("/hub/manga",)
    assert fetched == [(ocr.MANGA_OCR_REPO, {"revision": model_pins.REVISIONS[ocr.MANGA_OCR_REPO],
                                              "etag_timeout": 30})]

    def member():
        model_offline.set_offline(True)
        return ocr._manga_ocr_source()
    with pytest.raises(RuntimeError, match="not downloaded yet"):
        _in_own_context(member)
    snap = _hf_repo(caches, ocr.MANGA_OCR_REPO, {"config.json": "{}", "pytorch_model.bin": "x"})
    assert _in_own_context(member) == (str(snap),)


def test_queue_add_needs_to_know_where_the_caller_is(isolated_db):
    from services import queue_service
    did, _session = _owned_drama()
    with pytest.raises(TypeError):
        queue_service.add_item(None, did, "export", True)
    with pytest.raises(TypeError):
        queue_service.retry_item(None, 1)


def test_a_member_queue_item_starts_offline(isolated_db, caches, monkeypatch):
    from services import queue_service, transcribe_service
    seen = []
    monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                        lambda did: seen.append(model_offline.active()) or {"job_id": "j"})
    did, session = _owned_drama(transcript_mode="whisper")
    _whisper(caches)
    item = _household_client().post("/api/queue/items", json={"drama_id": did, "step": "transcribe"},
                                    headers=_h(session)).json()["items"][0]
    queue_service._start_item(queue_service._find(queue_service._load(), item["id"]))
    assert seen == [True]
    assert model_offline.active() is False   # the runner thread is not left offline


def test_live_whisper_calls_keep_the_sessions_mark():
    """Live loads Whisper on a worker thread of its own, which must not lose
    the session's offline mark."""
    import live_whisper

    def member():
        model_offline.set_offline(True)
        runner = live_whisper.WhisperRunner(lambda: False, lambda *a: None, poll=0.01)
        return runner.run(lambda progress: model_offline.active(), timeout=5, label="probe")
    assert _in_own_context(member) is True
    runner = live_whisper.WhisperRunner(lambda: False, lambda *a: None, poll=0.01)
    assert runner.run(lambda progress: model_offline.active(), timeout=5, label="probe") is False


def test_scanlate_render_and_export_start_offline_for_a_member(isolated_db, monkeypatch):
    """They load LaMa only once cached; offline that load does not ask the Hub."""
    for function, path in (("start_render", "render"), ("start_export", "export")):
        started = _stub(monkeypatch, "scanlate_render_service", function)
        did, session = _owned_drama()
        url = f"/api/scanlate/dramas/{did}/{path}"
        assert _reached(_post(_household_client(), url, {}, session))
        assert _reached(_post(_admin_client(), url, {}))
        assert started == [True, False]


def test_lama_loads_the_cached_file_only_when_marked(monkeypatch):
    import sys
    import types
    import scanlate_inpaint
    calls = []

    def hf_hub_download(repo_id, filename, token=None, **kwargs):
        calls.append(kwargs)
        if kwargs.get("local_files_only"):
            raise OSError("not in the cache")
        raise RuntimeError("stop after the download call")
    hub = types.ModuleType("huggingface_hub")
    hub.hf_hub_download = hf_hub_download
    st = types.ModuleType("safetensors")
    st_torch = types.ModuleType("safetensors.torch")
    st_torch.load_file = lambda path: {}
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "safetensors", st)
    monkeypatch.setitem(sys.modules, "safetensors.torch", st_torch)
    monkeypatch.delitem(scanlate_inpaint.__dict__, "_lama_model", raising=False)
    with pytest.raises(RuntimeError, match="stop after"):
        scanlate_inpaint._load_lama_generator()

    def member():
        model_offline.set_offline(True)
        scanlate_inpaint._load_lama_generator()
    with pytest.raises(RuntimeError, match="not downloaded yet"):
        _in_own_context(member)
    pinned = {"revision": model_pins.REVISIONS[scanlate_inpaint._LAMA_ML_REPO], "etag_timeout": 30}
    assert calls == [pinned, {**pinned, "local_files_only": True}]


def test_offline_auto_separation_does_not_fall_back_to_an_undownloaded_demucs(caches, monkeypatch):
    import audio_preprocess
    ran = []

    def failing(name):
        def run(*a, **k):
            ran.append(name)
            raise audio_preprocess.VocalSeparationError(f"{name} failed")
        return run
    monkeypatch.setattr(audio_preprocess, "_BACKENDS", {
        "audio_separator": failing("audio_separator"), "demucs": failing("demucs")})
    monkeypatch.setattr(audio_preprocess.memory_headroom, "check_separation", lambda use_gpu: None)

    def member():
        model_offline.set_offline(True)
        audio_preprocess.separate_vocals("in.wav", "out.wav")
    with pytest.raises(audio_preprocess.VocalSeparationError, match="not downloaded yet"):
        _in_own_context(member)
    assert ran == ["audio_separator"]
    checkpoints = caches["torch"] / "hub" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / audio_preprocess.HTDEMUCS_CHECKPOINT).write_bytes(b"x")
    ran.clear()
    with pytest.raises(audio_preprocess.VocalSeparationError):
        _in_own_context(member)
    assert ran == ["audio_separator", "demucs"]
    ran.clear()
    with pytest.raises(audio_preprocess.VocalSeparationError):
        audio_preprocess.separate_vocals("in.wav", "out.wav")
    assert ran == ["audio_separator", "demucs"]


def test_a_queue_item_saved_without_at_pc_counts_as_away_from_the_pc():
    from services import queue_service
    raw = {"id": 1, "drama_id": 2, "step": "transcribe", "status": "waiting", "override": True}
    assert queue_service._clean_item(raw)["at_pc"] is False
    assert queue_service._clean_item(dict(raw, at_pc=True))["at_pc"] is True
