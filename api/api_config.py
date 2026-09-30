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
  (Step 133, see below).
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
  not authentication -- see docs/archive/migration-review.md. start.bat and
  start.ps1 set it to 1 unless it is already set (2026-09-29), so an
  explicit `0` opts out; `python -m api` run directly leaves it off.
- `BAIHE_API_SERVE_FRONTEND` (`1`/`0`, default on) -- serve the built
  React app (`frontend/dist`) at `/` from the same process. Has no
  effect when `frontend/dist/index.html` doesn't exist (API only).
  Unrelated to CORS and to the host binding.
- `BAIHE_API_AUTH` (`off` default, or `on`) -- Step 133. `off` keeps
  today's behaviour: every request is the local owner with every
  permission, so it also refuses (403) every request that isn't a direct
  loopback one: proxy/forwarding headers, non-loopback Host, peer or
  Origin (`api.auth.LoopbackOnlyGate`); a same-PC reverse proxy can't
  expose it. `on` enforces sessions, permissions and CSRF (see
  `api/auth.py`). A non-loopback `BAIHE_API_HOST` is refused at startup
  unless this is `on` (`check_bind_safety`, run by `python -m api` and
  `create_app`).
- `BAIHE_API_COOKIE_SECURE` (`1` default) -- the session cookie is always
  `Secure` unless this is `0` AND the request is plain-http loopback (dev).
- `BAIHE_API_BACKGROUND` (`1` default, or `0`) -- start the background
  pieces Streamlit used to start (chapter-check scheduler; the extension
  endpoint when its setting is on) when the API starts; see
  `api/background.py`. `ApiSettings()` built directly defaults to off, and
  tests/conftest.py sets `0`, so tests never start them.
- `BAIHE_API_HOUSEHOLD_PORT` (unset by default = off) -- a second listener
  in the same process for other devices, reached only through a reverse
  proxy (Caddy) on this PC; suggested value `8610`. The listener above
  stays the PC's own (the "admin" listener: auth off, loopback only,
  background services, `/api/docs`), and is what start.bat and the
  launcher open. The household listener binds the same loopback host,
  always has sign-in on (`BAIHE_API_AUTH` applies to the admin listener
  only, and must stay `off` while this is set), runs no background
  services, serves no docs or schema, and refuses every `local_only()`
  route and every handler-level "is this the PC" check, whatever the
  request looks like (`household_settings`, `api.auth.is_local_request`).
  Both share the in-memory job list. Refused at startup: the admin port,
  the extension bridge's 8756, a non-loopback host, development mode.
"""

import os
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

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
    background_services: bool = False   # load_settings: on unless BAIHE_API_BACKGROUND=0
    google_client_id: str = field(default="", repr=False)
    google_client_secret: str = field(default="", repr=False)
    public_url: str = ""
    household_port: int = 0       # 0 = no household listener
    listener: str = "admin"       # "admin" | "household" (set by household_settings only)

    @property
    def is_household(self) -> bool:
        return self.listener == "household"

    @property
    def auth_enabled(self) -> bool:
        return self.auth_mode == "on"

    @property
    def sign_in_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.public_url)

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
    background_text = (env.get("BAIHE_API_BACKGROUND") or "1").strip().lower()
    if background_text not in ("1", "0", "true", "false", "yes", "no", "on", "off"):
        raise ValueError(f"BAIHE_API_BACKGROUND must be 1 or 0, got {background_text!r}")
    background_services = background_text in ("1", "true", "yes", "on")
    household_text = (env.get("BAIHE_API_HOUSEHOLD_PORT") or "").strip()
    household_port = 0
    if household_text:
        try:
            household_port = int(household_text)
        except ValueError:
            raise ValueError(
                f"BAIHE_API_HOUSEHOLD_PORT must be a number, got {household_text!r}")
        if not 1 <= household_port <= 65535:
            raise ValueError(f"BAIHE_API_HOUSEHOLD_PORT must be 1-65535, got {household_port}")
    sign_in = _sign_in_values(env, from_env_file=environ is None)
    public_url = normalize_public_url(sign_in["BAIHE_PUBLIC_URL"])
    return ApiSettings(host=host, port=port, environment=environment,
                       cors_origins=cors_origins, allow_key_writes=allow_key_writes,
                       serve_frontend=serve_frontend, auth_mode=auth_mode,
                       cookie_secure=cookie_secure, background_services=background_services,
                       google_client_id=sign_in["BAIHE_GOOGLE_CLIENT_ID"],
                       google_client_secret=sign_in["BAIHE_GOOGLE_CLIENT_SECRET"],
                       public_url=public_url, household_port=household_port)


SIGN_IN_ENV_NAMES = ("BAIHE_GOOGLE_CLIENT_ID", "BAIHE_GOOGLE_CLIENT_SECRET", "BAIHE_PUBLIC_URL")


def _sign_in_values(env, from_env_file: bool) -> dict:
    """The three sign-in settings. At real startup (`environ` not given) the
    project's `.env` is read too -- the same file and parser the engine keys
    use (`settings_service._read_env_file`), with `.env` taking priority over
    the process environment, like `settings_service.resolve_key`."""
    file_env = {}
    if from_env_file:
        from services.settings_service import _read_env_file
        file_env = _read_env_file()
    return {name: (file_env.get(name) or env.get(name) or "").strip()
            for name in SIGN_IN_ENV_NAMES}


_DEV_HTTP_HOSTS = ("localhost", "127.0.0.1", "::1")


def normalize_public_url(value: str) -> str:
    """'' stays ''. Otherwise returns `scheme://host[:port]` with no trailing
    slash, or raises ValueError: the scheme must be https (http only for a
    loopback host, dev), and there may be no path, query, fragment or
    credentials. The message never echoes the value (it's config, but keep
    the startup error free of anything pasted by mistake)."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        parts.port   # raises ValueError on a bad port
    except ValueError:
        raise ValueError("BAIHE_PUBLIC_URL is not a valid URL.")
    if parts.scheme not in ("https", "http") or not hostname:
        raise ValueError("BAIHE_PUBLIC_URL must look like https://your-domain.")
    if "@" in parts.netloc or parts.path.strip("/") or parts.query or parts.fragment:
        raise ValueError("BAIHE_PUBLIC_URL must be just https://your-domain "
                         "(no path, query or user name).")
    if parts.scheme == "http" and hostname.lower() not in _DEV_HTTP_HOSTS:
        raise ValueError("BAIHE_PUBLIC_URL must use https:// (plain http:// is only "
                         "allowed for localhost during development).")
    return f"{parts.scheme}://{parts.netloc}"


def is_loopback_host(host: str) -> bool:
    return (host or "").strip().lower() in ("127.0.0.1", "localhost", "::1", "[::1]")


def check_bind_safety(settings: ApiSettings):
    """Refuses (ValueError) a non-loopback BAIHE_API_HOST while auth is off.
    Called by `python -m api` and by `create_app`. It only sees the env
    setting: a reverse proxy on the same PC, or `uvicorn --host`, gets past
    it, which is why off mode also refuses every non-direct-loopback request
    at request time (`api.auth.LoopbackOnlyGate`)."""
    if not is_loopback_host(settings.host) and not settings.auth_enabled:
        raise ValueError(
            f"Refusing to listen on {settings.host}: it is reachable from other machines and "
            "BAIHE_API_AUTH is not 'on'. Set BAIHE_API_AUTH=on (and allowlist users with "
            "`python -m api grant-admin <email>`), or use the default 127.0.0.1.")
    # Settings marked household by hand (not through household_settings)
    # get the same two guarantees.
    if settings.is_household and not (settings.auth_enabled and is_loopback_host(settings.host)):
        raise ValueError("The household listener needs sign-in on and a loopback host.")
    # ApiSettings built directly (tests, embedding) skip load_settings, so the
    # public URL rule is enforced here too; normalize_public_url is idempotent.
    if settings.public_url and normalize_public_url(settings.public_url) != settings.public_url:
        raise ValueError("BAIHE_PUBLIC_URL must be just https://your-domain.")


def check_household_bind_safety(settings: ApiSettings):
    """Refuses (ValueError) a household listener that could weaken the PC's
    own one: it binds only a loopback host (the reverse proxy on this PC is
    the only way in), never the admin port or the extension bridge's port,
    and only next to an admin listener in off mode (with auth on there, a
    proxy pointed at the admin port would get signed-in access to it).
    Development mode runs one auto-reloading app, so it has no household
    listener. `settings` are the admin listener's (`load_settings`)."""
    import page_server
    port = settings.household_port
    if not port:
        raise ValueError("BAIHE_API_HOUSEHOLD_PORT is not set.")
    if not is_loopback_host(settings.host):
        raise ValueError(f"Refusing a household listener on {settings.host}: it only listens "
                         "on this PC (127.0.0.1), behind the reverse proxy.")
    if port == settings.port:
        raise ValueError(f"BAIHE_API_HOUSEHOLD_PORT ({port}) must differ from BAIHE_API_PORT, "
                         "which is the PC's own listener.")
    if port == page_server.DEFAULT_PORT:
        raise ValueError(f"BAIHE_API_HOUSEHOLD_PORT may not be {port}: that is the browser "
                         "extension's port.")
    if settings.auth_enabled:
        raise ValueError("BAIHE_API_HOUSEHOLD_PORT needs BAIHE_API_AUTH=off: the household "
                         "listener has sign-in on, and the PC's own listener stays loopback-only.")
    if settings.is_development:
        raise ValueError("BAIHE_API_HOUSEHOLD_PORT is not available with "
                         "BAIHE_API_ENV=development.")


def household_settings(settings: ApiSettings) -> ApiSettings:
    """The household listener's settings, derived from the admin listener's
    after `check_household_bind_safety`: same loopback host, the household
    port, sign-in on, no background services, no engine-key writes."""
    check_household_bind_safety(settings)
    return replace(settings, port=settings.household_port, auth_mode="on",
                   background_services=False, allow_key_writes=False,
                   listener="household")
