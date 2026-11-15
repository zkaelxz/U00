"""The sampling temperature of a translation request.

Only a Benchmark Lab arm sets it (context["temperature"]); a title's run sends no
field and every provider keeps its own default, except a local Ollama model, which
translates at OLLAMA_DEFAULT_TEMPERATURE so one page gives one result. Claude and OpenAI are left out
because their current models refuse a temperature other than the default, so a
request carrying one would fail instead of translating."""

TEMPERATURE_ENGINES = ("deepseek", "gemini", "ollama")

MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0


def temperature_of(context: dict, engine_name: str):
    """The temperature to send, or None for no field."""
    value = context.get("temperature")
    return value if value is not None and engine_name in TEMPERATURE_ENGINES else None


# A local model sampled at Ollama's default temperature gave a different line each run
# of the same page ("Justice" / "Carry it out."), which made a fix impossible to compare
# and sometimes flipped a pronoun. Greedy decoding repeats; the seed only matters if a
# future change raises the temperature.
OLLAMA_DEFAULT_TEMPERATURE = 0.0
OLLAMA_DEFAULT_SEED = 1


def ollama_sampling(context: dict | None = None) -> dict:
    """The sampling options for an Ollama request: a Lab arm's temperature when it set
    one, else the repeatable default."""
    value = (context or {}).get("temperature")
    return {"temperature": OLLAMA_DEFAULT_TEMPERATURE if value is None else value,
            "seed": OLLAMA_DEFAULT_SEED}
