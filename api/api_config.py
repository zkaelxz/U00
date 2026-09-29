"""
api/api_config.py -- where the FastAPI server's own settings come from.

Deliberately *not* a second configuration system. Everything the app
already configures stays where it is: the library/database location is
`db.LIBRARY_DIR` (the same folder Streamlit and `cli.py` use), portable
mode is `portable.py`, API keys stay in the environment / `.env`. This
module only adds the handful of settings a network server needs that
Streamlit never did, read from `BAIHE_API_*` environment variables in
the same style as the existing `BAIHE_PORTABLE` / `BAIHE_HF_TOKEN` /
`BAIHE_MONTHLY_CAP_USD` variables.

- `BAIHE_API_HOST` (default `127.0.0.1`) -- loopback only by default.
  Setting `0.0.0.0` exposes the API to the LAN with **no
  authentication**; that matches today's trusted-network assumption
  for Streamlit (Step 10e) but is not a remote-access story -- see the
  roadmap's M8-H.
- `BAIHE_API_PORT` (default `8600`) -- not adjacent to Streamlit's 8501
  or the extension bridge's 8756 (`page_server.DEFAULT_PORT`).
- `BAIHE_API_ENV` (`development` or `production`, default
  `production`) -- development turns on CORS for the React dev server's
  origins below. Production sends no CORS headers at all: the built
  frontend is expected to be served same-origin (or through the Vite
  proxy in development), so no cross-origin browser access is needed.
- `BAIHE_API_CORS_ORIGINS` -- comma-separated origins allowed in
  development. Defaults to the Vite dev/preview servers on loopback.
  A literal `*` is refused, not honoured.
- `BAIHE_API_ALLOW_KEY_WRITES` (`1` to enable, default off) -- turns on
  the write-only engine-key endpoints (`POST /api/settings/keys/...`,
  Migration Slice 24). Off: they return 403. On: they still refuse any
  request that looks remote (non-loopback peer/Host, proxy or identity
  headers, cross-origin) and need `confirm=true`. That is a safeguard,
  not authentication -- see docs/migration-review.md.
"""

import os
from dataclasses import dataclass, field

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8600
DEV_CORS_ORIGINS = (
    "http://localhost:5173", "http://127.0.0.1:5173",   # vite dev
    "http://localhost:4173", "http://127.0.0.1:4173",   # vite preview
)


@dataclass(frozen=True)
class ApiSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    environment: str = "production"
    cors_origins: tuple = field(default_factory=tuple)
    allow_key_writes: bool = False

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


def load_settings(environ=None) -> ApiSettings:
    """Reads `BAIHE_API_*` from `environ` (default: `os.environ`). An
    unparseable port or unknown environment raises ValueError at startup
    rather than quietly falling back, so a typo can't silently change
    which interface or mode the server runs in."""
    env = os.environ if environ is None else environ
    host = (env.get("BAIHE_API_HOST") or DEFAULT_HOST).strip()
    port_text = (env.get("BAIHE_API_PORT") or str(DEFAULT_PORT)).strip()
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f"BAIHE_API_PORT must be a number, got {port_text!r}")
    if not 1 <= port <= 65535:
        raise ValueError(f"BAIHE_API_PORT must be 1-65535, got {port}")
    environment = (env.get("BAIHE_API_ENV") or "production").strip().lower()
    if environment not in ("development", "production"):
        raise ValueError(
            f"BAIHE_API_ENV must be 'development' or 'production', got {environment!r}")

    cors_origins = ()
    if environment == "development":
        raw = env.get("BAIHE_API_CORS_ORIGINS")
        origins = ([o.strip() for o in raw.split(",") if o.strip()] if raw
                   else list(DEV_CORS_ORIGINS))
        if "*" in origins:
            raise ValueError("BAIHE_API_CORS_ORIGINS may not be '*'; list the real origins.")
        cors_origins = tuple(origins)
    allow_key_writes = (env.get("BAIHE_API_ALLOW_KEY_WRITES") or "").strip() == "1"
    return ApiSettings(host=host, port=port, environment=environment,
                       cors_origins=cors_origins, allow_key_writes=allow_key_writes)
