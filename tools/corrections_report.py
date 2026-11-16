"""Corrections report: `python tools/corrections_report.py [--json]`.

Reads the library's edit log (read-only) and prints how many corrections were made per
engine, model, prompt version, flag and mark, for English and for source text, and how
often a flagged line was corrected against an unflagged one.

A deleted title's corrections are kept and counted, but no longer have a line, so the
flagged/unflagged rates leave them out. Not counted: a translation made off-peak records
no engine, so its corrections show under "(none)"; an accepted line-AI rewrite is logged
as a person's English correction; edits saved before the edit log existed have no line,
engine or flag.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/corrections_report.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _pct(part, whole) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def _rate_lines(rates: dict, line_count: int) -> list:
    flagged = rates["flagged_lines"]
    unflagged = max(line_count - flagged, 0)
    return [f"  flagged lines corrected:   {rates['flagged_corrected']} of {flagged} "
            f"({_pct(rates['flagged_corrected'], flagged)})",
            f"  unflagged lines corrected: {rates['unflagged_corrected']} of {unflagged} "
            f"({_pct(rates['unflagged_corrected'], unflagged)})"]


def render(report: dict) -> str:
    out = [f"Lines in the library: {report['line_count']}", ""]
    for kind, title in (("english", "English corrections"), ("source", "Source-text corrections")):
        part = report[kind]
        out.append(f"{title}: {part['corrections']} "
                   f"({part['from_deleted_titles']} from deleted titles)")
        for key, counts in part.items():
            if key.startswith("by_") or key == "machine_fixes_by_path":
                label = key.replace("_", " ")
                out.append(f"  {label}: " + (", ".join(f"{k} {v}" for k, v in counts.items())
                                            or "none"))
        out += _rate_lines(part["flag_rates"], report["line_count"])
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the counts as JSON")
    args = parser.parse_args(argv)
    from services import edit_log_service
    report = edit_log_service.corrections_report()
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
