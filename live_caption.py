"""Captioning of one live chunk: padded audio window, board update, hand-off to the translator.

Mixed into live_translate._LiveRun, which owns the state it reads (state,
board, stage, runner, skips ...) and the Whisper call itself, so the
patch targets tests use on live_translate stay where they are.
"""
import os
from collections.abc import Callable
from typing import Any

import live_audio
import live_cue_feed
import live_pipeline
import live_whisper

# Whisper's initial_prompt has a real, hard limit (roughly the last 224
# tokens) -- capping what's carried forward here to a generous but bounded
# tail keeps every chunk's prompt cheap to build, not because a longer
# value would break anything, since Whisper itself only ever uses the end
# of whatever's passed.
CHUNK_CONTEXT_CARRY_CHARS = 200


class ChunkCaptioner:
    # Provided by _LiveRun; declared so this mixin type-checks on its own.
    out_dir: str
    segment_seconds: int
    overlap_seconds: float
    state: dict[str, Any]
    note: Callable[..., Any]
    runner: live_whisper.WhisperRunner
    board: live_pipeline.CueBoard
    skips: live_whisper.SkipNotes
    stage: live_pipeline.Stage
    stats: live_pipeline.LagStats
    _transcribe_window: Callable[..., tuple[list[dict[str, Any]], Any]]
    _superseded: Callable[[], bool]

    def caption(self, idx, path, worker, final=False):
        """Whisper for one chunk, its lines onto the board and its translation
        handed to the worker. Raises JobCancelled once the session is cancelled."""
        scratch = []
        try:
            # Read before this chunk's file is deleted below --
            # it's the audio the NEXT chunk gets padded with.
            window = live_audio.window_for_chunk(
                path, idx, self.out_dir, self.segment_seconds, self.state["prev_tail"],
                self.overlap_seconds)
            tail, scratch = window.tail, window.scratch
            if final and window.audio_seconds - window.pad_seconds < live_pipeline.MIN_FINAL_SECONDS:
                return
            if window.trimmed_seconds:
                self.note(f"Chunk {idx} held {window.audio_seconds + window.trimmed_seconds:.0f} s "
                          f"of audio: skipped its first {window.trimmed_seconds:.0f} s to catch up.")
            # Taken before this chunk's own jump is added: that one moves
            # the chunks after it, not this chunk's start.
            shift = self.state["jump"]
            self.count_jump(idx, path, window.chunk_seconds)
            new_cues, timing = self._transcribe_window(idx, path, window, shift)
            if self._superseded():
                # The session moved on while this one chunk's own
                # transcribe call was in flight -- its result is stale,
                # so it's dropped, not applied.
                return
            self.runner.mark_model_ready()
            self.board.add(new_cues)
            self._hand_to_translator(idx, window, new_cues, timing, worker, final)
            if new_cues:
                # Carries this chunk's own tail into the NEXT chunk's
                # transcription -- see process_chunk's docstring.
                # Independent of the translation, so a translation
                # failure never breaks this.
                joined = " ".join(c["text"] for c in new_cues)
                self.state["context"] = joined[-CHUNK_CONTEXT_CARRY_CHARS:]
            self._carry_tail(idx, tail, new_cues, shift)
        except live_whisper.ChunkTimeout as exc:
            self.skips.timed_out(idx, exc.seconds)
            self.state["prev_tail"] = None
        except live_whisper.WhisperBusy as exc:
            self.skips.busy(idx, exc.label)
            self.state["prev_tail"] = None
        finally:
            self.stage.whisper_over()
            self.state["last"] = idx
            self.runner.flush_ollama_notice()
            for leftover in [path, *scratch]:
                try:
                    os.remove(leftover)
                except OSError:
                    pass


    def count_jump(self, idx, path, chunk_seconds=None):
        """Adds how far chunk `idx` is off its slot (live_audio.jump_seconds)
        to the shift every later chunk's timestamps get, so the cues stay on
        the stream's real timeline after a timestamp jump. Counted once per
        chunk, whether it is captioned or dropped to catch up."""
        if chunk_seconds is None:
            chunk_seconds = live_audio.wav_seconds(path) or 0.0
        self.state["jump"] += live_audio.jump_seconds(
            chunk_seconds, self.segment_seconds)

    def _hand_to_translator(self, idx, window, new_cues, timing, worker, final):
        own_audio = window.audio_seconds - window.pad_seconds
        pending = [c for c in new_cues if c.get("translation") == live_cue_feed.PENDING]
        if pending:
            worker.submit(idx, pending, timing, own_audio, keep_all=final)
        else:
            self.stats.chunk_done(own_audio, timing.report(idx, self.note, cues=0))


    def _carry_tail(self, idx, tail, new_cues, shift=0.0):
        if tail:
            # Only cues that reach into this chunk's own last
            # tail["seconds"] -- an older line that merely
            # repeats a phrase must never match the next
            # chunk's overlap and get new speech trimmed.
            chunk_end = idx * self.segment_seconds + shift + tail["chunk_seconds"]
            tail["index"] = idx
            tail["text"] = " ".join(
                c["text"] for c in new_cues if c["end"] > chunk_end - tail["seconds"])
        self.state["prev_tail"] = tail
