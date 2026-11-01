"""
lib/model_offline.py -- model loads that must not reach the network.

A run started away from the PC must never download a model, and a model on
disk is not enough on its own: Hugging Face and ModelScope loaders check the
repo's main branch online and fetch a newer revision when there is one. So
such a run loads offline:

- The request thread marks itself (`set_offline`); background_jobs and
  gpu_process_job carry the mark into the job they start (`for_thread`,
  `for_process`), however much later a GPU slot frees up.
- A child process sets the libraries' offline switches in its own
  environment, which its own children inherit. A thread in the server never
  touches os.environ: that would make the owner's concurrent runs offline too.
  The loaders a thread job reaches ask `active()` and load from the cache
  folder or with local_files_only instead.

Generic on purpose: no knowledge of which models exist (that is
services/model_readiness_service.py).
"""

import contextvars
import functools
import os
import sys

NOT_DOWNLOADED_MESSAGE = ("This model is not downloaded yet. Ask the owner of the main PC "
                          "to download it first.")

# huggingface_hub, transformers and datasets read these; ModelScope and
# PaddleX have no switch of their own, so their loaders get a cache folder.
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}

# A new thread starts with an empty context, so a job thread never inherits
# this by accident; only for_thread / for_process set it there.
_OFFLINE = contextvars.ContextVar("baihe_models_offline", default=False)


def active() -> bool:
    return _OFFLINE.get()


def set_offline(value: bool):
    """Returns the token for reset()."""
    return _OFFLINE.set(bool(value))


def reset(token) -> None:
    _OFFLINE.reset(token)


def _run_in_thread(target, *args, **kwargs):
    token = _OFFLINE.set(True)
    try:
        return target(*args, **kwargs)
    finally:
        _OFFLINE.reset(token)


def _run_in_process(target, *args, **kwargs):
    os.environ.update(OFFLINE_ENV)
    # The switch is read when huggingface_hub is imported; unpickling the
    # target may already have imported it.
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None:
        setattr(constants, "HF_HUB_OFFLINE", True)
    _OFFLINE.set(True)
    return target(*args, **kwargs)


def for_thread(target):
    return functools.partial(_run_in_thread, target)


def for_process(target):
    """Top level and a partial, so it pickles for a spawned child."""
    return functools.partial(_run_in_process, target)


# --- Cache folders -----------------------------------------------------------

def hf_hub_dir() -> str:
    # huggingface_hub's own order: HF_HUB_CACHE overrides HF_HOME/hub. Looking
    # elsewhere would call a downloaded model missing, or an unpinned one present.
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return explicit
    hub = os.environ.get("HF_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache", "huggingface")
    return os.path.join(hub, "hub")


def hf_snapshot(repo_id: str, revision: str | None = None):
    """The folder `from_pretrained(repo_id, revision=revision)` resolves
    offline, or None. Without a revision, the snapshot refs/main points at; a
    download at a commit hash writes no ref, so a pinned one is looked up by
    its own folder."""
    base = os.path.join(hf_hub_dir(), "models--" + repo_id.replace("/", "--"))
    if revision is None:
        try:
            with open(os.path.join(base, "refs", "main"), encoding="utf-8") as f:
                revision = f.read().strip()
        except OSError:
            return None
    folder = os.path.join(base, "snapshots", revision)
    return folder if revision and os.path.isdir(folder) else None


def torch_hub_checkpoint(filename: str) -> bool:
    """A finished torch.hub checkpoint (torch.hub.get_dir()/checkpoints). torch.hub
    skips a file that is there, so on disk means no fetch; a torch zip cut
    off mid-download has no central directory."""
    import zipfile
    home = os.environ.get("TORCH_HOME") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
        "torch")
    path = os.path.join(home, "hub", "checkpoints", filename)
    try:
        return os.path.getsize(path) > 0 and (not filename.endswith(".pt")
                                              or zipfile.is_zipfile(path))
    except OSError:
        return False


def modelscope_model_dir(model_id: str, weights: str = "model.pt"):
    """A ModelScope download's folder when it holds `weights`, or None.
    ModelScope moved its layout to hub/models/<id> in newer releases."""
    root = os.environ.get("MODELSCOPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".cache", "modelscope", "hub")
    for prefix in (("models",), ()):
        folder = os.path.join(root, *prefix, *model_id.split("/"))
        if os.path.isfile(os.path.join(folder, weights)):
            return folder
    return None
