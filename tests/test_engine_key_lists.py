"""The Settings key list exists in Python and, copied by hand, in the
frontend. These tests read the TypeScript source so a new engine added on
one side only fails CI."""
import os
import re

import translate_engines
from services import settings_service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ts(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _secret_engines():
    block = re.search(r"(?:export )?const SECRET_ENGINES[^=]*=\s*\[(.*?)\n\]", _ts("frontend/src/pages/settingsKeys.ts"), re.S)
    assert block, "SECRET_ENGINES literal not found"
    return re.findall(r"engine:\s*'([^']+)'", block.group(1))


def _engine_labels():
    block = re.search(r"export const ENGINE_LABELS[^=]*=\s*\{(.*?)\n\}", _ts("frontend/src/labels.ts"), re.S)
    assert block, "ENGINE_LABELS literal not found"
    return set(re.findall(r"^\s*([a-z_]+):", block.group(1), re.M))


def test_frontend_secret_engines_are_exactly_the_writable_keys():
    found = _secret_engines()
    assert len(found) == len(set(found))
    assert set(found) == set(settings_service.KEY_WRITE_ENGINES)


def test_every_writable_key_has_an_env_name():
    assert set(settings_service.KEY_WRITE_ENGINES) <= set(settings_service.ENV_NAMES)


def test_frontend_labels_cover_every_engine_and_key():
    labels = _engine_labels()
    # "fake" is the test suite's own engine (tests/fake_engine.py), not the app's; likewise "fake_mt".
    needed = (set(translate_engines.ENGINES) - {"fake", "fake_mt"}) | (set(settings_service.ENV_NAMES) - {"monthly_cap_usd"})
    assert needed - labels == set()


def test_frontend_labels_have_no_unknown_extras():
    known = set(translate_engines.ENGINES) | set(settings_service.ENV_NAMES)
    assert _engine_labels() - known == set()
