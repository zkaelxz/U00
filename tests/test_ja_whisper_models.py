"""tools/ja_whisper_models.py: the safety checks around Japanese Whisper fine-tunes.

Fakes only: no network, no model weights, no optional library.
"""

import hashlib
import importlib.util
import json
import re
import types
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ja_whisper_models", ROOT / "tools" / "ja_whisper_models.py")
jw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jw)

EXPECTED_SET = ["config.json", "model.bin", "tokenizer.json", "vocabulary.json",
                "preprocessor_config.json", "README.md", ".gitattributes"]


def fake_model_bin(path, decoder_layers=2):
    names = [f"decoder/layer_{i}/self_attention/linear_0/weight" for i in range(decoder_layers)]
    path.write_bytes(b"encoder/layer_31/x\0" + b"\0".join(n.encode() for n in names))


def make_folder(tmp_path, heads, decoder_layers=2, extra=()):
    folder = tmp_path / "model"
    folder.mkdir()
    fake_model_bin(folder / "model.bin", decoder_layers)
    (folder / "config.json").write_text(json.dumps({"alignment_heads": heads}), encoding="utf-8")
    for name in ("tokenizer.json", "vocabulary.json", "preprocessor_config.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    for name in extra:
        (folder / name).write_text("x", encoding="utf-8")
    return folder


class TestAllowlist:
    @pytest.mark.parametrize("bad", ["modeling.py", "weights.pt", "weights.pth", "data.pkl",
                                     "pytorch_model.bin", "model.ckpt", "sub/model.bin"])
    def test_refuses_and_names_the_file(self, bad):
        with pytest.raises(jw.ToolError) as err:
            jw.refuse_unexpected_files(EXPECTED_SET + [bad])
        assert bad in str(err.value)

    def test_accepts_the_expected_set(self):
        jw.refuse_unexpected_files(EXPECTED_SET + ["LICENSE", "vocabulary.txt"])

    def test_fetch_exits_nonzero_and_downloads_nothing(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(jw, "list_repo_files", lambda repo, rev: [("model.bin", 1), ("evil.py", 1)])
        calls = []
        monkeypatch.setattr(jw, "download_files", lambda *a: calls.append(a))
        code = jw.main(["fetch", "kotoba", "--out", str(tmp_path / "out")])
        assert code != 0 and calls == []
        assert "evil.py" in capsys.readouterr().err

    def test_anime_selection_refuses_pickle_weights(self):
        with pytest.raises(jw.ToolError, match="pytorch_model.bin"):
            jw.select_anime_files(["model.safetensors", "pytorch_model.bin", "config.json"])

    def test_anime_selection_needs_safetensors(self):
        with pytest.raises(jw.ToolError, match="safetensors"):
            jw.select_anime_files(["config.json", "vocab.json"])

    def test_anime_selection_skips_images_and_keeps_tokenizer_files(self):
        names = ["model.safetensors", "config.json", "vocab.json", "merges.txt", "figs/cer_ngram5.png"]
        assert jw.select_anime_files(names) == ["config.json", "merges.txt", "model.safetensors", "vocab.json"]


class FakeRepo:
    """Stands in for the hub: a listing, and a download that writes a stock-config folder."""

    def __init__(self, monkeypatch, heads=((7, 0),), model_sha="auto"):
        self.downloads = []
        self.heads = [list(h) for h in heads]
        self.model_sha = model_sha
        monkeypatch.setattr(jw, "list_repo_files", self.list)
        monkeypatch.setattr(jw, "download_files", self.download)

    def list(self, repo, revision):
        names = ["config.json", "model.bin", "tokenizer.json", "vocabulary.json", "preprocessor_config.json"]
        sha = hashlib.sha256(self._bin_bytes()).hexdigest() if self.model_sha == "auto" else self.model_sha
        return [(n, sha if n == "model.bin" else None) for n in names]

    @staticmethod
    def _bin_bytes():
        return b"decoder/layer_0/x\0decoder/layer_1/x"

    def download(self, repo, revision, names, dest):
        self.downloads.append((repo, revision, list(names)))
        Path(dest).mkdir(parents=True, exist_ok=True)
        (Path(dest) / "model.bin").write_bytes(self._bin_bytes())
        (Path(dest) / "config.json").write_text(json.dumps({"alignment_heads": self.heads}), encoding="utf-8")
        for name in ("tokenizer.json", "vocabulary.json", "preprocessor_config.json"):
            (Path(dest) / name).write_text("{}", encoding="utf-8")


class TestFetchCommand:
    def test_kotoba_with_the_stock_config_is_patched_and_succeeds(self, monkeypatch, tmp_path, capsys):
        FakeRepo(monkeypatch)
        out = tmp_path / "kotoba"
        assert jw.main(["fetch", "kotoba", "--out", str(out)]) == 0
        assert json.loads((out / "config.json").read_text())["alignment_heads"] == [[1, h] for h in range(20)]
        assert json.loads((out / "config.json.orig").read_text())["alignment_heads"] == [[7, 0]]
        assert "alignment_heads" in capsys.readouterr().out

    def test_ja_1_5b_with_bad_heads_is_not_silently_patched(self, monkeypatch, tmp_path):
        FakeRepo(monkeypatch)
        out = tmp_path / "ja"
        assert jw.main(["fetch", "ja-1.5b", "--out", str(out)]) == 1
        assert not (out / "config.json.orig").exists()

    def test_a_model_bin_that_does_not_match_the_listing_is_refused(self, monkeypatch, tmp_path, capsys):
        FakeRepo(monkeypatch, model_sha="0" * 64)
        assert jw.main(["fetch", "kotoba", "--out", str(tmp_path / "k")]) == 1
        assert "sha256" in capsys.readouterr().err

    def test_a_listing_without_a_checksum_still_downloads(self, monkeypatch, tmp_path):
        FakeRepo(monkeypatch, model_sha=None)
        assert jw.main(["fetch", "kotoba", "--out", str(tmp_path / "k")]) == 0


class TestConvertCommand:
    def test_refuses_an_existing_output_before_downloading(self, monkeypatch, tmp_path, capsys):
        repo = FakeRepo(monkeypatch)
        out = tmp_path / "anime"
        out.mkdir()
        assert jw.main(["convert-anime-whisper", "--out", str(out)]) == 1
        assert repo.downloads == []
        assert "must not exist" in capsys.readouterr().err

    def test_converter_is_built_without_remote_code(self, monkeypatch, tmp_path):
        names = ["model.safetensors", "config.json", "preprocessor_config.json", "vocab.json"]
        monkeypatch.setattr(jw, "list_repo_files", lambda r, v: [(n, None) for n in names])

        def download(repo, revision, wanted, dest):
            for n in wanted:
                (Path(dest) / n).write_text("{}", encoding="utf-8")
            (Path(dest) / "tokenizer.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(jw, "download_files", download)
        seen = {}

        class FakeConverter:
            def __init__(self, src, **kwargs):
                seen.update(kwargs)

            def convert(self, out, quantization=None):
                Path(out).mkdir(parents=True)
                fake_model_bin(Path(out) / "model.bin")
                for n in ("config.json", "vocabulary.json"):
                    (Path(out) / n).write_text(json.dumps({"alignment_heads": [[7, 0]]}), encoding="utf-8")
                seen["quantization"] = quantization

        class FakeTokenizer:
            def save_pretrained(self, dest):
                pass
        sys_modules = {
            "transformers": types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(
                from_pretrained=lambda *a, **k: FakeTokenizer())),
            "ctranslate2": types.ModuleType("ctranslate2"),
            "ctranslate2.converters": types.SimpleNamespace(TransformersConverter=FakeConverter),
        }
        for name, mod in sys_modules.items():
            monkeypatch.setitem(__import__("sys").modules, name, mod)
        out = tmp_path / "anime"
        assert jw.main(["convert-anime-whisper", "--out", str(out), "--quant", "int8"]) == 0
        assert seen["trust_remote_code"] is False and seen["quantization"] == "int8"
        assert json.loads((out / "config.json").read_text())["alignment_heads"] == [[1, h] for h in range(20)]
        assert json.loads((out / "baihe-manifest.json").read_text())["quantization"] == "int8"


class TestAlignmentHeads:
    def test_patch_writes_heads_and_keeps_a_backup(self, tmp_path):
        folder = make_folder(tmp_path, [[7, 0], [10, 17]])
        jw.patch_alignment_heads(folder)
        assert json.loads((folder / "config.json").read_text())["alignment_heads"] == [[1, h] for h in range(20)]
        assert json.loads((folder / "config.json.orig").read_text())["alignment_heads"] == [[7, 0], [10, 17]]

    def test_second_patch_keeps_the_first_backup(self, tmp_path):
        folder = make_folder(tmp_path, [[7, 0]])
        jw.patch_alignment_heads(folder)
        jw.patch_alignment_heads(folder)
        assert json.loads((folder / "config.json.orig").read_text())["alignment_heads"] == [[7, 0]]


class TestManifest:
    def test_has_the_provenance_and_no_path(self, tmp_path):
        folder = make_folder(tmp_path, [[1, 0]])
        jw.write_manifest(folder, jw.ANIME_REPO, jw.ANIME_REVISION, "int8", {"ctranslate2": "4.7.1"})
        text = (folder / "baihe-manifest.json").read_text()
        manifest = json.loads(text)
        assert manifest["source_repo"] == "litagin/anime-whisper"
        assert manifest["source_revision"] == jw.ANIME_REVISION
        assert manifest["quantization"] == "int8"
        assert manifest["tool"] and manifest["libraries"] == {"ctranslate2": "4.7.1"}
        assert re.fullmatch(r"[0-9a-f]{64}", manifest["model_bin_sha256"])
        assert str(tmp_path) not in text and "\\\\" not in text


class TestPinnedRevisions:
    @pytest.mark.parametrize("revision", [jw.JA_1_5B_REVISION, jw.KOTOBA_REVISION, jw.ANIME_REVISION])
    def test_is_a_full_sha(self, revision):
        assert re.fullmatch(r"[0-9a-f]{40}", revision)

    def test_ja_1_5b_is_the_reviewed_commit(self):
        assert jw.JA_1_5B_REVISION.startswith("1527314")


class TestCheck:
    def test_decoder_layers_counted_across_read_boundaries(self, tmp_path):
        fake_model_bin(tmp_path / "model.bin", decoder_layers=12)
        assert jw.decoder_layer_count(tmp_path / "model.bin", chunk_size=7) == 12

    def test_flags_heads_past_the_decoder(self, tmp_path):
        folder = make_folder(tmp_path, [[7, 0], [1, 3]], decoder_layers=2)
        problems, _ = jw.inspect_folder(folder)
        assert any("alignment_heads" in p and "[7]" in p for p in problems)

    def test_accepts_the_patched_config(self, tmp_path):
        folder = make_folder(tmp_path, jw.ALIGNMENT_HEADS, decoder_layers=2)
        assert jw.inspect_folder(folder) == ([], [])

    @pytest.mark.parametrize("stray", ["modeling.py", "weights.pt", "data.pkl"])
    def test_flags_unexpected_files(self, tmp_path, stray):
        folder = make_folder(tmp_path, jw.ALIGNMENT_HEADS, extra=[stray])
        problems, _ = jw.inspect_folder(folder)
        assert any(stray in p for p in problems)

    def test_flags_a_missing_file(self, tmp_path):
        folder = make_folder(tmp_path, jw.ALIGNMENT_HEADS)
        (folder / "tokenizer.json").unlink()
        problems, _ = jw.inspect_folder(folder)
        assert any("tokenizer.json" in p for p in problems)

    def test_hub_cache_folder_and_our_files_are_not_unexpected(self, tmp_path):
        folder = make_folder(tmp_path, jw.ALIGNMENT_HEADS, extra=["config.json.orig", "baihe-manifest.json"])
        (folder / ".cache").mkdir()
        assert jw.inspect_folder(folder)[0] == []

    def test_check_command_stops_before_loading_a_bad_folder(self, monkeypatch, tmp_path, capsys):
        folder = make_folder(tmp_path, [[7, 0]])
        monkeypatch.setattr(jw, "transcribe_probe", lambda *a: pytest.fail("model was loaded"))
        assert jw.main(["check", str(folder)]) == 1
        assert "alignment_heads" in capsys.readouterr().out

    def test_check_command_reports_a_crash(self, monkeypatch, tmp_path, capsys):
        folder = make_folder(tmp_path, jw.ALIGNMENT_HEADS)

        def boom(*a):
            raise RuntimeError("bad model")
        monkeypatch.setattr(jw, "transcribe_probe", boom)
        assert jw.main(["check", str(folder)]) == 1
        assert "CRASHED: bad model" in capsys.readouterr().out

    def test_check_command_passes_and_never_sends_a_prompt(self, monkeypatch, tmp_path, capsys):
        folder = make_folder(tmp_path, jw.ALIGNMENT_HEADS)
        word = types.SimpleNamespace(start=0.1, end=0.5, word="a")
        seg = types.SimpleNamespace(start=0.0, end=1.0, words=[word])
        info = types.SimpleNamespace(language="ja", language_probability=0.9)
        monkeypatch.setattr(jw, "transcribe_probe", lambda *a: ([seg], info))
        assert jw.main(["check", str(folder)]) == 0
        out = capsys.readouterr().out
        assert "Segments: 1, words: 1" in out and "Language token: ja" in out
        assert "initial prompt" in out

    def test_word_timing_problems(self):
        seg = types.SimpleNamespace(start=1.0, end=2.0, words=[
            types.SimpleNamespace(start=1.2, end=1.2, word="flat"),
            types.SimpleNamespace(start=0.5, end=1.5, word="early"),
            types.SimpleNamespace(start=1.1, end=1.9, word="fine"),
        ])
        found = jw.word_timing_problems([seg])
        assert len(found) == 2 and "flat" in found[0] and "early" in found[1]

    def test_synthesized_wav_is_valid(self, tmp_path):
        jw.make_test_wav(tmp_path / "t.wav")
        with wave.open(str(tmp_path / "t.wav")) as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1
            assert w.getnframes() == 24000
