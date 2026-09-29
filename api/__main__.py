"""
`python -m api` -- start the Baihe API server with the settings from
`BAIHE_API_*` (see `api/api_config.py`). Loopback-only unless
`BAIHE_API_HOST` says otherwise, and a non-loopback host is refused
unless `BAIHE_API_AUTH=on`.

Local user administration (Step 133). These touch the library database
directly, so only someone at the PC (with file access) can run them; they
print no tokens or hashes:

    python -m api grant-admin <email>   # allowlist/reactivate as admin (also recovery)
    python -m api list-users
"""

import portable
portable.activate_portable_mode()

import argparse
import sys


def _serve():
    import uvicorn
    from api.api_config import check_bind_safety, load_settings
    settings = load_settings()
    try:
        check_bind_safety(settings)
    except ValueError as e:
        raise SystemExit(f"ERROR: {e}")
    uvicorn.run("api.server:app", host=settings.host, port=settings.port,
                reload=settings.is_development)


def _grant_admin(email: str) -> int:
    from services import auth_service
    from services.service_errors import ServiceError
    try:
        user = auth_service.grant_admin_local(email)
    except ServiceError as e:
        print(f"ERROR: {e.message}", file=sys.stderr)
        return 2
    print(f"{user['email']} is now an active admin (user id {user['id']}).")
    return 0


def _list_users() -> int:
    from services import auth_service
    users = auth_service.list_users()
    if not users:
        print("No users. Add the first admin with: python -m api grant-admin <email>")
        return 0
    for u in users:
        flags = ", ".join(f for f, on in (("admin", u["is_admin"]),
                                           ("inactive", not u["is_active"]),
                                           ("google-bound", u["has_google_binding"])) if on)
        print(f"{u['id']:>4}  {u['email']}  [{flags or 'user'}]  "
              f"{', '.join(u['permissions']) or '(no permissions)'}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m api",
                                     description="Run the Baihe API, or manage its users locally.")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the API server (the default)")
    grant = sub.add_parser("grant-admin", help="allowlist an email as an active admin")
    grant.add_argument("email")
    sub.add_parser("list-users", help="list allowlisted users and their permissions")
    args = parser.parse_args(argv)
    if args.command == "grant-admin":
        return _grant_admin(args.email)
    if args.command == "list-users":
        return _list_users()
    _serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
