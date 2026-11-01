"""tools/translate_context_bench.py: dry run, spend gate, nothing saved, report content, redaction."""
import inspect
from engine_backends import engine_registry, pricing
import json
import re
from types import SimpleNamespace

import pytest

import db
from core import Line
from engine_backends.claude import ClaudeEngine
from tests.saved_settings import patch_setting
from tools import translate_context_bench as bench

FAKE_KEY = "sk-ant-api03-FAKEKEYVALUE1234567890abcdef"
LINE_COUNT = 40
WRITER_PREFIXES = ("save_", "update_", "create_", "delete_", "add_", "set_", "insert_", "remove_",
                   "replace_", "upsert_", "import_", "clear_", "reset_", "mark_", "record_", "rename_",
                   "merge_", "restore_", "log_")


class FakeMessages:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **kwargs):
        owner = self.owner
        owner.calls += 1
        if owner.error:
            raise RuntimeError(owner.error)
        user = kwargs["messages"][0]["content"]
        numbered = user.split("Translate these lines:\n\n", 1)[-1]
        pairs = re.findall(r"^(\d+)\. (?:\[[^\]]*\] )?(.*)$", numbered, re.MULTILINE)
        # A real model sometimes skips a line; the retry must ask for just that id.
        if owner.drop_first_id_once and len(pairs) > 1:
            owner.drop_first_id_once = False
            pairs = pairs[1:]
        window = len(owner.window_sizes)
        owner.window_sizes.append(len(user))
        answers = {i: f"{owner.tag} Mei said: {zh} (call {window})" for i, zh in pairs}
        text = json.dumps(answers)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=500,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                               stop_reason="end_turn", usage=usage)


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.error = None
        self.drop_first_id_once = False
        self.tag = "she"
        self.window_sizes = []
        self.messages = FakeMessages(self)


class FakeClaude(ClaudeEngine):
    """The real engine and prompt builder over a fake client: no SDK, no network."""

    def __init__(self, api_key, model="claude-sonnet-5-5", client=None):
        self.client = client
        self.model = model
        from engine_backends.shared import _empty_usage
        self.last_usage = _empty_usage()


@pytest.fixture
def live(monkeypatch):
    """One shared fake client for the whole run, so tests can count real calls."""
    client = FakeClient()
    monkeypatch.setattr(engine_registry, "get_engine",
                        lambda name, key, model=None, **kw: FakeClaude(key, model or "claude-sonnet-5-5", client))
    monkeypatch.setattr(bench.translate_service, "resolve_api_key", lambda name: FAKE_KEY)
    monkeypatch.setattr("engine_backends.shared.time.sleep", lambda s: None)
    return client


@pytest.fixture
def title(isolated_db):
    did = db.create_drama(title_zh="D", translation_engine="claude")
    db.save_lines(did, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1, zh=f"你好{i}", en=f"old {i}",
                             speaker="SPEAKER_00" if i % 2 else None)
                        for i in range(LINE_COUNT)])
    db.upsert_character(did, "SPEAKER_00", character_name="Mei", pronouns="she/her")
    return did


@pytest.fixture
def writes(monkeypatch):
    """Every db function that looks like a writer, recorded and refused except log_usage."""
    calls = []
    for name, fn in list(vars(db).items()):
        if (inspect.isfunction(fn) and fn.__module__.startswith("db")
                and name.startswith(WRITER_PREFIXES)):
            def spy(*a, _name=name, _fn=fn, **kw):
                calls.append((_name, a, kw))
                if _name != "log_usage":
                    raise AssertionError(f"the bench called db.{_name}")
                return _fn(*a, **kw)
            monkeypatch.setattr(db, name, spy)
    return calls


def _lines(did):
    return [(r["id"], r["idx"], r["zh"], r["en"], r["flag"]) for r in db.load_lines(did)]


def test_dry_run_makes_no_calls_and_writes_nothing(title, live, writes, capsys, tmp_path):
    out = tmp_path / "r.md"
    before = _lines(title)
    assert bench.main([str(title), "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert live.calls == 0 and writes == [] and not out.exists()
    assert _lines(title) == before
    assert "Dry run only" in text
    for label in ("batch 30, ctx 10/6", "batch 60, ctx 10/6", "batch 60, ctx 20/10"):
        assert label in text
    assert re.search(r"total\s+\d+\s+\d+\s+[\d.]+", text)


def test_refuses_above_max_usd_before_any_call(title, live, writes, capsys, tmp_path):
    out = tmp_path / "r.md"
    assert bench.main([str(title), "--yes", "--max-usd", "0.0001", "--out", str(out)]) == 2
    assert "above the" in capsys.readouterr().out
    assert live.calls == 0 and writes == [] and not out.exists()


def test_refuses_when_monthly_cap_is_used_up(title, live, writes, capsys, tmp_path, monkeypatch):
    db.log_usage(None, "claude", "claude-sonnet-5-5", "translate", 1, 1, 5.0)
    patch_setting(monkeypatch, "monthly_cap_usd", 1.0)
    assert bench.main([str(title), "--yes", "--out", str(tmp_path / "r.md")]) == 2
    assert "spending cap" in capsys.readouterr().out
    assert live.calls == 0


def test_live_run_saves_nothing_but_usage_and_reports(title, live, writes, capsys, tmp_path):
    out = tmp_path / "r.md"
    before = _lines(title)
    live.drop_first_id_once = True
    rc = bench.main([str(title), "--yes", "--out", str(out), "--config", "10:4/2",
                     "--config", "20:8/4", "--edge", "2"])
    assert rc == 0
    assert _lines(title) == before
    assert {name for name, *_ in writes} == {"log_usage"}
    assert all(a[0] is None and a[3] == "context_bench" for _, a, _kw in writes)

    report = out.read_text(encoding="utf-8")
    assert report.splitlines()[0] == bench.REPORT_TITLE
    row = next(r for r in report.splitlines() if r.startswith("| batch 10, ctx 4/2 |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    # calls, batches, retry calls, failed calls, ids once, missing, duplicate, extra
    assert cells[1:9] == ["5", "4", "1", "0", "40", "0", "0", "0"]
    assert "Lines whose English differs between configs" in report
    assert re.search(r"^\| \d+ \| E \|", report, re.MULTILINE)
    assert "| Mei |" in report
    assert FAKE_KEY not in report and FAKE_KEY not in capsys.readouterr().out


def test_edge_lines_come_from_baseline_batches():
    rec = bench.Recorder()
    for ids in ([1, 2, 3, 4, 5, 6], [7, 8, 9, 10], [3, 4]):  # last is a re-sent subset
        rec.begin(ids)
        rec.end()
    assert bench.edge_ids(rec, 2) == {1, 2, 5, 6, 7, 8, 9, 10}


def test_id_counts_cover_missing_duplicate_and_extra():
    rec = bench.Recorder()
    rec.begin([1, 2, 3, 4])
    rec.on_reply('{"1": "a", "2": "b", "2": "b again", "9": "x"}')
    rec.end()
    s = rec.stats
    assert (s.once, s.missing, s.duplicate, s.extra, s.calls) == (1, 2, 1, 1, 1)


def test_error_text_with_a_key_never_reaches_output(title, live, writes, capsys, tmp_path):
    live.error = f"401 invalid x-api-key {FAKE_KEY}"
    out = tmp_path / "r.md"
    bench.main([str(title), "--yes", "--out", str(out), "--config", "10:2/2"])
    shown = capsys.readouterr().out
    assert FAKE_KEY not in shown and FAKE_KEY not in out.read_text(encoding="utf-8")
    assert "REDACTED" in out.read_text(encoding="utf-8")


def test_out_refuses_a_file_that_is_not_a_report(title, live, writes, capsys, tmp_path):
    env = tmp_path / ".env"
    env.write_text("CLAUDE_API_KEY=keep-me\n", encoding="utf-8")
    assert bench.main([str(title), "--yes", "--out", str(env)]) == 2
    assert env.read_text(encoding="utf-8") == "CLAUDE_API_KEY=keep-me\n"
    assert live.calls == 0 and writes == []
    # An earlier report may be replaced.
    old = tmp_path / "old.md"
    old.write_text(bench.REPORT_TITLE + "\nold\n", encoding="utf-8")
    assert bench.main([str(title), "--yes", "--out", str(old), "--config", "10:2/2"]) == 0
    assert old.read_text(encoding="utf-8").splitlines()[1:2] != ["old"]


def test_report_has_no_paths_or_urls(title, live, tmp_path):
    db.save_lines(title, [Line(idx=0, start=0, end=1, zh="看 https://example.com/a?k=1", en="see")],
                  fields=("zh",))
    out = tmp_path / "r.md"
    assert bench.main([str(title), "--yes", "--out", str(out), "--config", "10:2/2",
                       "--config", "10:2/2"]) == 0
    report = out.read_text(encoding="utf-8")
    assert "http" not in report and str(tmp_path) not in report
    assert "batch 10, ctx 2/2 #2" in report


def test_only_claude_is_measured(isolated_db, live, capsys):
    did = db.create_drama(title_zh="D", translation_engine="deepseek")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="hi")])
    assert bench.main([str(did)]) == 2
    assert "Claude only" in capsys.readouterr().out
    assert live.calls == 0


def test_default_window_is_longest_translated_run_capped_at_300():
    rows = [{"id": i, "idx": i, "zh": "字", "en": "x" if i not in (3, 40) else ""} for i in range(400)]
    assert bench.choose_window(rows, None, None) == list(range(41, 341))
    rows = [{"id": i, "idx": i, "zh": "字", "en": "x"} for i in range(400)]
    assert bench.choose_window(rows, None, None) == list(range(300))
    assert bench.choose_window(rows, 10, 12) == [10, 11, 12]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "0", "-1"])
def test_non_finite_or_non_positive_max_usd_is_refused(title, live, writes, capsys, tmp_path, value):
    assert bench.main([str(title), "--yes", f"--max-usd={value}", "--from", "0", "--to", "100000",
                       "--out", str(tmp_path / "r.md")]) == 2
    assert "finite number above zero" in capsys.readouterr().out
    assert live.calls == 0 and writes == []


def test_a_nan_estimate_refuses(title, live, writes, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(pricing, "estimate_cost_for_engine",
                        lambda *a, **k: float("nan"))
    assert bench.main([str(title), "--yes", "--out", str(tmp_path / "r.md")]) == 2
    assert live.calls == 0 and writes == []


def test_monthly_cap_wins_over_a_larger_max_usd(title, live, writes, capsys, tmp_path, monkeypatch):
    db.log_usage(None, "claude", "claude-sonnet-5-5", "translate", 1, 1, 0.99)
    patch_setting(monkeypatch, "monthly_cap_usd", 1.0)
    assert bench.main([str(title), "--yes", "--max-usd", "50", "--out", str(tmp_path / "r.md")]) == 2
    assert "above the $0.01 limit" in capsys.readouterr().out
    assert live.calls == 0


def test_default_report_goes_to_the_data_folder_not_the_title_folder(title, live, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(bench.portable, "data_dir", lambda: str(tmp_path))
    assert bench.main([str(title), "--yes", "--config", "10:2/2"]) == 0
    assert (tmp_path / bench.REPORT_NAME).is_file()
    assert str(tmp_path) not in capsys.readouterr().out


def test_report_is_not_written_over_a_file_swapped_in_during_the_run(title, live, capsys, tmp_path):
    out = tmp_path / "r.md"
    real_create = live.messages.create

    def create(**kw):
        out.write_text("CLAUDE_API_KEY=keep-me\n", encoding="utf-8")  # appears after the first check
        return real_create(**kw)
    live.messages.create = create
    bench.main([str(title), "--yes", "--out", str(out), "--config", "10:2/2"])
    assert out.read_text(encoding="utf-8") == "CLAUDE_API_KEY=keep-me\n"
    assert "Could not write" in capsys.readouterr().out
