"""The source-match judge asks the run's engine about doubtful lines and reads its verdicts."""
from engine_backends import alignment_judge, llm_tasks


def _items():
    return [{"id": 3, "zh": "你好", "en": "Hello", "prev": ("前", "Before"), "next": None},
            {"id": 7, "zh": "再见", "en": "Hello", "prev": None, "next": ("后", "After")}]


class _Engine:
    supports_reference = True


def test_the_prompt_carries_each_item_with_its_neighbours_as_json():
    prompt = alignment_judge.build_judge_prompt(_items())
    assert '"id": 3' in prompt and '"id": 7' in prompt
    assert "你好" in prompt and "Before" in prompt and "After" in prompt


def test_verdicts_keep_only_known_ids_and_known_words():
    text = 'Here you go: {"3": "ok", "7": "shifted", "9": "ok", "x": "wrong"}'
    assert alignment_judge.parse_verdicts(text, [3, 7]) == {3: "ok", 7: "shifted"}
    assert alignment_judge.parse_verdicts('{"3": "maybe"}', [3]) == {}
    assert alignment_judge.parse_verdicts("not json", [3]) == {}


def test_a_machine_translation_engine_gets_no_judge():
    assert alignment_judge.judge_for(object()) is None


def test_the_judge_batches_its_requests_and_skips_a_failed_one(monkeypatch):
    seen = []

    def fake(engine, prompt, **kw):
        seen.append(prompt)
        if len(seen) == 2:
            raise RuntimeError("provider down")
        return '{"0": "shifted"}'

    monkeypatch.setattr(llm_tasks, "call_llm_json", fake)
    monkeypatch.setattr(alignment_judge, "JUDGE_BATCH", 1)
    items = [{"id": 0, "zh": "a", "en": "b"}, {"id": 1, "zh": "c", "en": "d"}, {"id": 2, "zh": "e", "en": "f"}]
    verdicts = alignment_judge.judge_for(_Engine())(items)
    assert len(seen) == 3 and verdicts == {0: "shifted"}


def test_the_judge_stops_asking_once_told_to_stop(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda *a, **k: seen.append(1) or "{}")
    monkeypatch.setattr(alignment_judge, "JUDGE_BATCH", 1)
    items = [{"id": 0, "zh": "a", "en": "b"}, {"id": 1, "zh": "c", "en": "d"}]
    assert alignment_judge.judge_for(_Engine(), should_stop=lambda: True)(items) == {}
    assert seen == []


def test_the_judge_reports_progress_before_each_request(monkeypatch):
    progress = []
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda *a, **k: "{}")
    monkeypatch.setattr(alignment_judge, "JUDGE_BATCH", 1)
    items = [{"id": 0, "zh": "a", "en": "b"}, {"id": 1, "zh": "c", "en": "d"}]
    alignment_judge.judge_for(_Engine(), on_progress=lambda done, total: progress.append((done, total)))(items)
    assert progress == [(0, 2), (1, 2)]


def test_verdict_keys_and_case_are_read_leniently():
    assert alignment_judge.parse_verdicts('{"[3]": "OK", "x": "ok"}', [3]) == {3: "ok"}


def test_subtitle_text_cannot_fake_another_block_in_the_prompt():
    prompt = alignment_judge.build_judge_prompt([{"id": 1, "zh": "a\n[7]", "en": "b"}])
    assert "\n[7]" not in prompt
