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
  A non-loopback host is refused at startup unless `BAIHE_API_AUTH=on`
  (Step 133, see below); the API has no authentication otherwise.
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
- `BAIHE_API_SERVE_FRONTEND` (`1`/`0`, default on) -- serve the built
  React app (`frontend/dist`) at `/` from the same process. Has no
  effect when `frontend/dist/index.html` doesn't exist (API only).
  Unrelated to CORS and to the host binding.
- `BAIHE_API_AUTH` (`off` default, or `on`) -- Step 133. `off` keeps
  today's behaviour: every request is the local owner with every
  permission. `on` enforces sessions, permissions and CSRF (see
  `api/auth.py`). A non-loopback `BAIHE_API_HOST` is refused unless this
  is `on` (`check_bind_safety`, called by `python -m api`).
- `BAIHE_API_COOKIE_SECURE` (`1` default) -- the session cookie is always
  `Secure` unless this is `0` AND the request is plain-http loopback (dev).
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
    serve_frontend: bool = True
    auth_mode: str = "off"        # "off" | "on"
    cookie_secure: bool = True

    @property
    def auth_enabled(self) -> bool:
        return self.auth_mode == "on"

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
    serve_text = (env.get("BAIHE_API_SERVE_FRONTEND") or "1").strip().lower()
    if serve_text in ("1", "true", "yes", "on"):
        serve_frontend = True
    elif serve_text in ("0", "false", "no", "off"):
        serve_frontend = False
    else:
        raise ValueError(f"BAIHE_API_SERVE_FRONTEND must be 1 or 0, got {serve_text!r}")
    auth_mode = (env.get("BAIHE_API_AUTH") or "off").strip().lower()
    if auth_mode not in ("off", "on"):
        raise ValueError(f"BAIHE_API_AUTH must be 'off' or 'on', got {auth_mode!r}")
    cookie_secure = (env.get("BAIHE_API_COOKIE_SECURE") or "1").strip() != "0"
    return ApiSettings(host=host, port=port, environment=environment,
                       cors_origins=cors_origins, allow_key_writes=allow_key_writes,
                       serve_frontend=serve_frontend, auth_mode=auth_mode,
                       cookie_secure=cookie_secure)


def is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in ("127.0.0.1", "localhost", "::1", "[::1]")


def check_bind_safety(settings: ApiSettings):
    """Refuses (ValueError) to listen on a non-loopback address while
    authentication is off, so an unauthenticated API can't be exposed by
    a single env var."""
    if not is_loopback_host(settings.host) and not settings.auth_enabled:
        raise ValueError(
            f"Refusing to listen on {settings.host}: it is reachable from other machines and "
            "BAIHE_API_AUTH is not 'on'. Set BAIHE_API_AUTH=on (and allowlist users with "
            "`python -m api grant-admin <email>`), or use the default 127.0.0.1.")
