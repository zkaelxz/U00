"""Flags translated lines whose English does not match their source, by comparing multilingual embeddings."""
import json
import logging
import math

from lib import http

from .shared import PROVIDER_RESPONSE_MAX_BYTES, redact_secrets

log = logging.getLogger(__name__)

ALIGNMENT_FLAG = "source_mismatch"
FLAG_NOTE_PREFIX = "Source match: "
EMBED_MODEL = "bge-m3"

# Chosen from a 14-pair zh/ja/ko hand check where right pairs scored 0.79-0.95 and
# another line's English at most 0.68. Real series have not been measured, so the
# bar sits between those and a flag only asks for a look.
MIN_SIMILARITY = 0.55
# A one- or two-character line ("はい", "嗯") has too little for an embedding to compare.
MIN_SOURCE_CHARS = 3
EMBED_BATCH = 32
# The first request can load the model into memory, so it gets longer than a chat tick.
EMBED_TIMEOUT = 120


def embed(base_url: str, texts: list, model: str = EMBED_MODEL):
    """One vector per text from Ollama's /api/embed, or None when the server, the
    model or the reply is unusable. A missing model must never fail the translate run."""
    vectors = []
    for start in range(0, len(texts), EMBED_BATCH):
        chunk = texts[start:start + EMBED_BATCH]
        try:
            resp = http.post(f"{base_url}/api/embed", timeout=EMBED_TIMEOUT,
                             max_bytes=PROVIDER_RESPONSE_MAX_BYTES, guard=None,
                             deadline=2 * EMBED_TIMEOUT, json={"model": model, "input": chunk})
            if not resp.ok:
                log.warning("Source-match check skipped: Ollama returned HTTP %s for %s "
                            "(is it pulled? `ollama pull %s`)", resp.status, model, model)
                return None
            got = json.loads(resp.body).get("embeddings")
        except (http.FetchError, ValueError) as exc:
            log.warning("Source-match check skipped: %s", redact_secrets(str(exc)))
            return None
        if not isinstance(got, list) or len(got) != len(chunk):
            return None
        vectors.extend(got)
    return vectors


def cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def flag_note(score: float) -> str:
    return f"{FLAG_NOTE_PREFIX}the English reads unlike its source line (similarity {score:.2f})"


def flag_mismatches(lines, embed_texts, min_similarity: float = MIN_SIMILARITY) -> int:
    """Flags lines whose English is far from their source; returns how many it flagged.

    embed_texts(list of str) returns one vector per text, or None when embedding is
    unavailable, which leaves every line as it was. A line that already carries
    another flag keeps it; one only this check flagged earlier is cleared once it passes."""
    todo = [ln for ln in lines
            if (ln.en or "").strip() and len((ln.zh or "").strip()) >= MIN_SOURCE_CHARS]
    if not todo:
        return 0
    vectors = embed_texts([ln.zh for ln in todo] + [ln.en for ln in todo])
    if vectors is None or len(vectors) != 2 * len(todo):
        return 0
    flagged = 0
    for i, ln in enumerate(todo):
        score = cosine(vectors[i], vectors[len(todo) + i])
        if score < min_similarity:
            if not ln.flag or ln.flag == ALIGNMENT_FLAG:
                ln.flag, ln.flag_note = ALIGNMENT_FLAG, flag_note(score)
                flagged += 1
        elif ln.flag == ALIGNMENT_FLAG:
            ln.flag, ln.flag_note = None, ""
    return flagged
