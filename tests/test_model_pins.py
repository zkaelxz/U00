"""
Every Hugging Face model Baihe downloads is fetched at the commit recorded in
model_pins.REVISIONS, never at whatever a branch holds now. A loader that names
a repo with no pin, or a download call without a revision, fails here.
"""

import ast
import os
import re
import sys
import types

import pytest

import model_pins
import whisper_models

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Modules that download from the Hub. dub.py (frozen) and diarize.py (pyannote,
# gated behind the owner's own token) are not pinned yet.
LOADER_MODULES = ("whisper_models.py", "qwen3_native.py", "ocr.py", "scanlate_detect.py",
                  "scanlate_inpaint.py")
DOWNLOAD_CALLS = {"from_pretrained", "snapshot_download", "hf_hub_download",
                  "try_to_load_from_cache"}


def _repos_the_loaders_name():
    import ocr
    import qwen3_native
    import scanlate_detect
    import scanlate_inpaint
    from services import transcribe_options_service
    # Every size a title may store or Live may pick.
    sizes = {*transcribe_options_service._allowed_whisper_sizes(), *whisper_models.WHISPER_MODELS,
             *whisper_models.HIDDEN_WHISPER_SIZES, *whisper_models.LIVE_WHISPER_SIZES} - set(
                 whisper_models.PINNED_CT2_REPOS)
    repos = {whisper_models.faster_whisper_repo(size) for size in sizes}
    repos |= {repo for repo, _rev in whisper_models.PINNED_CT2_REPOS.values()}
    repos.add(whisper_models.ANIME_WHISPER_PIN["repo"])
    repos |= {qwen3_native.asr_repo_id(size) for size in qwen3_native.APPROX_DOWNLOAD_GB
              if size != "aligner"}
    repos |= {qwen3_native.ALIGNER_REPO, ocr.MANGA_OCR_REPO, ocr._PADDLE_VL_MANGA_REPO,
              ocr._PADDLE_VL_PROCESSOR_REPO, scanlate_detect._BUBBLE_ML_REPO,
              scanlate_inpaint._LAMA_ML_REPO}
    return repos


def test_every_repo_a_loader_names_has_a_pin():
    repos = _repos_the_loaders_name()
    assert None not in repos
    missing = sorted(r for r in repos if r not in model_pins.REVISIONS)
    assert not missing, f"no tested revision recorded in model_pins.REVISIONS for {missing}"


def test_every_pin_is_a_full_commit_hash():
    for repo, revision in model_pins.REVISIONS.items():
        assert re.fullmatch(r"[0-9a-f]{40}", revision), repo


def test_the_whisper_tables_read_their_revisions_from_the_one_table():
    for repo, revision in whisper_models.PINNED_CT2_REPOS.values():
        assert revision == model_pins.REVISIONS[repo]
    pin = whisper_models.ANIME_WHISPER_PIN
    assert pin["revision"] == model_pins.REVISIONS[pin["repo"]]


def test_every_download_call_passes_a_revision():
    unpinned = []
    for name in LOADER_MODULES:
        with open(os.path.join(ROOT, name), encoding="utf-8") as f:
            tree = ast.parse(f.read(), name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if called not in DOWNLOAD_CALLS:
                continue
            # NativeQwen3ASR.from_pretrained is this repo's own wrapper; it pins inside.
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                    and func.value.id == "cls":
                continue
            passes_revision = any(
                k.arg == "revision" or (k.arg is None and isinstance(k.value, ast.Call)
                                        and getattr(k.value.func, "id", None) == "_hub_kwargs")
                for k in node.keywords)
            if not passes_revision:
                unpinned.append(f"{name}:{node.lineno} {called}")
    assert not unpinned, f"download calls without revision=: {unpinned}"


def test_an_unknown_repo_is_refused_not_fetched_at_latest():
    with pytest.raises(model_pins.UnpinnedModelError, match="no tested version"):
        model_pins.revision("someone/new-model")


def test_a_vanished_revision_reads_plainly_however_deep_it_is_wrapped():
    class RevisionNotFoundError(Exception):
        pass
    try:
        try:
            raise RevisionNotFoundError("404 https://huggingface.co/x/y/revision/abc")
        except RevisionNotFoundError as inner:
            raise OSError("x/y is not a valid git identifier") from inner
    except OSError as outer:
        wrapped = outer
    assert model_pins.is_revision_gone(wrapped)
    assert not model_pins.is_revision_gone(OSError("disk full"))
    with pytest.raises(model_pins.RevisionGone) as info:
        with model_pins.fetching("x/y"):
            raise wrapped
    assert "does not fall back" in str(info.value) and "https://" not in str(info.value)


# --- Whisper sizes download and load at the pin -------------------------------

@pytest.fixture
def hub(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    calls = []

    def snapshot_download(repo, **kw):
        calls.append((repo, kw))
        return f"/hub/{repo}@{kw['revision']}"
    monkeypatch.setitem(sys.modules, "huggingface_hub",
                        types.SimpleNamespace(snapshot_download=snapshot_download))
    monkeypatch.setattr(whisper_models, "_download_model", whisper_models._download_model.real)
    built = []
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = lambda target, device, compute_type, **k: built.append(target) or object()
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})
    monkeypatch.setattr(whisper_models, "_whisper_device_info", {})
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    return types.SimpleNamespace(root=tmp_path, calls=calls, built=built)


def test_a_whisper_size_downloads_at_its_pin_even_without_a_token(hub):
    whisper_models.load_whisper_model("large-v3-turbo")
    repo = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    revision = model_pins.REVISIONS[repo]
    assert hub.calls == [(repo, {"revision": revision, "token": None, "etag_timeout": 30,
                                 "allow_patterns": ["config.json", "preprocessor_config.json",
                                                    "model.bin", "tokenizer.json",
                                                    "vocabulary.*"]})]
    assert hub.built == [f"/hub/{repo}@{revision}"]


def test_another_revision_on_disk_is_not_the_downloaded_model(hub):
    repo = "Systran/faster-whisper-small"
    snaps = hub.root / ("models--" + repo.replace("/", "--")) / "snapshots"
    from lib.ct2_repo import CT2_REQUIRED
    for revision in ("0" * 40, model_pins.REVISIONS[repo]):
        (snaps / revision).mkdir(parents=True)
        for name in CT2_REQUIRED:
            (snaps / revision / name).write_bytes(b"x")
        if revision != model_pins.REVISIONS[repo]:
            assert not whisper_models.is_whisper_model_cached("small")
    assert whisper_models.is_whisper_model_cached("small")
    # Without its tokenizer, faster-whisper would fetch one from an unpinned repo.
    (snaps / model_pins.REVISIONS[repo] / "tokenizer.json").unlink()
    assert not whisper_models.is_whisper_model_cached("small")


def test_a_vanished_whisper_revision_stops_without_trying_latest(hub, monkeypatch):
    class RevisionNotFoundError(Exception):
        pass

    def gone(repo, **kw):
        hub.calls.append((repo, kw))
        raise RevisionNotFoundError("404")
    sys.modules["huggingface_hub"].snapshot_download = gone
    with pytest.raises(whisper_models.ModelDownloadError, match="does not fall back"):
        whisper_models.load_whisper_model("small")
    assert len(hub.calls) == 1 and hub.calls[0][1]["revision"] == \
        model_pins.REVISIONS["Systran/faster-whisper-small"]
    assert hub.built == []


def test_an_unpinned_repo_id_is_never_downloaded(hub):
    with pytest.raises(model_pins.UnpinnedModelError):
        whisper_models.load_whisper_model("someone/faster-whisper-custom")
    assert hub.calls == [] and hub.built == []


@pytest.mark.parametrize("fetch", ["pinned_ct2", "anime"])
def test_a_vanished_pin_of_a_fine_tune_reads_plainly_not_as_network_trouble(
        fetch, monkeypatch, tmp_path):
    class RevisionNotFoundError(Exception):
        pass

    def model_info(repo, **kw):
        # Its text names the Hub, which alone would read as a network problem.
        raise RevisionNotFoundError("404 Client Error for https://huggingface.co/api/models/x")
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    import portable
    monkeypatch.setattr(portable, "data_dir", lambda: str(tmp_path))
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(
        HfApi=lambda: types.SimpleNamespace(model_info=model_info),
        snapshot_download=lambda *a, **k: pytest.fail("downloaded")))
    with pytest.raises(whisper_models.ModelDownloadError, match="does not fall back"):
        if fetch == "anime":
            whisper_models._fetch_anime_whisper(None)
        else:
            whisper_models._fetch_pinned_model("ja-1.5b", None)
