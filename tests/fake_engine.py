"""A deterministic, key-free translation engine for tests and the e2e server.

The class is engine_backends.echo.EchoEngine with the "[TEST]" prefix and token
counts. It is not part of the app: install() registers it under the id "fake"
in engine_backends' tables, uninstall() puts them back. Output is
"[TEST] <text>" and costs nothing, so whole pipelines (align, translate,
review, export, dub) run with no network, key or model.
"""
from engine_backends import engine_registry
from engine_backends.echo import EchoEngine

ENGINE_ID = "fake"


class FakeEngine(EchoEngine):
    __test__ = False  # not a pytest class
    name = ENGINE_ID
    prefix = "[TEST]"

    def __init__(self, api_key: str = None, model: str = "test-offline"):
        super().__init__(api_key, model)

    def translate_batch(self, zh_lines, context: dict):
        out = super().translate_batch(zh_lines, context)
        self.last_usage = {
            "input_tokens": sum(len(l) for l in zh_lines),
            "output_tokens": sum(len(o) for o in out),
        }
        return out


_saved = None


def install():
    """Idempotent. Registers the engine just before Ollama, so the free
    engines stay contiguous in ENGINES' order."""
    global _saved
    if _saved is not None:
        return
    _saved = (dict(engine_registry.ENGINES), dict(engine_registry.ENGINE_CAPABILITIES), set(engine_registry.FREE_ENGINES),
              set(engine_registry.KEYLESS_ENGINES), dict(engine_registry.ENGINE_NOTES))
    engines = {}
    for name, cls in engine_registry.ENGINES.items():
        if name == "ollama":
            engines[ENGINE_ID] = FakeEngine
        engines[name] = cls
    engine_registry.ENGINES.clear()
    engine_registry.ENGINES.update(engines)
    engine_registry.ENGINE_CAPABILITIES[ENGINE_ID] = frozenset({engine_registry.CAP_TRANSLATE, engine_registry.CAP_LOCAL, engine_registry.CAP_CHEAP})
    engine_registry.FREE_ENGINES.add(ENGINE_ID)
    engine_registry.KEYLESS_ENGINES.add(ENGINE_ID)
    engine_registry.ENGINE_NOTES[ENGINE_ID] = "Free: fake output for tests, no AI."


def uninstall():
    global _saved
    if _saved is None:
        return
    engines, caps, free, keyless, notes = _saved
    for table, original in ((engine_registry.ENGINES, engines), (engine_registry.ENGINE_CAPABILITIES, caps),
                            (engine_registry.ENGINE_NOTES, notes)):
        table.clear()
        table.update(original)
    for table, original in ((engine_registry.FREE_ENGINES, free), (engine_registry.KEYLESS_ENGINES, keyless)):
        table.clear()
        table.update(original)
    _saved = None


# A second stand-in that is translation-only (no instruction following), so the
# guards that refuse such an engine stay covered now that no real one exists.
MT_ENGINE_ID = "fake_mt"


class FakeMTEngine(FakeEngine):
    name = MT_ENGINE_ID
    supports_reference = False


_mt_installed = False


def install_mt():
    global _mt_installed
    if _mt_installed:
        return
    engine_registry.ENGINES[MT_ENGINE_ID] = FakeMTEngine
    engine_registry.ENGINE_CAPABILITIES[MT_ENGINE_ID] = frozenset({engine_registry.CAP_TRANSLATE, engine_registry.CAP_LOCAL, engine_registry.CAP_CHEAP})
    engine_registry.FREE_ENGINES.add(MT_ENGINE_ID)
    engine_registry.KEYLESS_ENGINES.add(MT_ENGINE_ID)
    engine_registry.ENGINE_NOTES[MT_ENGINE_ID] = "Free: fake translation-only output for tests."
    engine_registry.TRANSLATION_ONLY_ENGINES.add(MT_ENGINE_ID)
    _mt_installed = True


def uninstall_mt():
    global _mt_installed
    if not _mt_installed:
        return
    engine_registry.ENGINES.pop(MT_ENGINE_ID, None)
    engine_registry.ENGINE_CAPABILITIES.pop(MT_ENGINE_ID, None)
    engine_registry.FREE_ENGINES.discard(MT_ENGINE_ID)
    engine_registry.KEYLESS_ENGINES.discard(MT_ENGINE_ID)
    engine_registry.ENGINE_NOTES.pop(MT_ENGINE_ID, None)
    engine_registry.TRANSLATION_ONLY_ENGINES.discard(MT_ENGINE_ID)
    _mt_installed = False
