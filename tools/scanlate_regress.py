"""Scanlate regression sheet: `python tools/scanlate_regress.py SAMPLES_DIR [--out DIR]`.

SAMPLES_DIR holds pageNN.jpg (or .png) beside pageNN.json, a Quick read cache entry or stored
regions with `translated_text`. Each page is typeset by the app's own render code twice, once
with its text and once with the text blanked, and every balloon is checked for fill outside its
outline, off-centre text, tiny text and source lettering left behind. Writes a report and a
before|after sheet per page. The samples are private art: keep them out of the repository.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cv2

from scanlate import scanlate_balloons, scanlate_checks, scanlate_render

# Text the renderer draws as nothing, so the render shows only what the erase left.
_BLANK_TEXT = "​"


def _pages(folder: Path):
    for json_path in sorted(folder.glob("page*.json")):
        for ext in (".jpg", ".png", ".jpeg"):
            image = json_path.with_suffix(ext)
            if image.exists():
                yield json_path.stem, image, json_path
                break


def check_page(image: Path, regions: list, out_dir: Path, stem: str):
    """(report rows, path of the typeset page) for one page."""
    shown = [dict(r, skip=False) for r in regions]
    blank = [dict(r, skip=False, translated_text=_BLANK_TEXT) for r in regions]
    typeset = out_dir / f"{stem}-after.png"
    erased = out_dir / f"{stem}-erased.png"
    scanlate_render.process_page(str(image), shown, str(typeset), notes=[])
    scanlate_render.process_page(str(image), blank, str(erased), notes=[])
    original = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    after = cv2.imread(str(typeset), cv2.IMREAD_GRAYSCALE)
    cleaned = cv2.imread(str(erased), cv2.IMREAD_GRAYSCALE)
    rows = []
    for region in regions:
        if not (region.get("translated_text") or "").strip() or region.get("kind", "bubble") != "bubble":
            continue
        interior = scanlate_balloons.bubble_erase_mask(str(image), region, image=original)
        if interior is None:
            continue
        rows.append({"text": region["translated_text"][:40],
                     "problems": scanlate_checks.region_problems(original, after, cleaned, region, interior)})
    erased.unlink(missing_ok=True)
    return rows, typeset


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("samples")
    parser.add_argument("--out", default="scanlate-regress")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report, failing = {}, 0
    for stem, image, json_path in _pages(Path(args.samples)):
        regions = json.loads(json_path.read_text(encoding="utf-8")).get("regions", [])
        rows, typeset = check_page(image, regions, out_dir, stem)
        before, after = cv2.imread(str(image)), cv2.imread(str(typeset))
        cv2.imwrite(str(out_dir / f"{stem}-sheet.jpg"), cv2.hconcat([before, after]))
        report[stem] = rows
        failing += sum(1 for r in rows if r["problems"])
    (out_dir / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    total = sum(len(rows) for rows in report.values())
    print(f"{failing} of {total} balloons fail a check; see {out_dir / 'report.json'}")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
