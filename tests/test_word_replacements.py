"""Word replacements: the whole-word replacer, the series-owned store and routes, and the hook in a finished translation run."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from types import SimpleNamespace as NS

import translation_finish
import word_replacements
from core import Line


def _drama(db, name="S", series=True):
    sid = db.get_or_create_series(name) if series else None
    return db.create_drama(title_en=f"D-{name}", series_id=sid)


def _url(did, tail=""):
    return f"/api/glossary/dramas/{did}/word-replacements{tail}"


class TestReplacer:
    def test_whole_words_only_ignoring_case(self):
        fix = word_replacements.build_replacer([("art", "are"), ("Lin", "Lin Mo")])
        assert fix("Thou ART here; start Berlin; Lin came") == "Thou are here; start Berlin; Lin Mo came"

    def test_longer_find_text_wins_and_a_replacement_is_not_replaced_again(self):
        fix = word_replacements.build_replacer([("aunt", "Auntie"), ("aunt wang", "Auntie Wang"), ("Auntie", "Aunt")])
        assert fix("Aunt Wang met an aunt") == "Auntie Wang met an Auntie"

    def test_no_pairs_leaves_the_text_alone(self):
        assert word_replacements.build_replacer([])("anything") == "anything"
        assert word_replacements.build_replacer([("", "x")])("anything") == "anything"


class TestRoutes:
    def test_save_list_update_delete(self, client, isolated_db):
        did = _drama(isolated_db)
        assert client.get(_url(did)).json() == []
        first = client.post(_url(did), json={"find_text": "thee", "replace_text": "you"}).json()
        again = client.post(_url(did), json={"find_text": "thee", "replace_text": "thou"}).json()
        assert again["id"] == first["id"] and again["replace_text"] == "thou"
        assert [(r["find_text"], r["replace_text"]) for r in client.get(_url(did)).json()] == [("thee", "thou")]
        assert client.delete(_url(did, f"/{first['id']}")).json() == {"deleted": [first["id"]]}
        assert client.get(_url(did)).json() == []
        assert client.delete(_url(did, f"/{first['id']}")).status_code == 404

    def test_a_title_without_a_series_gets_one_on_its_first_save(self, client, isolated_db):
        did = _drama(isolated_db, series=False)
        assert client.get(_url(did)).json() == []
        assert client.post(_url(did), json={"find_text": "a", "replace_text": "b"}).status_code == 200
        assert isolated_db.get_drama(did)["series_id"]

    def test_series_isolation(self, client, isolated_db):
        a, b = _drama(isolated_db, "A"), _drama(isolated_db, "B")
        rid = client.post(_url(a), json={"find_text": "x", "replace_text": "y"}).json()["id"]
        assert client.get(_url(b)).json() == []
        assert client.delete(_url(b, f"/{rid}")).status_code == 404

    @pytest.mark.parametrize("body", [{"find_text": "", "replace_text": "y"},
                                      {"find_text": "same", "replace_text": "SAME"},
                                      {"find_text": "x", "replace_text": "y", "extra": 1}])
    def test_bad_input_is_refused(self, client, isolated_db, body):
        assert client.post(_url(_drama(isolated_db)), json=body).status_code in (400, 422)


class TestFinishedRun:
    def _run(self, db, did, **kwargs):
        lines = db.load_line_objects(did)
        translation_finish.finish_translation_run(
            did, lines, NS(model="fake"), "claude", "audio_drama", glossary_terms=[], errors=[],
            enforce_ids={ln.id for ln in lines}, **kwargs)
        return db.load_lines(did)[0]["en"]

    def test_the_series_replacements_are_applied_to_what_the_run_wrote(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_word_replacement(isolated_db.get_drama(did)["series_id"], "thee", "you")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="一", en="I saw thee at dawn")])
        assert self._run(isolated_db, did) == "I saw you at dawn"

    def test_no_replacements_leaves_the_line_alone(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="一", en="I saw thee at dawn")])
        assert self._run(isolated_db, did) == "I saw thee at dawn"
