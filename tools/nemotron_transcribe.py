"""Try Nemotron-3.5-Transcribe on a clip through a local NeMo-Speech.cpp server and write an SRT to compare.

    python tools/nemotron_transcribe.py AUDIO.mp4 --language zh [--start 0 --end 600] [--diarize]
        [--server http://127.0.0.1:8080] [--out OUT.srt]

Start the server yourself first (`nemo-speech serve --asr-model DECODER.gguf`, see "Trying
Nemotron-3.5-Transcribe" in docs/asr-experiments.md). The tool cuts the clip to 16 kHz mono WAV
with ffmpeg, posts it to /v1/audio/transcriptions, and groups the returned words into lines.
Nothing is read from or saved to the app's library, and nothing is installed. Standard library
only, so it runs without the app's packages.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid

DEFAULT_SERVER = "http://127.0.0.1:8080"
# The audio is the user's own recording; it must not leave the PC, so only a loopback server is accepted.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
REQUEST_TIMEOUT_S = 3600
FFMPEG_TIMEOUT_S = 600
# A pause this long, or a speaker change, starts a new line.
LINE_GAP_S = 0.8
# Subtitle lines read badly past this many characters (CJK) or words (spaced languages).
MAX_CJK_CHARS = 30
MAX_SPACED_WORDS = 14
CJK_LANGUAGES = ("zh", "ja", "ko")


def check_server(url: str) -> str:
    """The server URL with no trailing slash, or ValueError when it is not a loopback address."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or (parts.hostname or "").lower() not in LOOPBACK_HOSTS:
        raise ValueError("The server must be on this PC (127.0.0.1 or localhost): "
                         "the audio would otherwise leave it.")
    return url.rstrip("/")


def cut_wav(source: str, dest: str, start: float | None, end: float | None) -> None:
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", source]
    if end is not None:
        cmd += ["-t", f"{end - (start or 0):.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dest]
    subprocess.run(cmd, check=True, timeout=FFMPEG_TIMEOUT_S)


def multipart(fields: dict, file_field: str, file_name: str, file_bytes: bytes):
    boundary = uuid.uuid4().hex
    out = bytearray()
    for key, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
    out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_name}"\r\nContent-Type: audio/wav\r\n\r\n').encode()
    out += file_bytes + f"\r\n--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def transcribe(server: str, wav_path: str, language: str | None, diarize: bool) -> dict:
    """The server's verbose_json reply: {"text", "language", "duration", "words": [{word, start, end, speaker?}]}."""
    fields = {"response_format": "verbose_json", "timestamp_granularities[]": "word"}
    if language:
        fields["language"] = language
    if diarize:
        fields["diarization"] = "true"
    with open(wav_path, "rb") as fh:
        body, content_type = multipart(fields, "file", "clip.wav", fh.read())
    req = urllib.request.Request(server + "/v1/audio/transcriptions", data=body,
                                 headers={"Content-Type": content_type}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"The server answered {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the server at {server}: {exc.reason}") from None


def group_words(words: list, language: str | None, offset: float = 0.0) -> list:
    """Lines {"start", "end", "text", "speaker"} from timed words. A line ends at a pause of LINE_GAP_S,
    a speaker change, or the length limit; times are shifted by `offset` (the clip's start in the source)."""
    cjk = (language or "") in CJK_LANGUAGES
    joiner = "" if cjk else " "
    lines, cur = [], []

    def flush():
        if cur:
            lines.append({"start": cur[0]["start"] + offset, "end": cur[-1]["end"] + offset,
                          "text": joiner.join(w["word"].strip() for w in cur).strip(),
                          "speaker": cur[0].get("speaker")})
            cur.clear()

    for w in words:
        if not str(w.get("word", "")).strip():
            continue
        if cur:
            gap = w["start"] - cur[-1]["end"]
            size = sum(len(x["word"].strip()) for x in cur) if cjk else len(cur)
            if (gap >= LINE_GAP_S or w.get("speaker") != cur[-1].get("speaker")
                    or size >= (MAX_CJK_CHARS if cjk else MAX_SPACED_WORDS)):
                flush()
        cur.append(w)
    flush()
    return lines


def srt_time(seconds: float) -> str:
    ms = int(round(max(seconds, 0.0) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def to_srt(lines: list) -> str:
    blocks = []
    for i, ln in enumerate(lines, 1):
        label = f"[{ln['speaker']}] " if ln.get("speaker") not in (None, "") else ""
        blocks.append(f"{i}\n{srt_time(ln['start'])} --> {srt_time(ln['end'])}\n{label}{ln['text']}\n")
    return "\n".join(blocks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("audio", help="audio or video file")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--language", help="zh, ja or ko; omit to let the model detect it")
    ap.add_argument("--start", type=float, help="clip start in seconds")
    ap.add_argument("--end", type=float, help="clip end in seconds")
    ap.add_argument("--diarize", action="store_true", help="ask for speaker labels")
    ap.add_argument("--out", help="SRT path; default is AUDIO.nemotron.srt beside the input")
    args = ap.parse_args(argv)
    try:
        server = check_server(args.server)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    if not os.path.isfile(args.audio):
        print("The audio file does not exist.", file=sys.stderr)
        return 2
    out = args.out or os.path.splitext(args.audio)[0] + ".nemotron.srt"
    if os.path.exists(out):
        print(f"{out} already exists; pass --out for a new name.", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "clip.wav")
        cut_wav(args.audio, wav, args.start, args.end)
        reply = transcribe(server, wav, args.language, args.diarize)
    words = reply.get("words") or []
    if not words:
        print("The server returned no word timings. Text only:\n" + str(reply.get("text", "")), file=sys.stderr)
        return 1
    lines = group_words(words, args.language or reply.get("language"), offset=args.start or 0.0)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(to_srt(lines))
    print(f"Wrote {len(lines)} lines to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
