"""The re-hear of a thin stretch marks the lines it writes, so Review can tell them from the first hearing."""
from types import SimpleNamespace

import whisper_transcribe as wt


class _Model:
    """Accepts clip_timestamps and returns fixed segments for the re-decode."""
    def __init__(self, heard):
        self.heard = heard

    def transcribe(self, audio_path, clip_timestamps=None, vad_filter=None, **kw):
        return [SimpleNamespace(start=s, end=e, text=t, words=None, avg_logprob=-0.2,
                                no_speech_prob=0.0, compression_ratio=1.0)
                for s, e, t in self.heard], None


def _run(heard, segments):
    return wt._rehear_sparse(_Model(heard), "a.wav", {}, segments, set())


def test_a_replaced_stretch_carries_the_reheard_note():
    thin = {"start": 0.0, "end": 10.0, "text": "嗯"}
    out = _run([(1.0, 4.0, "我今天去了學校"), (4.5, 9.0, "然後回家吃飯")], [thin])
    assert [s["text"] for s in out] == ["我今天去了學校", "然後回家吃飯"]
    assert all(s["flag"] == "text_uncertain" and s["flag_note"] == wt.REHEARD_NOTE for s in out)


def test_a_stretch_that_is_kept_is_not_marked():
    thin = {"start": 0.0, "end": 10.0, "text": "我今天去了學校然後回家"}
    out = _run([(1.0, 3.0, "嗯")], [thin])
    assert out == [thin] and "flag" not in out[0]


def test_other_segments_are_left_unmarked():
    thin = {"start": 0.0, "end": 10.0, "text": "嗯"}
    dense = {"start": 11.0, "end": 14.0, "text": "這一句是正常的對話內容"}
    out = _run([(1.0, 4.0, "我今天去了學校")], [thin, dense])
    assert out[0].get("flag") == "text_uncertain" and "flag" not in out[-1]
