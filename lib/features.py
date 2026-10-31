"""Kill switches for whole feature areas.

A switch is off only when its name is listed in `BAIHE_FEATURES_OFF`
(comma-separated, e.g. `BAIHE_FEATURES_OFF=jellyfin,sources`, in `.env` or the
environment; `api.api_config.load_settings` reads it at server start). Unset or
empty means everything is on, so an upgrade changes nothing. It is a startup
value rather than a Settings row because it exists for when a site adapter or an
external server breaks and the person needs the area out of the way until the
fix lands.

An unknown name is ignored instead of refused: a typo or a name left from a
newer version must not stop the server from starting.
"""

ENV_VAR = "BAIHE_FEATURES_OFF"
FEATURES = ("jellyfin", "sources", "extension")


def parse(raw) -> frozenset:
    names = {part.strip().lower() for part in (raw or "").split(",")}
    return frozenset(names & set(FEATURES))


def status(off) -> dict:
    return {name: name not in off for name in FEATURES}


def unknown(raw) -> tuple:
    names = [part.strip().lower() for part in (raw or "").split(",")]
    return tuple(n for n in names if n and n not in FEATURES)
