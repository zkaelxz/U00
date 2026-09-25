"""
tests/test_voice_id.py -- Step 8's recurring-voice suggestions. Every
embedding here is a small hand-picked vector, not a real pyannote
fingerprint -- the point is the ranking/threshold/dismissal logic, not
anything about actual voice similarity.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_id as vid


class TestCosineSimilarity:
    def test_identical_vectors_are_1(self):
        assert vid.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0

    def test_orthogonal_vectors_are_0(self):
        assert vid.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite_vectors_are_negative_1(self):
        assert vid.cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == -1.0

    def test_scale_does_not_matter(self):
        assert vid.cosine_similarity([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)

    def test_dimension_mismatch_is_zero_not_an_error(self):
        assert vid.cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_empty_or_zero_vectors_are_zero(self):
        assert vid.cosine_similarity([], []) == 0.0
        assert vid.cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def _char(id, name, fingerprint, as_json=True):
    return {"id": id, "character_name": name,
            "voice_fingerprint": json.dumps(fingerprint) if as_json else fingerprint}


class TestSuggestSpeakerMatches:
    def test_best_match_above_threshold_is_suggested(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [_char(1, "Su Shan", [1.0, 0.01]), _char(2, "Rin", [0.0, 1.0])]
        out = vid.suggest_speaker_matches(embeddings, characters)
        assert len(out) == 1
        assert out[0]["speaker_label"] == "SPEAKER_00"
        assert out[0]["character_name"] == "Su Shan"
        assert out[0]["series_character_id"] == 1
        assert out[0]["similarity"] > 0.99

    def test_no_candidate_above_threshold_gives_no_suggestion(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [_char(1, "Rin", [0.0, 1.0])]  # orthogonal -- similarity 0
        assert vid.suggest_speaker_matches(embeddings, characters) == []

    def test_threshold_is_configurable(self):
        embeddings = {"SPEAKER_00": [1.0, 1.0]}
        characters = [_char(1, "Su Shan", [1.0, 0.5])]
        similarity = vid.cosine_similarity([1.0, 1.0], [1.0, 0.5])
        assert vid.suggest_speaker_matches(embeddings, characters,
                                           threshold=similarity + 0.01) == []
        assert len(vid.suggest_speaker_matches(embeddings, characters,
                                               threshold=similarity - 0.01)) == 1

    def test_only_the_single_best_candidate_is_returned_per_speaker(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [_char(1, "Close", [0.9, 0.1]), _char(2, "Exact", [1.0, 0.0])]
        out = vid.suggest_speaker_matches(embeddings, characters)
        assert len(out) == 1
        assert out[0]["character_name"] == "Exact"

    def test_already_named_speakers_are_skipped(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [_char(1, "Su Shan", [1.0, 0.0])]
        out = vid.suggest_speaker_matches(embeddings, characters, already_named={"SPEAKER_00"})
        assert out == []

    def test_dismissed_pair_is_skipped_but_a_different_candidate_still_shows(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [_char(1, "Su Shan", [1.0, 0.0]), _char(2, "Almost", [0.99, 0.01])]
        out = vid.suggest_speaker_matches(
            embeddings, characters, dismissed={("SPEAKER_00", 1)})
        assert len(out) == 1
        assert out[0]["series_character_id"] == 2

    def test_characters_with_no_fingerprint_yet_are_never_candidates(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [{"id": 1, "character_name": "No Voice Yet", "voice_fingerprint": None}]
        assert vid.suggest_speaker_matches(embeddings, characters) == []

    def test_a_malformed_stored_fingerprint_is_skipped_not_raised(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [{"id": 1, "character_name": "Corrupt", "voice_fingerprint": "not json"}]
        assert vid.suggest_speaker_matches(embeddings, characters) == []

    def test_multiple_speakers_each_get_their_own_best_match(self):
        embeddings = {"SPEAKER_00": [1.0, 0.0], "SPEAKER_01": [0.0, 1.0]}
        characters = [_char(1, "Su Shan", [1.0, 0.0]), _char(2, "Rin", [0.0, 1.0])]
        out = vid.suggest_speaker_matches(embeddings, characters)
        by_speaker = {s["speaker_label"]: s["character_name"] for s in out}
        assert by_speaker == {"SPEAKER_00": "Su Shan", "SPEAKER_01": "Rin"}

    def test_results_are_sorted_by_similarity_descending(self):
        embeddings = {"SPEAKER_00": [1.0, 0.3], "SPEAKER_01": [1.0, 0.0]}
        characters = [_char(1, "Su Shan", [1.0, 0.0])]
        out = vid.suggest_speaker_matches(embeddings, characters, threshold=0.5)
        assert [s["speaker_label"] for s in out] == ["SPEAKER_01", "SPEAKER_00"]

    def test_pre_parsed_list_fingerprint_also_works(self):
        # A caller (e.g. a test fixture) that already parsed the JSON.
        embeddings = {"SPEAKER_00": [1.0, 0.0]}
        characters = [_char(1, "Su Shan", [1.0, 0.0], as_json=False)]
        out = vid.suggest_speaker_matches(embeddings, characters)
        assert len(out) == 1

    def test_no_embeddings_gives_no_suggestions(self):
        characters = [_char(1, "Su Shan", [1.0, 0.0])]
        assert vid.suggest_speaker_matches({}, characters) == []

    def test_never_auto_applies_anything(self):
        # The whole point: this only ever returns data for a person to
        # confirm or dismiss. Nothing here writes to a database -- it
        # doesn't even import db.py.
        import inspect
        source = inspect.getsource(vid.suggest_speaker_matches)
        assert "upsert_character" not in source
        assert "import db" not in inspect.getsource(vid)
