"""Test stand-in for the voice engines: routes narration through a per-line fake instead of a voice venv."""

from voice import dub_narration
from voice.dub import VENV_LINE_FAILED
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


class FakeFfmpeg:
    """Stands in for the ffmpeg that encodes the narration track: keeps the command and the
    bytes fed to it, writes the output file once its input is closed, and can fail like the
    real one (a non-zero exit with stderr text, or no longer reading after stop_after bytes).
    on_write(chunk) runs on every write, for checks made while the track is streaming."""

    def __init__(self, returncode=0, stderr=b"", stop_after=None, on_write=None):
        self.returncode, self.stderr_text, self.stop_after, self.on_write = returncode, stderr, stop_after, on_write
        self.cmd, self.fed, self.proc = None, bytearray(), None

    def __call__(self, cmd, stderr):
        self.cmd = cmd
        stderr.write(self.stderr_text)
        stderr.flush()
        outer = self

        class _Stdin:
            closed = False

            def write(self, chunk):
                if outer.proc.killed:
                    raise BrokenPipeError(32, "Broken pipe")
                if outer.stop_after is not None and len(outer.fed) >= outer.stop_after:
                    raise BrokenPipeError(32, "Broken pipe")
                outer.fed.extend(chunk)
                if outer.on_write:
                    outer.on_write(chunk)

            def close(self):
                if not self.closed and not outer.proc.killed and outer.returncode == 0:
                    with open(cmd[-1], "wb") as f:
                        f.write(b"fLaC")
                self.closed = True

        class _Proc:
            killed, returncode = False, None
            stdin = _Stdin()

            def kill(self):
                self.killed = True

            def wait(self, timeout=None):
                self.returncode = -9 if self.killed else outer.returncode
                return self.returncode

        self.proc = _Proc()
        return self.proc


def use_fake_ffmpeg(monkeypatch, **kwargs) -> FakeFfmpeg:
    ffmpeg = FakeFfmpeg(**kwargs)
    monkeypatch.setattr(dub_narration, "_start_ffmpeg", ffmpeg)
    return ffmpeg
