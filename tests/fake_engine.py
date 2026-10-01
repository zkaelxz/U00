"""A deterministic, key-free translation engine for tests and the e2e server.

It is not part of the app: install() registers it under the id "fake"
in translate_engines' tables, uninstall() puts them back. Output is
"[TEST] <text>" and costs nothing, so whole pipelines (align, translate,
review, export, dub) run with no network, key or model.
"""
import translate_engines as te

ENGINE_ID = "fake"


class FakeEngine:
    __test__ = False  # not a pytest class
    name = ENGINE_ID
    supports_reference = True

    def __init__(self, api_key: str = None, model: str = "test-offline"):
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        self.client = None  # no LLM client: free-form LLM features decline cleanly

    def translate_batch(self, zh_lines, context: dict):
        out = []
        for line in zh_lines:
            preview = (line or "").strip()
            if len(preview) > 40:
                preview = preview[:40] + "…"
            out.append(f"[TEST] {preview}")
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
    _saved = (dict(te.ENGINES), dict(te.ENGINE_CAPABILITIES), set(te.FREE_ENGINES),
              set(te.KEYLESS_ENGINES), dict(te.ENGINE_NOTES))
    engines = {}
    for name, cls in te.ENGINES.items():
        if name == "ollama":
            engines[ENGINE_ID] = FakeEngine
        engines[name] = cls
    te.ENGINES.clear()
    te.ENGINES.update(engines)
    te.ENGINE_CAPABILITIES[ENGINE_ID] = frozenset({te.CAP_TRANSLATE, te.CAP_LOCAL, te.CAP_CHEAP})
    te.FREE_ENGINES.add(ENGINE_ID)
    te.KEYLESS_ENGINES.add(ENGINE_ID)
    te.ENGINE_NOTES[ENGINE_ID] = "🧪 Free: fake output for tests, no AI."


def uninstall():
    global _saved
    if _saved is None:
        return
    engines, caps, free, keyless, notes = _saved
    for table, original in ((te.ENGINES, engines), (te.ENGINE_CAPABILITIES, caps),
                            (te.ENGINE_NOTES, notes)):
        table.clear()
        table.update(original)
    for table, original in ((te.FREE_ENGINES, free), (te.KEYLESS_ENGINES, keyless)):
        table.clear()
        table.update(original)
    _saved = None
