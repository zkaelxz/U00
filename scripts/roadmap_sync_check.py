#!/usr/bin/env python3
"""Mechanical consistency check for docs/baihe-roadmap.md's three tracking
structures (### Step headers, §2 manual-check table, §4 status table).

This is planning-repo tooling, not application code -- it never touches
baihe-subtitler. Run it before reasoning by hand about roadmap sync state;
investigate only what it flags. See CLAUDE.md's "Keep the roadmap's three
tracking structures in sync" rule for why this exists: the manual version
of this check already demonstrated its failure mode once (commit
52bbc3c, 2026-09-27 -- 11 missing/stale §4 rows caught only by a full
manual re-derivation), and a first automated pass still missed 3 more
until step ids were diffed, not just counted (commit 9e82869).

Usage: python3 scripts/roadmap_sync_check.py [path-to-roadmap.md]
Exit code 0 if all three structures agree, 1 otherwise.
"""
import re
import sys
from pathlib import Path

STEP_ID = r"[0-9]+[a-zA-Z0-9-]*"  # e.g. "6b", "23g", "1c-pre" (hyphenated ids exist)


def extract_ids(text: str, pattern: str) -> list[str]:
    return re.findall(pattern, text, re.MULTILINE)


def section(text: str, start_pat: str, end_pat: str) -> str:
    m = re.search(start_pat, text, re.MULTILINE)
    if not m:
        raise SystemExit(f"Could not find section start: {start_pat!r}")
    rest = text[m.end():]
    m2 = re.search(end_pat, rest, re.MULTILINE)
    return rest[: m2.start()] if m2 else rest


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/baihe-roadmap.md")
    text = path.read_text(encoding="utf-8")

    header_ids = extract_ids(text, rf"^### Step ({STEP_ID}) —")

    sec2 = section(text, r"^## 2\. Roadmap: build in this order", r"^## Steps 36")
    sec2_ids = extract_ids(sec2, rf"^\| ({STEP_ID}) \|")

    sec4_start = re.search(r"\| Step \| Branch \| Merged \| Manual check \|", text)
    sec4_end = re.search(r"^- \*\*After Step 10", text, re.MULTILINE)
    if not (sec4_start and sec4_end):
        raise SystemExit("Could not locate §4's status table bounds")
    sec4 = text[sec4_start.end(): sec4_end.start()]
    sec4_ids = extract_ids(sec4, rf"^  \| ({STEP_ID}) [—-]")

    ok = True
    print(f"### Step headers : {len(header_ids)}")
    print(f"§2 manual-check   : {len(sec2_ids)}")
    print(f"§4 status table   : {len(sec4_ids)}")

    h, s2, s4 = set(header_ids), set(sec2_ids), set(sec4_ids)

    missing_from_2 = h - s2
    missing_from_4 = h - s4
    extra_in_2 = s2 - h
    extra_in_4 = s4 - h

    if missing_from_2:
        ok = False
        print(f"\n⚠ In headers but missing from §2: {sorted(missing_from_2)}")
    if missing_from_4:
        ok = False
        print(f"\n⚠ In headers but missing from §4: {sorted(missing_from_4)}")
    if extra_in_2:
        ok = False
        print(f"\n⚠ In §2 but no matching header: {sorted(extra_in_2)}")
    if extra_in_4:
        ok = False
        print(f"\n⚠ In §4 but no matching header: {sorted(extra_in_4)}")

    # §4 rows still reading "Not started" while a PR number for that exact
    # step id shows up elsewhere in the merged git log is exactly the class
    # of stale-status bug this repo has hit twice -- this script can't run
    # git here (no guarantee this checkout has baihe-subtitler fetched),
    # so it only flags the structural drift above; cross-check merge status
    # against real git/PR state separately, per CLAUDE.md's own method.

    if ok:
        print("\n✓ All three tracking structures agree on the same step-id set.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
