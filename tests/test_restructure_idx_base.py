"""Investigation of a suspected bug (React Review builder, 2026-09-29):
"restructure_service._commit renumbers lines from 0, so dramas numbered from
1 show #0 after a split/merge/add/delete".

Finding: NOT a product bug in _commit. `Line.idx` is 0-based everywhere the
app creates lines (transcribe_service, narration, cli, core.align /
merge_adjacent_short_lines, resegment, subtitle_formats.clip) and Streamlit
displays it as `idx + 1`. The 1-based numbering came from the React e2e seed
(frontend/e2e/review-stage.spec.ts), which masked the real display gap: the
React review row shows the raw `idx` (#0 for the first line).

These tests pin the convention _commit relies on, so a future "fix" that
renumbers from 1 fails here instead of silently shifting every stored
position. Fully mocked: isolated_db, no network.
"""
import pytest

import background_jobs
import core
import db
from core import Line
from services import restructure_service as svc


@pytest.fixture(autouse=True)
def _env(isolated_db):
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _seed(n=4):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh=f"第{i}句话", en=f"L{i}",
                             speaker="S") for i in range(n)])
    return did, [r["id"] for r in db.load_lines(did)]


def _idxs(did):
    return [r["idx"] for r in db.load_lines(did)]


class TestRestructureKeepsZeroBasedIdx:
    def test_add_delete_merge_split_all_leave_idx_0_to_n_minus_1(self):
        did, ids = _seed()
        svc.add_line(did, ids, after_line_id=ids[0], start=1.0, end=1.5, zh="新", en="")
        assert _idxs(did) == [0, 1, 2, 3, 4]
        ids = [r["id"] for r in db.load_lines(did)]
        svc.delete_line(did, ids[1], ids, confirm=True)
        assert _idxs(did) == [0, 1, 2, 3]
        ids = [r["id"] for r in db.load_lines(did)]
        svc.merge_lines(did, [ids[1], ids[2]], ids)
        assert _idxs(did) == [0, 1, 2]
        rows = db.load_lines(did)
        ids = [r["id"] for r in rows]
        svc.split_line(did, ids[0], ids, at_char=2, expected_zh=rows[0]["zh"])
        assert _idxs(did) == [0, 1, 2, 3]

    def test_restructure_base_matches_streamlit_merge_base(self):
        # The Streamlit "Apply merge" saves core.merge_adjacent_short_lines'
        # output, which renumbers from 0 (core.py `ln.idx = i`).
        streamlit = core.merge_adjacent_short_lines(
            [Line(idx=i + 1, start=i * 0.5, end=i * 0.5 + 0.4, zh="嗯", en="", speaker="S")
             for i in range(3)])
        assert streamlit[0].idx == 0

    def test_note_line_idx_follows_its_line_after_renumbering(self):
        did, ids = _seed()
        db.save_translation_notes(did, [{"line_id": ids[3], "line_idx": 3, "term": "t",
                                         "note": "n"}])
        svc.delete_line(did, ids[0], ids, confirm=True)
        (note,) = db.list_translation_notes(did)
        assert note["line_id"] == ids[3] and note["line_idx"] == 2
