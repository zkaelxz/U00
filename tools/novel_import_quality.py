"""Novel import quality: `python tools/novel_import_quality.py [--out DIR] [--case NAME ...]`.

Runs every saved case in tests/fixtures/novel_import/ through the app's own import code
into a throwaway library, reads the result back the way the Chapters panel does, and
scores it against the case's expected chapters. Nothing touches the network: site pages
are served from the saved files, and chapter-image OCR uses the case's saved OCR output.

A case is a folder with `case.json` (what to run) and `expected.json` (the chapters a
reader should get: title and paragraphs, plus how many images each one has). Kinds:

    adapter   a site adapter: get_chapters + the real chapter import job
    page      the generic extractor for a site with no adapter, one saved page per chapter
    upload    a .txt/.md/.epub raw-novel upload
    ocr       chapter OCR over page images, from each page's saved OCR text

Writes report.md and report.json to --out (default: the data folder's
novel-import-quality/). tests/test_novel_import_quality.py runs the same cases and pins
each score, so a site change or a regression fails a named test.
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python tools/novel_import_quality.py` sys.path[0] is tools/, not the repo.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CASES_DIR = REPO_ROOT / "tests" / "fixtures" / "novel_import"
SCORE_KEYS = ("completeness", "order", "titles", "paragraphs", "cleanliness", "encoding")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line or "").strip()


def _same_title(a: str, b: str) -> bool:
    return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "")


def _body_lines(chapter: dict) -> list:
    """The chapter's text lines, without the title line the import puts first."""
    lines = [_norm(ln) for ln in (chapter.get("text") or "").split("\n")]
    lines = [ln for ln in lines if ln]
    if lines and chapter.get("title") and _same_title(lines[0], chapter["title"]):
        lines = lines[1:]
    return lines


def _overlap(expected_paras: set, lines: list) -> float:
    if not expected_paras:
        return 0.0
    return len(expected_paras & set(lines)) / len(expected_paras)


def _in_order_fraction(positions: list) -> float:
    """Longest increasing run (not necessarily contiguous) over all: 1.0 when every
    matched chapter sits in the expected order."""
    if not positions:
        return 1.0
    best = []
    for p in positions:
        lo, hi = 0, len(best)
        while lo < hi:
            mid = (lo + hi) // 2
            if best[mid] < p:
                lo = mid + 1
            else:
                hi = mid
        if lo == len(best):
            best.append(p)
        else:
            best[lo] = p
    return len(best) / len(positions)


# Characters that never belong in CJK or English novel text and show up when bytes
# were read in the wrong encoding: the replacement character, Latin-1 letters from
# UTF-8 read as cp1252/latin-1, and C1 controls.
_DAMAGE = re.compile(r"[�\u0080-\u009fÀ-ÿ]")


def score(expected: list, actual: list, allowed=()) -> dict:
    """expected: [{title, paragraphs, images}]; actual: [{title, text, images}] in the
    order the reader sees them. Each expected chapter is matched to the actual chapter
    holding most of its paragraphs (at least half); a second actual chapter holding them
    is a duplicate, an actual chapter matching nothing is extra (a contents page, a
    copyright page, an unsplit blob). A chapter with no text (an illustrations page) is
    matched by its title. `allowed`: lines that are not chapter text but belong in the
    import all the same (a TXT file's title and blurb), so they don't count as junk."""
    exp_sets = [{_norm(p) for p in ch["paragraphs"] if _norm(p)} for ch in expected]
    act_lines = [_body_lines(ch) for ch in actual]
    matched = {}          # expected index -> actual index
    duplicates = extra = 0
    for ai, lines in enumerate(act_lines):
        best, best_ov = None, 0.0
        for ei, paras in enumerate(exp_sets):
            ov = _overlap(paras, lines) if paras else \
                float(_same_title(actual[ai].get("title"), expected[ei]["title"]))
            if ov > best_ov:
                best, best_ov = ei, ov
        if best is None or best_ov < 0.5:
            # An unsplit blob holds many chapters' paragraphs, each under half of it.
            extra += 1
        elif best in matched:
            duplicates += 1
        else:
            matched[best] = ai
    missing = [expected[i]["title"] for i in range(len(expected)) if i not in matched]
    order = _in_order_fraction([matched[i] for i in sorted(matched)])
    titles_ok = sum(1 for ei, ai in matched.items()
                    if _same_title(actual[ai].get("title"), expected[ei]["title"]))
    # Paragraphs are looked for in the whole import, so text that landed in the wrong
    # chapter (or in one unsplit blob) still counts as kept; losing it is what this scores.
    all_lines = set(ln for lines in act_lines for ln in lines)
    exp_total = sum(len(s) for s in exp_sets)
    found = sum(len(s & all_lines) for s in exp_sets)
    all_expected = set().union(*exp_sets) if exp_sets else set()
    ok_lines = {re.sub(r"\s+", "", t) for t in [ch["title"] for ch in expected] + list(allowed)}
    junk = [ln for lines in act_lines for ln in lines
            if ln not in all_expected and re.sub(r"\s+", "", ln) not in ok_lines]
    total_lines = sum(len(lines) for lines in act_lines)
    text = "\n".join(ch.get("text") or "" for ch in actual)
    exp_text = "\n".join("\n".join(ch["paragraphs"]) for ch in expected)
    damage = [c for c in _DAMAGE.findall(text) if c not in exp_text]
    images_expected = sum(int(ch.get("images", 0)) for ch in expected)
    images_kept = sum(int(ch.get("images", 0)) for ch in actual)
    n = len(expected) or 1
    scores = {
        "completeness": len(matched) / n,
        "order": order,
        "titles": titles_ok / n,
        "paragraphs": found / exp_total if exp_total else 1.0,
        "cleanliness": 1 - len(junk) / total_lines if total_lines else 0.0,
        "encoding": max(0.0, 1 - len(damage) / max(len(text), 1) * 20),
    }
    overall = round(100 * sum(scores.values()) / len(scores))
    return {
        "overall": overall,
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "chapters_expected": len(expected), "chapters_found": len(actual),
        "missing": missing, "duplicates": duplicates, "extra": extra,
        "junk_lines": len(junk), "junk_examples": junk[:5],
        "damaged_chars": len(damage),
        "images_expected": images_expected, "images_kept": images_kept,
    }


# ---------------------------------------------------------------------------
# Running a case
# ---------------------------------------------------------------------------

def case_names() -> list:
    return sorted(p.name for p in CASES_DIR.iterdir() if (p / "case.json").is_file())


def _load(case_dir: Path, name: str):
    with open(case_dir / name, encoding="utf-8") as f:
        return json.load(f)


def _fixture_transport(case_dir: Path, routes: dict):
    from sources.http_transport import Response

    def transport(method, url, headers, data, timeout):
        route = routes.get(url)
        if route is None:
            return Response(404, {"content-type": "text/html"}, b"<html>not found</html>", url)
        body = (case_dir / route["file"]).read_bytes()
        ctype = route.get("content_type", "text/html")
        return Response(route.get("status", 200), {"content-type": ctype}, body, url)
    return transport


def _client(source: str, transport):
    from sources.http import PacingPolicy, SourceClient
    policy = PacingPolicy(min_delay=0.0, max_delay=0.0, session_break_min_requests=0)
    return SourceClient(source, policy=policy, transport=transport, sleep=lambda s: None)


def _new_title(language: str) -> int:
    import db
    return db.create_drama(title_en="Import quality check", source_language=language)


def _saved_chapters(drama_id: int) -> list:
    """The chapters as the Chapters panel lists and reads them."""
    from services import novel_chapters_service as ncs
    listing = ncs.list_chapters(drama_id, limit=ncs.MAX_PAGE)
    out = []
    for row in listing["chapters"]:
        parts, offset = [], 0
        while offset is not None:
            got = ncs.read_chapter(drama_id, row["number"], offset, ncs.MAX_SLICE_CHARS)
            parts.append(got["text"])
            offset = got["next_offset"]
        text = "".join(parts)
        out.append({"title": row["title"], "text": text, "images": text.count("[[IMG:")})
    return out


def _run_adapter(case_dir: Path, case: dict, drama_id: int) -> None:
    from sources import chapter_order, pipeline, registry
    cls = registry.adapter_classes()[case["source"]]
    client = _client(case["source"], _fixture_transport(case_dir, case["routes"]))
    adapter = cls(client=client)
    chapters = chapter_order.reading_order(adapter, adapter.get_chapters(case["series_id"]))
    pipeline.run_import_job(f"quality_{drama_id}", case["source"], chapters, drama_id,
                            adapter=adapter)


def _run_pages(case_dir: Path, case: dict, drama_id: int) -> None:
    from sources import adaptive, pipeline
    for page in case["pages"]:
        html = (case_dir / page["file"]).read_bytes().decode(page.get("encoding", "utf-8"))
        data, _report = adaptive.extract_novel(html, page["url"], engine=None, use_cache=False,
                                               remember=False)
        if data is None:
            continue
        heading = adaptive.page_heading(data, html, page["url"]) or \
            adaptive.title_from_url(page["url"])
        pipeline.save_novel_text(drama_id, data["content"], append=True, heading=heading,
                                 url=page["url"])


def _epub_bytes(folder: Path) -> bytes:
    """A saved EPUB is kept unzipped so it can be read in review; zipped here, mimetype
    first and stored, as the format asks."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder).as_posix()
            if path.is_file() and rel != "mimetype":
                zf.write(path, rel, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def _run_upload(case_dir: Path, case: dict, drama_id: int) -> None:
    from services import novel_files_service
    source = case_dir / case["file"]
    data = _epub_bytes(source) if source.is_dir() else source.read_bytes()
    novel_files_service.upload_raw_novel(drama_id, case["filename"], io.BytesIO(data))


def _run_ocr(case_dir: Path, case: dict, drama_id: int) -> list:
    """Chapter OCR with each page's saved OCR text standing in for the engine. The
    result is the narration text (one chapter's images make one block of text)."""
    from unittest import mock

    import ocr
    from services import novel_attach_service as nas
    texts = [(case_dir / p).read_text(encoding="utf-8") for p in case["pages"]]
    stage = tempfile.mkdtemp(prefix="ocrq_")
    paths = []
    for n in range(len(texts)):
        path = os.path.join(stage, f"{n:04d}.png")
        open(path, "wb").close()
        paths.append(path)
    by_path = dict(zip(paths, texts))
    with mock.patch.object(ocr, "resolve_tesseract_lang", return_value="chi_sim"), \
            mock.patch.object(ocr, "extract_text_tesseract",
                              side_effect=lambda p, **kw: by_path[p]), \
            mock.patch.object(nas, "_image_readable", return_value=True), \
            mock.patch("background_jobs.set_result"), \
            mock.patch("background_jobs.update_progress"), \
            mock.patch("background_jobs.is_cancel_requested", return_value=False):
        nas._run_ocr_job(f"ocrq_{drama_id}", drama_id, stage, paths, "tesseract", "replace",
                         case.get("language", "zh"), "simplified", None)
    text = nas._read_novel(drama_id)
    return [{"title": case["chapter_title"], "text": text, "images": 0}]


def run_case(name: str) -> dict:
    """Runs one case into a new title of the library db is configured for."""
    case_dir = CASES_DIR / name
    case = _load(case_dir, "case.json")
    expected_doc = _load(case_dir, "expected.json")
    expected = expected_doc["chapters"]
    drama_id = _new_title(case.get("language", "zh"))
    kind = case["kind"]
    if kind == "ocr":
        actual = _run_ocr(case_dir, case, drama_id)
    else:
        {"adapter": _run_adapter, "page": _run_pages, "upload": _run_upload}[kind](
            case_dir, case, drama_id)
        actual = _saved_chapters(drama_id)
    result = score(expected, actual, expected_doc.get("allowed_lines", ()))
    result.update(case=name, kind=kind, label=case.get("label", name),
                  titles_seen=[c["title"] for c in actual][:12])
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _pct(v: float) -> str:
    return f"{round(100 * v)}%"


def render_markdown(results: list, before: dict = None) -> str:
    lines = ["# Novel import quality", "",
             "Each case is a saved set of pages or files run through the app's own import, "
             "read back as the Chapters panel shows it, and scored against what a reader "
             "should get. Scores: chapters found (completeness), chapter order, chapter "
             "titles, paragraphs kept, lines that are not the novel (cleanliness), and "
             "characters damaged by a wrong encoding. Images are counted, not scored.", "",
             "| Case | Overall | Chapters | Order | Titles | Paragraphs | Clean | Encoding "
             "| Missing / dup / extra | Junk lines | Images kept |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        s = r["scores"]
        prev = (before or {}).get(r["case"])
        overall = f"{r['overall']}" + (f" (was {prev['overall']})" if prev else "")
        lines.append(
            f"| {r['label']} | {overall} | {r['chapters_found']}/{r['chapters_expected']} "
            f"| {_pct(s['order'])} | {_pct(s['titles'])} | {_pct(s['paragraphs'])} "
            f"| {_pct(s['cleanliness'])} | {_pct(s['encoding'])} "
            f"| {len(r['missing'])} / {r['duplicates']} / {r['extra']} | {r['junk_lines']} "
            f"| {r['images_kept']}/{r['images_expected']} |")
    lines += ["", "## Leftover lines per case", ""]
    for r in results:
        if r["junk_examples"]:
            shown = "; ".join(f"`{j[:60]}`" for j in r["junk_examples"])
            lines.append(f"- {r['label']}: {shown}")
    return "\n".join(lines) + "\n"


def _default_out() -> str:
    import db
    return os.path.join(os.path.dirname(db.DRAMAS_DIR), "novel-import-quality")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", help="folder for report.md and report.json")
    parser.add_argument("--case", action="append", help="run only this case (repeatable)")
    parser.add_argument("--before", help="an earlier report.json to show next to the scores")
    args = parser.parse_args(argv)

    import db
    out = args.out or _default_out()
    with tempfile.TemporaryDirectory(prefix="novel_quality_") as tmp:
        previous = db.LIBRARY_DIR
        db.configure_library_dir(os.path.join(tmp, "library"))
        db.init_db()
        try:
            results = [run_case(name) for name in (args.case or case_names())]
        finally:
            db.configure_library_dir(previous)
    before = None
    if args.before:
        with open(args.before, encoding="utf-8") as f:
            before = {r["case"]: r for r in json.load(f)}
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out, "report.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(results, before))
    for r in results:
        print(f"{r['overall']:>3}  {r['label']}")
    print(f"Report: {os.path.join(out, 'report.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
