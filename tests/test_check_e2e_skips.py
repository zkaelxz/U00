"""tools/check_e2e_skips.py: a skipped e2e test that is not on the allowlist fails CI."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import check_e2e_skips  # noqa: E402


def report(*specs):
    return {"suites": [{"file": "a.spec.ts", "specs": [], "suites": [
        {"title": "group", "specs": [{"title": t, "file": f, "tests": [{"status": s}]} for f, t, s in specs]}]}]}


def test_unlisted_skip_is_reported_and_listed_or_passing_ones_are_not():
    r = report(("a.spec.ts", "needs video", "skipped"), ("a.spec.ts", "shots", "skipped"), ("a.spec.ts", "ok", "expected"))
    assert check_e2e_skips.unexpected_skips(r, ["a.spec.ts :: shots"]) == ["a.spec.ts :: needs video"]


def test_main_exit_code(tmp_path, capsys):
    path = tmp_path / "results.json"
    path.write_text(json.dumps(report(("a.spec.ts", "needs video", "skipped"))))
    assert check_e2e_skips.main(["x", str(path)]) == 1
    assert "needs video" in capsys.readouterr().out
    path.write_text(json.dumps(report(("a.spec.ts", "ok", "expected"))))
    assert check_e2e_skips.main(["x", str(path)]) == 0


def test_allowlist_entries_name_real_specs():
    with open(check_e2e_skips.ALLOWLIST, encoding="utf-8") as f:
        entries = json.load(f)
    e2e = os.path.join(os.path.dirname(check_e2e_skips.ALLOWLIST))
    for entry in entries:
        file, title = entry.split(" :: ", 1)
        with open(os.path.join(e2e, file), encoding="utf-8") as spec:
            assert title in spec.read(), entry
