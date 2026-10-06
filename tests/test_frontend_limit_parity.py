"""Client-side size limits that mirror server caps must carry the same number."""

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from api.schemas import TranslateRequest
from services import novel_attach_service

SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"


def _ts_number(path, name):
    text = (SRC / path).read_text(encoding="utf-8")
    m = re.search(rf"export const {name} = ([0-9_ *]+)\n", text)
    assert m, f"{name} not found in {path}"
    return eval(m.group(1).replace("_", ""), {"__builtins__": {}})


def test_translate_text_cap_matches_the_schema():
    max_length = next(m.max_length for m in TranslateRequest.model_fields["text"].metadata
                      if hasattr(m, "max_length"))
    assert _ts_number("api/translate.ts", "MAX_TRANSLATE_TEXT_CHARS") == max_length


def test_min_silence_bounds_match_core():
    import core
    assert _ts_number("pages/workspace/sourceForm.ts", "MIN_SILENCE_MS_MIN") == core.MIN_SILENCE_MS_MIN
    assert _ts_number("pages/workspace/sourceForm.ts", "MIN_SILENCE_MS_MAX") == core.MIN_SILENCE_MS_MAX


def test_novel_epub_cap_matches_the_attach_service():
    assert (_ts_number("pages/workspace/stages/novelFile.ts", "MAX_NOVEL_EPUB_BYTES")
            == novel_attach_service.MAX_EPUB_BYTES)
