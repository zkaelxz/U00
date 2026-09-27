"""
`python -m api` -- start the Baihe API server with the settings from
`BAIHE_API_*` (see `api/api_config.py`). Loopback-only unless
`BAIHE_API_HOST` says otherwise.
"""

import portable
portable.activate_portable_mode()

import uvicorn

from api.api_config import load_settings


def main():
    settings = load_settings()
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: the API is listening on {settings.host}, reachable from other "
              "machines on this network, with NO authentication. Only do this on a "
              "network you trust.")
    uvicorn.run("api.server:app", host=settings.host, port=settings.port,
                reload=settings.is_development)


if __name__ == "__main__":
    main()
