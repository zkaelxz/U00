"""
tests/test_install_presets.py -- Diagnostics "Packages" install fixes and
presets: pip flags (--no-cache-dir, --disable-pip-version-check), the
pip-cache permission hint, install names that are real PyPI distributions,
the task map, approx. sizes, PyPI links, the not-offered canvas package
and the qwen-asr transformers downgrade warning. No network, no real pip.
"""
import os

import pytest

import diagnostics
from services import diagnostics_gaps_service as svc

FLAGS = ["--no-cache-dir", "--disable-pip-version-check"]
CONSTRAINTS = ["-c", os.path.join(svc.default_project_root(), "constraints.txt")]

# Canonical PyPI distribution names this app installs, checked by hand
# against pypi.org. Static on purpose: a new package must be added here
# after checking its real distribution name.
KNOWN_PYPI_DISTS = {
    "onnxruntime", "faster-whisper", "ctranslate2", "opencv-python", "anthropic", "openai", "requests",
    "beautifulsoup4", "pyannote-audio", "soundfile", "pydub",
    "omnivoice", "pytesseract", "pillow", "paddleocr",
    "paddlepaddle",
    "manga-ocr", "jieba", "pypinyin", "sudachipy", "pykakasi", "kiwipiepy",
    "transformers", "torch", "torchaudio", "uroman", "sentencepiece", "yt-dlp",
    "opencc-python-reimplemented", "sudachidict-core", "safetensors", "huggingface-hub",
    "pypdf", "genanki", "ebooklib", "plyer", "playwright",
    "lightnovel-crawler",
    "trafilatura", "audio-separator", "funasr", "demucs", "cryptography", "authlib",
    "numpy", "httpx", "qwen-asr", "jiwer", "sacrebleu",
}
# Import names whose PyPI project is something else (or a squatter).
IMPORT_ONLY_NAMES = {"cv2", "pil", "bs4", "sklearn", "yaml", "skimage", "dateutil",
                     "attr", "magic", "fitz", "docx", "pptx", "serial", "usb", "crypto"}


def _offered():
    names = {k for k, (_i, _f, tier) in diagnostics.OPTIONAL_DEPENDENCIES.items()
             if tier in diagnostics.INSTALLABLE_TIERS}
    return names | {e["package"] for e in diagnostics.MODEL_ENGINE_REGISTRY if e.get("package")}


class _FakePopen:
    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self._rc = returncode

    def wait(self):
        return self._rc


# ---- A: pip flags and the cache hint ----

def test_stream_pip_install_disables_cache_and_version_check(monkeypatch):
    seen = []
    monkeypatch.setattr(diagnostics.subprocess, "Popen",
                        lambda cmd, **kw: seen.append(cmd) or _FakePopen([]))
    monkeypatch.setattr(diagnostics.sys, "executable", "/py")
    list(diagnostics.stream_pip_install(["jieba"]))
    assert seen == [["/py", "-m", "pip", "install", *FLAGS, "jieba"]]


def test_service_install_and_upgrade_commands_carry_the_flags(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ((cmd, _t),) = svc._install_commands("jieba")
    assert cmd[3:] == ["install", *FLAGS, "jieba", *CONSTRAINTS]
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    for cmd, _t in svc._install_commands("torch"):
        assert cmd[3:6] == ["install", *FLAGS]


WIN_LINE = ("ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: "
            "'C:\\users\\kae\\appdata\\local\\pip\\cache\\wheels\\ab\\jieba-0.42.1-py3-none-any.whl'")


@pytest.mark.parametrize("lines,hinted", [
    ([WIN_LINE], True),
    (["Permission denied: '/home/kae/.cache/pip/wheels/x.whl'"], True),
    (["ERROR: No matching distribution found for cv2"], False),
    (["Permission denied: 'C:\\Program Files\\Python312\\Lib\\site-packages\\x'"], False),
])
def test_pip_cache_permission_hint(lines, hinted):
    hint = diagnostics.pip_cache_permission_hint(lines)
    assert (hint is not None) is hinted
    if hinted:
        assert "%LOCALAPPDATA%\\pip\\cache" in hint and "antivirus" in hint


def test_failed_install_returns_the_hint_even_though_output_is_redacted(monkeypatch):
    def fake(cmd, timeout):
        yield {"line": WIN_LINE}
        yield {"returncode": 1, "timed_out": False}
    monkeypatch.setattr(svc, "stream_tree", fake)
    out = svc._run_commands([(["pip"], 1)])
    assert out["ok"] is False and out["hint"] == diagnostics.PIP_CACHE_PERMISSION_HINT
    assert "kae" not in " ".join(out["output_tail"])


def test_success_has_no_hint(monkeypatch):
    def fake(cmd, timeout):
        yield {"line": WIN_LINE}
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(svc, "stream_tree", fake)
    assert svc._run_commands([(["pip"], 1)])["hint"] is None


# ---- B: install names are real distributions ----

def test_every_offered_package_installs_a_known_pypi_distribution():
    for name in _offered():
        dist = diagnostics.canonical_dist(diagnostics.pip_install_name(name))
        assert dist in KNOWN_PYPI_DISTS, f"{name} installs {dist!r}, not a known distribution"
        assert dist not in IMPORT_ONLY_NAMES, name


@pytest.mark.parametrize("key,dist", [("cv2", "opencv-python"), ("PIL", "pillow"),
                                      ("bs4", "beautifulsoup4"), ("jieba", "jieba")])
def test_install_name_mapping(key, dist):
    assert diagnostics.pip_install_name(key) == dist


def test_service_installs_opencv_python_for_cv2(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ((cmd, _t),) = svc._install_commands("cv2")
    assert cmd[-3:] == ["opencv-python", *CONSTRAINTS]


def test_dependency_install_uses_the_dist_name(monkeypatch):
    seen = []
    monkeypatch.setattr(diagnostics, "stream_pip_install",
                        lambda args, py=None: seen.append(args) or iter(()))
    list(diagnostics.stream_dependency_install("PIL"))
    assert seen == [["pillow", *CONSTRAINTS]]


# ---- C: task map ----

def test_tasks_are_well_formed_and_name_real_packages():
    ids = [t["id"] for t in diagnostics.INSTALL_TASKS]
    assert len(ids) == len(set(ids))
    offered = _offered()
    for t in diagnostics.INSTALL_TASKS:
        assert t["label"] and t["help"] and t["group"] and t["packages"]
        assert len(t["packages"]) == len(set(t["packages"]))
        for p in t["packages"]:
            assert p in offered, (t["id"], p)


def test_every_feature_package_is_reachable_from_some_task():
    in_tasks = {p for t in diagnostics.INSTALL_TASKS for p in t["packages"]}
    keys = {k for k, (_i, _f, tier) in diagnostics.OPTIONAL_DEPENDENCIES.items()
            if tier in diagnostics.INSTALLABLE_TIERS}
    # requests ships with the core install; nothing to pick it for.
    assert keys - in_tasks <= {"requests"}


def test_presets_report_installed_state_sizes_and_what_to_install(monkeypatch):
    installed = {"numpy", "soundfile"}
    monkeypatch.setattr(diagnostics, "check_dependency", lambda imp: imp in installed)
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda dist: None)
    out = svc.get_install_presets()
    t = next(t for t in out["tasks"] if t["id"] == "transcribe")
    assert t["installed_count"] == 2 and t["to_install"] == [
        "faster_whisper", "ctranslate2"]
    assert t["approx_mb"] == (diagnostics.APPROX_DOWNLOAD_MB["faster-whisper"]
                              + diagnostics.APPROX_DOWNLOAD_MB["ctranslate2"])
    scan = next(t for t in out["tasks"] if t["id"] == "scanlate")
    assert "pypdf" in scan["packages"]
    assert "pypdf" in scan["to_install"]
    # paddleocr 3.x doesn't pull in paddlepaddle, and zh/ko pages default to paddle.
    assert {"paddleocr", "paddlepaddle"} <= set(scan["to_install"])
    assert scan["roles"]["paddleocr"] == scan["roles"]["paddlepaddle"] == "recommended"
    assert scan["approx_mb"] == sum(diagnostics.approx_download_mb(n) for n in scan["to_install"])
    cv2 = out["packages"]["cv2"]
    assert cv2["dist"] == "opencv-python" and cv2["installable"] is True
    assert cv2["source_url"] == "https://pypi.org/project/opencv-python/"


# ---- D/E: sizes and links ----

def test_every_offered_package_has_an_approx_size():
    for name in _offered() | {p for t in diagnostics.INSTALL_TASKS for p in t["packages"]}:
        mb = diagnostics.approx_download_mb(name)
        assert isinstance(mb, int) and mb > 0, name
    assert diagnostics.approx_download_mb("torch") >= 1000       # shown in GB
    assert diagnostics.approx_download_mb("jieba") < 1000


def test_pypi_url_is_built_only_from_a_valid_name(monkeypatch):
    assert diagnostics.pypi_url("pyannote.audio") == "https://pypi.org/project/pyannote-audio/"
    monkeypatch.setitem(diagnostics.PIP_DIST_NAMES, "x", "evil/../path?q=1")
    assert diagnostics.pypi_url("x") is None


# ---- F: not offered, downgrade warning ----

def test_lncrawl_is_not_offered_and_refused():
    reason = diagnostics.known_install_limitation_reason("lightnovel-crawler")
    assert reason and "not offered" in reason
    assert "lightnovel-crawler" not in svc.installable_packages()
    assert diagnostics.known_install_limitation_reason("jieba") is None


def test_lncrawl_install_is_refused_by_the_service(monkeypatch):
    monkeypatch.setattr(svc, "guard", lambda confirm: None)
    with pytest.raises(svc.AdminActionUnknownPackage):
        svc.install_dependency("lightnovel-crawler", confirm=True)


@pytest.mark.parametrize("have,warned", [("5.2.0", True), ("4.57.6", False), (None, False)])
def test_qwen_asr_warns_before_downgrading_transformers(monkeypatch, have, warned):
    monkeypatch.setattr(diagnostics, "get_installed_version",
                        lambda dist: have if dist == "transformers" else None)
    w = diagnostics.install_downgrade_warning("qwen-asr")
    assert (w is not None) is warned
    if warned:
        assert "5.2.0" in w and "4.57.6" in w
    assert diagnostics.install_downgrade_warning("jieba") is None


def test_dependency_install_refuses_a_not_offered_package(monkeypatch):
    monkeypatch.setattr(diagnostics, "stream_pip_install",
                        lambda *a, **k: pytest.fail("must not run pip"))
    items = list(diagnostics.stream_dependency_install("lightnovel-crawler"))
    assert items[-1]["done"] is True and items[-1]["ok"] is False
    assert "not offered" in items[0]["line"]


def test_python_version_limitation_is_a_warning_not_a_refusal(monkeypatch):
    monkeypatch.setattr(diagnostics, "known_install_limitation_reason",
                        lambda n: "known not to install on Python 3.14" if n == "audio-separator"
                        else None)
    monkeypatch.setattr(diagnostics, "check_dependency", lambda imp: False)
    info = svc.get_install_presets()["packages"]["audio-separator"]
    assert info["not_offered_reason"] is None and info["installable"] is True
    assert "3.14" in info["warning"]


# ---- Required / recommended / optional, and minimum versions ----

def test_task_roles_name_the_tasks_own_packages():
    for t in diagnostics.INSTALL_TASKS:
        rec, opt = set(t.get("recommended", ())), set(t.get("optional", ()))
        assert rec <= set(t["packages"]) and opt <= set(t["packages"]), t["id"]
        assert not rec & opt, t["id"]
        # every task needs at least one package it can't work without, unless
        # it is a set of interchangeable engines or independent extras
        required = [n for n in t["packages"] if diagnostics.task_package_role(t, n) == "required"]
        assert required or t["id"] in {"books", "paid_engines"}, t["id"]


def test_install_for_a_task_skips_optional_packages(monkeypatch):
    monkeypatch.setattr(diagnostics, "check_dependency", lambda imp: False)
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda dist: None)
    t = next(t for t in svc.get_install_presets()["tasks"] if t["id"] == "hardsub_ocr")
    assert t["roles"] == {"cv2": "required", "numpy": "required", "PIL": "required",
                          "pytesseract": "recommended", "paddleocr": "optional",
                          "paddlepaddle": "optional"}
    assert not {"paddleocr", "paddlepaddle"} & set(t["to_install"])
    assert t["optional_missing"] == ["paddleocr", "paddlepaddle"]
    assert set(t["required_missing"]) == {"cv2", "numpy", "PIL"}
    assert t["approx_mb"] == sum(diagnostics.approx_download_mb(n) for n in t["to_install"])


def test_required_min_versions_from_active_requirement_lines(tmp_path):
    (tmp_path / "requirements-core.txt").write_text(
        "requests>=2.32.2\nauthlib>=1.3,<2   # comment\n# jieba>=9.9\n")
    (tmp_path / "requirements-optional.txt").write_text("Sudachidict_Core>=20240716\n")
    assert diagnostics.required_min_versions(str(tmp_path)) == {
        "requests": "2.32.2", "authlib": "1.3", "sudachidict-core": "20240716"}


def test_real_requirements_give_known_minimums():
    mins = diagnostics.required_min_versions()
    assert mins["jieba"] == "0.42" and mins["opencv-python"] == "4.8.1.78"
    assert "paddleocr" not in mins               # commented out in requirements-optional.txt


@pytest.mark.parametrize("have,need,below", [
    ("0.41", "0.42", True), ("0.42.1", "0.42", False), ("2.11.0+cu128", "2.0", False),
    (None, "1.0", False), ("1.0", None, False), ("garbage", "1.0", False)])
def test_below_min_version(have, need, below):
    assert diagnostics.below_min_version(have, need) is below


def test_package_info_reports_min_version_and_below_min(monkeypatch):
    monkeypatch.setattr(diagnostics, "check_dependency", lambda imp: imp == "jieba")
    monkeypatch.setattr(diagnostics, "get_installed_version", {"jieba": "0.40"}.get)
    p = svc.get_install_presets()["packages"]
    assert p["jieba"]["min_version"] == "0.42" and p["jieba"]["below_min"] is True
    assert p["pypinyin"]["below_min"] is False


def test_install_commands_omit_constraints_when_file_is_absent(monkeypatch, tmp_path):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(svc, "default_project_root", lambda: str(tmp_path))
    ((cmd, _t),) = svc._install_commands("jieba")
    assert cmd[3:] == ["install", *FLAGS, "jieba"]
    assert all("-c" not in c for c, _t in svc._qwen_asr_fallback_commands("qwen-asr"))


def test_install_commands_include_constraints_when_file_exists(monkeypatch, tmp_path):
    import shutil
    (tmp_path / "constraints.txt").write_text("av<19\n")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(svc, "default_project_root", lambda: str(tmp_path))
    want = ["-c", str(tmp_path / "constraints.txt")]
    ((cmd, _t),) = svc._install_commands("jieba")
    assert cmd[-2:] == want
    for cmd, _t in svc._qwen_asr_fallback_commands("qwen-asr"):
        assert cmd[-2:] == want
