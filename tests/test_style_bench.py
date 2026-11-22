import json

import pytest

from tools import style_bench


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _novel(root):
    series = root / "novel" / "xianxia" / "demo"
    _write(series / "original" / "ch001.txt", "师兄来了。\n他笑了。\n天黑了。")
    _write(series / "original" / "ch002.txt", "她走了。")
    _write(series / "reference" / "ch001.txt", "Shixiong's here.\nHe smiled.\nNight fell.")
    _write(series / "reference" / "ch002.txt", "She left.")
    _write(series / "runs" / "plain" / "ch001.txt", "Senior brother has arrived.\nHe laughed.\nIt became dark.")
    _write(series / "runs" / "guide" / "ch001.txt", "Shixiong's here.\nHe smiled.\nNight fell!")
    return series


def test_check_names_missing_reference_and_reference_pictures(tmp_path):
    series = tmp_path / "comic" / "romance" / "demo"
    _write(series / "original" / "001.txt", "")  # not a picture, ignored for comics
    (series / "original").mkdir(parents=True, exist_ok=True)
    (series / "original" / "001.jpg").write_bytes(b"x")
    (series / "original" / "002.jpg").write_bytes(b"x")
    (series / "reference").mkdir()
    (series / "reference" / "001.png").write_bytes(b"x")
    notes = "\n".join(style_bench.check(tmp_path))
    assert "comic/romance/demo: 2 originals, 0 reference texts" in notes
    assert "no reference for: 002" in notes
    assert "1 reference pictures still need their English" in notes


def test_check_on_empty_root_says_how_to_start(tmp_path):
    assert "No series found" in style_bench.check(tmp_path)[0]


def test_fingerprint_counts_style_markers():
    fp = style_bench.fingerprint("Don't go, Hyung-nim! I'm... scared. Wait — please.")
    assert fp["contractions_per_100w"] > 0
    assert fp["honorifics_per_100w"] > 0
    assert fp["exclaims_per_100s"] > 0 and fp["ellipses_per_100s"] > 0 and fp["dashes_per_100s"] > 0


def test_score_ranks_the_closer_run_higher_and_only_scores_shared_items(tmp_path):
    pytest.importorskip("sacrebleu")
    series = _novel(tmp_path)
    s = style_bench.load_series(series)
    plain, guide = style_bench.score_run(s, "plain"), style_bench.score_run(s, "guide")
    assert plain["items"] == guide["items"] == 1  # ch002 has no run file
    assert guide["chrf"] > plain["chrf"]
    report = style_bench.build_report(tmp_path)
    assert "## novel/xianxia/demo" in report and "| xianxia | guide | 1 |" in report


def test_blind_page_hides_which_run_is_which_and_unblind_tallies(tmp_path):
    series = _novel(tmp_path)
    page, key = style_bench.make_blind(series, ["plain", "guide"], with_reference=True, seed=1)
    assert len(key["cards"]) == 1  # ch002 lacks the runs, so it has no card
    assert "plain" not in page and "guide" not in page
    row = key["cards"][0]
    assert sorted(row[k] for k in "ABC") == ["guide", "plain", "reference"]
    guide_letter = next(k for k in "ABC" if row[k] == "guide")
    assert "guide 1" in style_bench.unblind(key, {"1": guide_letter})
    assert "tie 1" in style_bench.unblind(key, {"1": "tie"})


def test_blind_splits_a_chapter_into_passages_when_lines_line_up(tmp_path, monkeypatch):
    monkeypatch.setattr(style_bench, "NOVEL_LINES_PER_CARD", 2)
    series = _novel(tmp_path)
    _, key = style_bench.make_blind(series, ["plain", "guide"], with_reference=False, seed=1)
    assert [(c["item"], c["part"]) for c in key["cards"]] == [("ch001", 1), ("ch001", 2)]


def test_blind_refuses_one_version_and_unknown_runs(tmp_path):
    series = _novel(tmp_path)
    with pytest.raises(SystemExit):
        style_bench.make_blind(series, ["plain"], with_reference=False)
    with pytest.raises(SystemExit):
        style_bench.make_blind(series, ["nope", "plain"], with_reference=False)


def test_cli_writes_page_and_sealed_key(tmp_path, capsys):
    series = _novel(tmp_path)
    style_bench.main(["blind", str(series), "--runs", "plain,guide", "--seed", "3"])
    key = json.loads((series / style_bench.KEY_NAME).read_text(encoding="utf-8"))
    assert (series / style_bench.PAGE_NAME).is_file() and key["runs"] == ["plain", "guide"]
    style_bench.main(["unblind", str(series), json.dumps({"1": "A"})])
    assert "Blind result for novel/xianxia/demo" in capsys.readouterr().out
