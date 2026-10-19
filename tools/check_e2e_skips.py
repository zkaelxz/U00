"""Fails when a Playwright test is skipped that frontend/e2e/skip-allowlist.json does not list.

    python tools/check_e2e_skips.py frontend/test-results/results.json

Several specs skip themselves when a prerequisite is missing (the review specs without OpenCV), and
Playwright counts a skip as a pass, so dropping that prerequisite from CI would remove the coverage
without a red check. The allowlist holds the skips CI is expected to have (the screenshot specs
that only run with a directory set); anything else skipped is a failure. Each shard sees only its own
slice, so the check is "skipped is a subset of the allowlist", not equality.
"""
import json
import os
import sys

ALLOWLIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "frontend", "e2e", "skip-allowlist.json")


def skipped_titles(report: dict) -> set[str]:
    found: set[str] = set()

    def walk(suite: dict) -> None:
        for spec in suite.get("specs", []):
            if any(t.get("status") == "skipped" for t in spec.get("tests", [])):
                found.add(f"{spec.get('file', suite.get('file', ''))} :: {spec['title']}")
        for child in suite.get("suites", []):
            walk(child)

    for top in report.get("suites", []):
        walk(top)
    return found


def unexpected_skips(report: dict, allowed: list[str]) -> list[str]:
    return sorted(skipped_titles(report) - set(allowed))


def main(argv: list[str]) -> int:
    with open(argv[1], encoding="utf-8") as f:
        report = json.load(f)
    with open(ALLOWLIST, encoding="utf-8") as f:
        allowed = json.load(f)
    extra = unexpected_skips(report, allowed)
    for title in extra:
        print(f"unexpected skipped e2e test: {title}")
    if extra:
        print("Fix the prerequisite that made it skip, or (if the skip is intended) add it to "
              "frontend/e2e/skip-allowlist.json.")
    return 1 if extra else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
