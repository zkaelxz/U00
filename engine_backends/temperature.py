"""The sampling temperature of a translation request.

Only a Benchmark Lab arm sets it (context["temperature"]); a title's run sends no
field and every provider keeps its own default. Claude and OpenAI are left out
because their current models refuse a temperature other than the default, so a
request carrying one would fail instead of translating."""

TEMPERATURE_ENGINES = ("deepseek", "gemini", "ollama")

MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0


def temperature_of(context: dict, engine_name: str):
    """The temperature to send, or None for no field."""
    value = context.get("temperature")
    return value if value is not None and engine_name in TEMPERATURE_ENGINES else None
