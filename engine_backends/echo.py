"""Echo (testing): a key-free engine so a new PC can exercise the whole app.

It is registered only while Developer Mode is on (set_enabled); with it off
the id is unknown and a run naming it is refused. Output is "[EN] <text>" and
costs nothing, so align, translate, review, versions and export all run with
no network, key or model. tests/fake_engine.py subclasses it as "fake".
"""


class EchoEngine:
    name = "echo"
    prefix = "[EN]"
    supports_reference = True

    def __init__(self, api_key: str = None, model: str = "echo-offline"):
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        # No LLM client, so free-form LLM features decline cleanly instead of calling out.
        self.client = None

    def translate_batch(self, zh_lines, context: dict):
        out = []
        for line in zh_lines:
            preview = (line or "").strip()
            if len(preview) > 40:
                preview = preview[:40] + "…"
            out.append(f"{self.prefix} {preview}")
        return out


ENGINE_ID = EchoEngine.name
LABEL = "Echo (testing)"
NOTE = "Test only: returns each line as [EN] text, no model, no key."


def set_enabled(enabled: bool) -> None:
    """Adds or removes the engine from the shared tables, in place: fallback
    and the services hold references to these same objects. Idempotent."""
    from . import engine_registry as reg
    if not enabled:
        reg.ENGINES.pop(ENGINE_ID, None)
        reg.ENGINE_CAPABILITIES.pop(ENGINE_ID, None)
        reg.FREE_ENGINES.discard(ENGINE_ID)
        reg.KEYLESS_ENGINES.discard(ENGINE_ID)
        reg.ENGINE_NOTES.pop(ENGINE_ID, None)
        return
    if ENGINE_ID in reg.ENGINES:
        return
    # Just before Ollama keeps the free engines contiguous in the pickers.
    engines = {}
    for name, cls in reg.ENGINES.items():
        if name == "ollama":
            engines[ENGINE_ID] = EchoEngine
        engines[name] = cls
    engines.setdefault(ENGINE_ID, EchoEngine)
    reg.ENGINES.clear()
    reg.ENGINES.update(engines)
    reg.ENGINE_CAPABILITIES[ENGINE_ID] = frozenset(
        {reg.CAP_TRANSLATE, reg.CAP_LOCAL, reg.CAP_CHEAP})
    reg.FREE_ENGINES.add(ENGINE_ID)
    reg.KEYLESS_ENGINES.add(ENGINE_ID)
    reg.ENGINE_NOTES[ENGINE_ID] = NOTE
