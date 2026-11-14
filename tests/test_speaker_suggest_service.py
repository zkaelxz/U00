import pytest

import diarize
from core import Line
from lib.errors import InvalidInputError, NotFoundError
from services import speaker_suggest_service as svc


def _line(i, speaker=None, **kw):
    return Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f"中{i}", en=f"Hi {i}", speaker=speaker, **kw)


def _seed(db, lines, turns=None):
    did = db.create_drama(title_en="D")
    db.save_lines(did, lines)
    if turns is not None:
        diarize.save_turns(db.drama_dir(did), turns)
    return did, {ln.idx: ln.id for ln in db.load_line_texts(did)}


def _turn(i, speaker):
    return {"start": i * 2.0, "end": i * 2.0 + 1.5, "speaker": speaker}


def _guesses(result):
    return {g["line_id"]: [(s["speaker"], s["reasons"]) for s in g["speakers"]] for g in result["lines"]}


def test_neighbours_that_agree_come_first(isolated_db):
    did, ids = _seed(isolated_db, [_line(0, "Anok"), _line(1), _line(2, "Anok")])
    assert _guesses(svc.suggest_speakers(did, [ids[1]])) == {ids[1]: [("Anok", ["above", "below"])]}


def test_nearest_labelled_lines_skip_other_blanks(isolated_db):
    did, ids = _seed(isolated_db, [_line(0, "Anok"), _line(1), _line(2), _line(3, "Cray")])
    g = _guesses(svc.suggest_speakers(did, [ids[1], ids[2]]))
    assert g[ids[1]] == [("Anok", ["above"]), ("Cray", ["below"])]
    assert g[ids[2]] == g[ids[1]]


def test_voice_pick_is_named_after_the_speaker_on_that_voices_lines(isolated_db):
    # The saved turns keep detection's own labels; the lines were renamed since.
    turns = [_turn(0, "SPEAKER_00"), _turn(1, "SPEAKER_01"), _turn(2, "SPEAKER_01"), _turn(3, "SPEAKER_00")]
    did, ids = _seed(isolated_db, [_line(0, "Anok"), _line(1), _line(2, "Cray"), _line(3, "Anok")], turns)
    # Cray has two clues (below and voice), Anok one, so Cray leads.
    assert _guesses(svc.suggest_speakers(did, [ids[1]]))[ids[1]] == [
        ("Cray", ["below", "voice"]), ("Anok", ["above"])]


def test_voice_with_no_named_lines_offers_nothing_for_it(isolated_db):
    turns = [_turn(0, "SPEAKER_00"), _turn(1, "SPEAKER_05")]
    did, ids = _seed(isolated_db, [_line(0, "Anok"), _line(1)], turns)
    assert _guesses(svc.suggest_speakers(did, [ids[1]]))[ids[1]] == [("Anok", ["above"])]


def test_lines_that_need_no_speaker_get_no_guess(isolated_db):
    did, ids = _seed(isolated_db, [
        _line(0, "Anok"), _line(1, speaker_manual=True), _line(2, sfx=True), _line(3, "Cray")])
    assert svc.suggest_speakers(did, [ids[0], ids[1], ids[2], ids[3]]) == {"lines": []}


def test_title_with_no_speakers_has_no_guesses(isolated_db):
    did, ids = _seed(isolated_db, [_line(0), _line(1)])
    assert svc.suggest_speakers(did, [ids[0], ids[1]]) == {"lines": []}


def test_other_titles_lines_are_left_out(isolated_db):
    did, ids = _seed(isolated_db, [_line(0, "Anok"), _line(1)])
    _, other_ids = _seed(isolated_db, [_line(0, "Cray"), _line(1)])
    assert svc.suggest_speakers(did, [other_ids[1]]) == {"lines": []}


def test_writes_nothing(isolated_db):
    did, ids = _seed(isolated_db, [_line(0, "Anok"), _line(1)])
    before = [ln._asdict() for ln in isolated_db.load_line_texts(did)]
    svc.suggest_speakers(did, [ids[1]])
    assert [ln._asdict() for ln in isolated_db.load_line_texts(did)] == before


@pytest.mark.parametrize("bad", [[], [True], ["1"], list(range(1, svc.MAX_LINES + 2))])
def test_bad_ids_are_refused(isolated_db, bad):
    did, _ = _seed(isolated_db, [_line(0)])
    with pytest.raises(InvalidInputError):
        svc.suggest_speakers(did, bad)


def test_unknown_title(isolated_db):
    with pytest.raises(NotFoundError):
        svc.suggest_speakers(999, [1])


def test_corrupt_turns_are_ignored(isolated_db):
    did, ids = _seed(isolated_db, [_line(0, "Anok"), _line(1)],
                     [{"start": "x", "end": 1, "speaker": "S"}, {"start": 3, "end": 2, "speaker": "S"}, {"end": 1}])
    assert _guesses(svc.suggest_speakers(did, [ids[1]]))[ids[1]] == [("Anok", ["above"])]
