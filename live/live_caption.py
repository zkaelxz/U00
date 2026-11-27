"""Captioning of one live chunk: padded audio window, board update, hand-off to the translator.

Mixed into live_translate._LiveRun, which owns the state it reads (state,
board, stage, runner, skips ...) and the Whisper call itself, so the
patch targets tests use on live_translate stay where they are.
"""
import os
from collections.abc import Callable
from typing import Any

from live import live_audio
from live import live_cue_feed
from live import live_pipeline
from live import live_whisper

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
        # Lines the previous chunk held back (hold_back_overlap): this chunk
        # hears them again only when it is padded with that chunk's tail.
        held = self.state.pop("held", None)
        unheard = None
        try:
            # Read before this chunk's file is deleted below --
            # it's the audio the NEXT chunk gets padded with.
            window = live_audio.window_for_chunk(
                path, idx, self.out_dir, self.segment_seconds, self.state["prev_tail"],
                self.overlap_seconds)
            tail, scratch = window.tail, window.scratch
            reheard = bool(held) and window.pad_seconds > 0 and held["index"] == idx - 1
            if held and not reheard:
                self._release(held, path, worker, final)
            unheard = held if reheard else None
            if final and window.audio_seconds - window.pad_seconds < live_pipeline.MIN_FINAL_SECONDS:
                # Nothing to hear, but a capture restarted after a break places
                # its chunks after this one's real end, not after a full slot.
                self.state["jump"] += window.chunk_seconds - self.segment_seconds
                return
            if window.trimmed_seconds:
                self.note(f"Chunk {idx} held {window.audio_seconds + window.trimmed_seconds:.0f} s "
                          f"of audio: skipped its first {window.trimmed_seconds:.0f} s to catch up.")
            # Taken before this chunk's own jump is added: that one moves
            # the chunks after it, not this chunk's start.
            shift = self.state["jump"]
            self.count_jump(idx, path, window.chunk_seconds)
            new_cues, timing = self._transcribe_window(
                idx, path, window, shift, covered=held["covered"] if reheard else None)
            if self._superseded():
                # The session moved on while this one chunk's own
                # transcribe call was in flight -- its result is stale,
                # so it's dropped, not applied.
                unheard = None
                return
            unheard = None
            lost = unheard_again(held["cues"], new_cues) if reheard else []
            if lost:
                # The second hearing left them out (Whisper skipped them, or the
                # overlap match trimmed them): the first hearing is all there is.
                # Translated with this chunk's lines: queued apart, they took a
                # backlog place and a translator one chunk behind skipped a chunk.
                self._show(lost)
                for cue in new_cues:
                    cue["id"] += len(lost)
            new_cues, self.state["held"] = hold_back_overlap(
                new_cues, tail, idx, self.segment_seconds, shift, final)
            self.board.add(new_cues)
            self._hand_to_translator(idx, window, lost + new_cues, timing, worker, final)
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
            if unheard and not self._superseded():
                # This chunk never got as far as hearing them: show them as they are.
                self._release(unheard, path, worker, final)
            self.stage.whisper_over()
            self.state["last"] = idx
            for leftover in [path, *scratch]:
                try:
                    os.remove(leftover)
                except OSError:
                    pass

    def _release(self, held, path, worker, final):
        """Puts held-back lines on the board and queues their translation, under
        the chunk that heard them. They are not timed as a chunk of their own: a
        batch with no audio would read as captions 0 s behind."""
        cues = held["cues"]
        self._show(cues)
        worker.submit(held["index"], cues, self.runner.timing(path, 0.0), 0.0, keep_all=final,
                      measured=False)

    def _show(self, cues):
        with self.board.lock:
            # A cue's id is its place on the board (live_cue_feed).
            for pos, cue in enumerate(cues):
                cue["id"] = len(self.board.cues) + pos
            self.board.add(cues)

    def release_held(self, worker):
        """At the end of a session: lines still held back have no next chunk to wait for."""
        held = self.state.pop("held", None)
        if held:
            self._release(held, "", worker, True)

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


def unheard_again(held: list, cues: list) -> list:
    """The held lines no cue of the re-hearing chunk overlaps in time."""
    return [h for h in held if not any(c["start"] < h["end"] and c["end"] > h["start"] for c in cues)]


def hold_back_overlap(cues, tail, idx, segment_seconds, shift, final):
    """(cues to show now, held) for one chunk. Lines that start in this chunk's
    last overlap are held back: the next chunk is transcribed with that audio
    in front and hears them whole, where this chunk heard them cut off. Showing
    both gave the same speech twice in two different forms, with the second
    one starting before the first. held is None, or {"index", "cues",
    "covered"}: covered is how far into the next chunk's padding this chunk's
    shown lines reach (live_translate.dedup_overlap). The last chunk of a
    session has no next one, so nothing is held then."""
    if final or not tail or not cues or not tail.get("seconds"):
        return cues, None
    chunk_end = idx * segment_seconds + shift + tail["chunk_seconds"]
    window_start = chunk_end - tail["seconds"]
    keep = len(cues)
    while keep > 0 and cues[keep - 1]["start"] >= window_start:
        keep -= 1
    if keep == len(cues):
        return cues, None
    held = cues[keep:]
    return cues[:keep], {"index": idx, "cues": held,
                         "covered": max(0.0, held[0]["start"] - window_start)}
