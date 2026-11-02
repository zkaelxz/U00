"""Test stand-in for the voice engines: routes narration through a per-line fake instead of a voice venv."""

import dub_narration
from dub import VENV_LINE_FAILED
from memory_headroom import HeadroomError


def speak_with(monkeypatch, synth):
    """Narration speaks each line with synth(text, out_path, ref_audio_path=, ref_text=,
    instruct=); a plain Exception from it fails that line, a HeadroomError or anything
    that is not an Exception (a kill) propagates as the real batch would."""
    def speak(engine, requests, workdir):
        for i, r in enumerate(requests):
            try:
                synth(r["text"], r["out"], ref_audio_path=r.get("ref_audio"),
                      ref_text=r.get("ref_text"), instruct=r.get("instruct"))
            except HeadroomError:
                raise
            except Exception:
                yield i, VENV_LINE_FAILED
            else:
                yield i, None
    monkeypatch.setattr(dub_narration, "speak_in_venv", speak)
    return synth
