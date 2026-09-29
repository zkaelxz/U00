"""
`python -m api` -- start the Baihe API server with the settings from
`BAIHE_API_*` (see `api/api_config.py`). Loopback-only unless
`BAIHE_API_HOST` says otherwise.
"""

import portable
portable.activate_portable_mode()

import uvicorn

from api.api_config import check_bind_safety, load_settings


def main():
    settings = load_settings()
    try:
        check_bind_safety(settings)
    except ValueError as e:
        raise SystemExit(f"ERROR: {e}")
    uvicorn.run("api.server:app", host=settings.host, port=settings.port,
                reload=settings.is_development)


if __name__ == "__main__":
    main()
