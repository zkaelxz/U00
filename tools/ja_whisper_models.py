"""Prepare Japanese Whisper fine-tunes for the Benchmark Lab's offline-model-folder arm.

    venv\\Scripts\\python.exe tools\\ja_whisper_models.py fetch ja-1.5b --out DIR
    venv\\Scripts\\python.exe tools\\ja_whisper_models.py fetch kotoba --out DIR
    venv\\Scripts\\python.exe tools\\ja_whisper_models.py convert-anime-whisper --out DIR [--quant float16|int8]
    venv\\Scripts\\python.exe tools\\ja_whisper_models.py check DIR [--wav FILE]

Only weight formats that cannot run code are ever downloaded or loaded
(CTranslate2 model.bin, safetensors), always at a pinned commit. Run it on the
owner's PC; nothing here installs a package. See "Trying a Japanese fine-tune" in
docs/asr-experiments.md.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Both import only the standard library, so `--help` still starts without the app's packages.
from lib.ct2_repo import CT2_ALLOWED, CT2_REQUIRED, RepoFilesRefused, refuse_unexpected_files as _refuse_ct2
from whisper_models import PINNED_CT2_REPOS

# huggingface_hub reads these when it is imported (every import in this file is
# lazy and comes later). Without them a stalled connection can hang a call forever.
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")

TOOL_NAME = "tools/ja_whisper_models.py"

# Full commit shas, not branch names or short prefixes: a repo owner can move a
# branch (or push a new file next to the weights) after this tool was reviewed,
# and a download at a sha always returns the files that were looked at.
JA_1_5B_REPO, JA_1_5B_REVISION = PINNED_CT2_REPOS["ja-1.5b"]
KOTOBA_REPO = "kotoba-tech/kotoba-whisper-v2.0-faster"
KOTOBA_REVISION = "f44edd35eaeb2274e85ac7b31fb2c6f59ff1c4bc"
ANIME_REPO = "litagin/anime-whisper"
ANIME_REVISION = "22e2008a8182b357da3922a6308d095008f72973"

CT2_SOURCES = {
    "ja-1.5b": (JA_1_5B_REPO, JA_1_5B_REVISION),
    "kotoba": (KOTOBA_REPO, KOTOBA_REVISION),
}

# Written by this tool next to the model, so `check` must not call them unexpected.
OUR_FILES = frozenset({"config.json.orig", "baihe-manifest.json"})

# Transformers repo: weights as safetensors plus the tokenizer and config text files.
ANIME_ALLOWED = frozenset({
    "model.safetensors", "config.json", "generation_config.json", "preprocessor_config.json",
    "tokenizer_config.json", "tokenizer.json", "vocab.json", "merges.txt", "added_tokens.json",
    "special_tokens_map.json", "normalizer.json", "README.md", "LICENSE", ".gitattributes",
})
# Anything that can execute code when loaded. Seen in a repo, it is a refusal, not a skip.
DANGEROUS_SUFFIXES = (".py", ".pt", ".pth", ".pkl", ".pickle", ".ckpt", ".bin", ".npy", ".npz", ".joblib")

# The 2-layer decoder of the kotoba family has no layer 7-25; the stock heads
# (inherited from large-v3) make CTranslate2 write out of bounds when word
# timestamps are on. Layer 1 with heads 0-19 is the community workaround
# (kotoba-tech/kotoba-whisper issue 2), not a calibrated choice.
ALIGNMENT_HEADS = [[1, h] for h in range(20)]

NO_PROMPT_NOTE = ("Do not set an initial prompt for anime-whisper: its model card says a prompt "
                  "degrades the output. This tool never sends one.")

NEXT_STEPS = """What to do next (all on the PC):
  1. Settings > Offline Whisper model folder: set it to the folder above.
  2. In the Workspace, transcribe ONE 30 second clip first. A crash there only kills the
     worker, not the app.
  3. Benchmark Lab: Transcription stage, add an arm named local-folder and run it.
  4. Clear the Offline Whisper model folder setting afterwards, or every title keeps using it.
Personal local benchmarking only: see the licence notes in docs/asr-experiments.md."""


class ToolError(Exception):
    """A problem the owner can act on; printed without a traceback."""


def _redact(text):
    # Imported here: the tool must start (and --help work) without the app's imports.
    sys.path.insert(0, str(ROOT))
    from translate_engines import redact_secrets
    return redact_secrets(str(text))


def _fail(message):
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


# --- file lists --------------------------------------------------------------

def refuse_unexpected_files(names, allowed=CT2_ALLOWED):
    try:
        _refuse_ct2(names, allowed)
    except RepoFilesRefused as exc:
        raise ToolError(f"{exc}. Nothing was downloaded. "
                        "Do not use this repository without reviewing it.") from exc


def select_anime_files(names):
    """Files to download from the transformers repo; refuses code-bearing formats."""
    for name in names:
        if name.lower().endswith(DANGEROUS_SUFFIXES):
            raise ToolError(f"the repository contains '{name}', a format that can run code when "
                            "loaded. Nothing was downloaded.")
    if "model.safetensors" not in names:
        raise ToolError("the repository has no model.safetensors, so there is no weight file "
                        "that is safe to load. Nothing was downloaded.")
    return sorted(n for n in names if n in ANIME_ALLOWED)


def list_repo_files(repo, revision):
    from huggingface_hub import HfApi
    info = HfApi().model_info(repo, revision=revision, files_metadata=True, timeout=30)
    # The LFS sha256 lets fetch confirm the weights on disk are the ones listed.
    return [(s.rfilename, s.lfs.sha256 if s.lfs else None) for s in info.siblings]


def download_files(repo, revision, names, dest):
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo, revision=revision, allow_patterns=list(names), local_dir=str(dest))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def print_files(folder):
    for path in sorted(Path(folder).iterdir()):
        if path.is_file():
            print(f"  {path.name}  {path.stat().st_size:,} bytes")
    model_bin = Path(folder) / "model.bin"
    if model_bin.exists():
        print(f"  model.bin sha256: {sha256_file(model_bin)}")


# --- config and model inspection ---------------------------------------------

def patch_alignment_heads(folder):
    config_path = Path(folder) / "config.json"
    backup = Path(folder) / "config.json.orig"
    if not backup.exists():
        shutil.copy2(config_path, backup)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["alignment_heads"] = ALIGNMENT_HEADS
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


_LAYER_NAME = re.compile(rb"decoder/layer_(\d+)/")


def decoder_layer_count(model_bin, chunk_size=1 << 24):
    """Decoder depth read from the variable names inside model.bin (0 if none found).

    config.json does not record it, and a head past the last layer is what makes
    CTranslate2 crash, so the names in the weights are the only local evidence.
    """
    highest = -1
    carry = b""
    with open(model_bin, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            data = carry + block
            for match in _LAYER_NAME.finditer(data):
                highest = max(highest, int(match.group(1)))
            # A name split across two reads is completed by the overlap.
            carry = data[-32:]
    return highest + 1


def inspect_folder(folder):
    """(problems, warnings) for a model folder; no model is loaded."""
    folder = Path(folder)
    problems, warnings = [], []
    if not folder.is_dir():
        return [f"'{folder.name}' is not a folder"], warnings
    present = set()
    for path in sorted(folder.iterdir()):
        if path.name == ".cache" and path.is_dir():
            continue  # huggingface_hub's download bookkeeping
        if path.is_dir() or path.name not in CT2_ALLOWED | OUR_FILES:
            problems.append(f"unexpected {'folder' if path.is_dir() else 'file'}: {path.name}")
        else:
            present.add(path.name)
    for name in CT2_REQUIRED:
        if name not in present:
            problems.append(f"missing file: {name}")
    if not present & {"vocabulary.json", "vocabulary.txt"}:
        problems.append("missing file: vocabulary.json or vocabulary.txt")

    if "config.json" in present:
        try:
            heads = json.loads((folder / "config.json").read_text(encoding="utf-8")).get("alignment_heads")
        except (OSError, ValueError) as exc:
            problems.append(f"config.json cannot be read: {_redact(exc)}")
            heads = None
        if heads and "model.bin" in present:
            layers = decoder_layer_count(folder / "model.bin")
            if layers == 0:
                warnings.append("could not read the decoder layer count from model.bin, so "
                                "alignment_heads were not checked against it")
            else:
                past = sorted({h[0] for h in heads if h[0] >= layers})
                if past:
                    problems.append(
                        f"alignment_heads point at decoder layer(s) {past} but the decoder has "
                        f"only {layers} layer(s) (0-{layers - 1}). Word timestamps will crash the "
                        "worker. For kotoba, run `fetch kotoba` again to patch config.json.")
    return problems, warnings


def write_manifest(folder, repo, revision, quantization, versions):
    manifest = {
        "source_repo": repo,
        "source_revision": revision,
        "tool": TOOL_NAME,
        "libraries": versions,
        "quantization": quantization,
        "model_bin_sha256": sha256_file(Path(folder) / "model.bin"),
    }
    (Path(folder) / "baihe-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def library_versions(*names):
    from importlib import metadata
    out = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "not installed"
    return out


# --- check: load and transcribe ----------------------------------------------

def make_test_wav(path, sample_rate=16000):
    """One second of silence then half a second of a 440 Hz tone; a crash test only."""
    frames = bytearray(b"\x00\x00" * sample_rate)
    for i in range(sample_rate // 2):
        frames += struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(frames))


def word_timing_problems(segments, tolerance=0.02):
    problems = []
    for seg in segments:
        for word in seg.words or []:
            if word.start >= word.end:
                problems.append(f"word '{word.word}' has start {word.start} >= end {word.end}")
            elif word.start < seg.start - tolerance or word.end > seg.end + tolerance:
                problems.append(f"word '{word.word}' ({word.start}-{word.end}) lies outside its "
                                f"segment ({seg.start}-{seg.end})")
    return problems


def transcribe_probe(folder, wav):
    from faster_whisper import WhisperModel
    model = WhisperModel(str(folder), device="auto", compute_type="default")
    segments, info = model.transcribe(str(wav), word_timestamps=True, condition_on_previous_text=False)
    return list(segments), info


# --- subcommands -------------------------------------------------------------

def cmd_fetch(args):
    repo, revision = CT2_SOURCES[args.model]
    try:
        files = list_repo_files(repo, revision)
        refuse_unexpected_files([name for name, _ in files])
        listed_sha = dict(files).get("model.bin")
        missing = [n for n in CT2_REQUIRED if n not in {name for name, _ in files}]
        if missing:
            raise ToolError(f"the repository at the pinned revision lacks {', '.join(missing)}")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {repo} at {revision} ...")
        download_files(repo, revision, [name for name, _ in files], out)
        if listed_sha and sha256_file(out / "model.bin") != listed_sha:
            raise ToolError("model.bin does not match the sha256 the repository lists for it. "
                            "Delete the folder and try again.")
        # Before the check: the stock kotoba heads are exactly what the check rejects.
        if args.model == "kotoba":
            patch_alignment_heads(out)
        problems, _ = inspect_folder(out)
        if problems:
            raise ToolError("the downloaded folder failed its own check: " + "; ".join(problems))
        print("Files:")
        print_files(out)
        if args.model == "kotoba":
            print("\nPatched config.json: alignment_heads set to layer 1, heads 0-19 "
                  "(original saved as config.json.orig).\nWhy: the stock config points at decoder "
                  "layers that do not exist in this 2-layer model and crashes with word timestamps; "
                  "this is the community workaround, not a vendor fix.")
    except ToolError as exc:
        return _fail(exc)
    except Exception as exc:
        return _fail(f"download failed: {_redact(exc)}")
    print(f"\nDone: {args.model} is in the folder you named. Only CTranslate2 files were fetched.")
    print(NEXT_STEPS)
    return 0


def cmd_convert_anime(args):
    out = Path(args.out)
    try:
        if out.exists():
            raise ToolError("--out must not exist yet (CTranslate2 refuses an existing output folder, "
                            "and this check saves a 3 GB download).")
        files = list_repo_files(ANIME_REPO, ANIME_REVISION)
        wanted = select_anime_files([name for name, _ in files])
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="baihe-anime-src-", dir=out.parent) as src:
            print(f"Downloading {ANIME_REPO} at {ANIME_REVISION} (about 3 GB) ...")
            download_files(ANIME_REPO, ANIME_REVISION, wanted, src)
            # Only safetensors weights are in src, so loading cannot unpickle anything,
            # and remote code stays off.
            from transformers import AutoTokenizer
            AutoTokenizer.from_pretrained(src, trust_remote_code=False).save_pretrained(src)
            from ctranslate2.converters import TransformersConverter
            print(f"Converting to CTranslate2 ({args.quant}) ...")
            TransformersConverter(src, trust_remote_code=False, low_cpu_mem_usage=True).convert(
                str(out), quantization=args.quant)
            for name in ("tokenizer.json", "preprocessor_config.json"):
                shutil.copy2(Path(src) / name, out / name)
        patch_alignment_heads(out)
        versions = library_versions("ctranslate2", "transformers", "tokenizers", "huggingface_hub")
        write_manifest(out, ANIME_REPO, ANIME_REVISION, args.quant, versions)
        problems, _ = inspect_folder(out)
        if problems:
            raise ToolError("the converted folder failed its own check: " + "; ".join(problems))
        print("Files:")
        print_files(out)
    except ToolError as exc:
        return _fail(exc)
    except Exception as exc:
        return _fail(f"conversion failed: {_redact(exc)}")
    print("\nalignment_heads set to layer 1, heads 0-19 to match the 2-layer decoder "
          "(the converter inherits large-v3 heads that do not exist there).")
    print(NO_PROMPT_NOTE)
    print(NEXT_STEPS)
    return 0


def cmd_check(args):
    folder = Path(args.folder)
    problems, warnings = inspect_folder(folder)
    for line in warnings:
        print(f"warning: {line}")
    for line in problems:
        print(f"PROBLEM: {line}")
    if problems:
        print("\nThe folder failed its checks; the model was not loaded.")
        return 1
    print("Folder check passed: expected files only, alignment_heads fit the decoder.")

    synthetic = not args.wav
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(args.wav) if args.wav else Path(tmp) / "probe.wav"
        if synthetic:
            make_test_wav(wav)
            print("No --wav given: using 1 s of silence plus a short tone. That is only a crash "
                  "test; it says nothing about accuracy. Use a real 30 s clip for timing.")
        try:
            segments, info = transcribe_probe(folder, wav)
        except Exception as exc:
            print(f"CRASHED: {_redact(exc)}")
            return 1
    words = [w for seg in segments for w in (seg.words or [])]
    print("No crash.")
    print(f"Segments: {len(segments)}, words: {len(words)}")
    print(f"Language token: {info.language} (probability {info.language_probability:.2f})")
    timing = word_timing_problems(segments)
    for line in timing[:10]:
        print(f"PROBLEM: {line}")
    if len(timing) > 10:
        print(f"... and {len(timing) - 10} more")
    print("Word timings: " + ("bad (see above)" if timing else "no word with start >= end or outside its segment"))
    if not words and not synthetic:
        print("PROBLEM: no words came back, so word timestamps were not exercised.")
        timing = timing or ["no words"]
    print(NO_PROMPT_NOTE)
    print(NEXT_STEPS)
    return 1 if timing else 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    fetch = sub.add_parser("fetch", help="download a pinned CTranslate2 build")
    fetch.add_argument("model", choices=sorted(CT2_SOURCES))
    fetch.add_argument("--out", required=True)
    fetch.set_defaults(func=cmd_fetch)
    conv = sub.add_parser("convert-anime-whisper", help="download anime-whisper and convert it")
    conv.add_argument("--out", required=True)
    conv.add_argument("--quant", choices=("float16", "int8"), default="float16")
    conv.set_defaults(func=cmd_convert_anime)
    check = sub.add_parser("check", help="verify a model folder and try it on a clip")
    check.add_argument("folder")
    check.add_argument("--wav")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
