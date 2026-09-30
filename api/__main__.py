"""
`python -m api` -- start the Baihe API server with the settings from
`BAIHE_API_*` (see `api/api_config.py`). Loopback-only unless
`BAIHE_API_HOST` says otherwise, and a non-loopback host is refused
unless `BAIHE_API_AUTH=on`.

Local user administration (Step 133). These touch the library database
directly, so only someone at the PC (with file access) can run them; they
print no tokens or hashes:

    python -m api grant-admin <email>   # allowlist/reactivate as admin (also recovery)
    python -m api add-user <email> [--name "Display name"]
                                        # allowlist a household member (default permissions);
                                        # their first Google sign-in binds their account
    python -m api deactivate <email>    # block sign-in and end their sessions
    python -m api grant <email> <permission>   # e.g. media.stream, engines.paid
    python -m api list-users
"""

import portable
portable.activate_portable_mode()

import argparse
import sys


def _serve():
    import uvicorn
    from api.api_config import check_bind_safety, load_settings
    try:
        settings = load_settings()   # also refuses a non-https BAIHE_PUBLIC_URL
        check_bind_safety(settings)
    except ValueError as e:
        raise SystemExit(f"ERROR: {e}")
    if settings.is_development:
        uvicorn.run("api.server:app", host=settings.host, port=settings.port, reload=True)
        return
    # Step 80b: when the installed launcher started us, every child process
    # (ffmpeg, Chromium, pip...) ends with this one (process_guard), and its
    # clean-stop route can make the server exit (shutdown_service).
    import os
    import process_guard
    from services import shutdown_service
    process_guard.contain_children()
    # Neither value is for the processes the server starts.
    shutdown_service.take_token_from_environment()
    os.environ.pop(process_guard.GROUP_NAME_ENV, None)
    # timeout_graceful_shutdown: an open connection (a media stream the app
    # window holds) can't keep a clean stop past the launcher's grace period.
    server = uvicorn.Server(uvicorn.Config("api.server:app", host=settings.host,
                                           port=settings.port, timeout_graceful_shutdown=3))
    shutdown_service.register_stopper(lambda: setattr(server, "should_exit", True))
    try:
        server.run()
    except KeyboardInterrupt:
        pass   # Ctrl+C: a normal stop, as with uvicorn.run


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


def _run(fn) -> int:
    from services.service_errors import ServiceError
    try:
        message = fn()
    except ServiceError as e:
        print(f"ERROR: {e.message}", file=sys.stderr)
        return 2
    print(message)
    return 0


def _user_id(email: str) -> int:
    from services import auth_service
    from services.service_errors import NotFoundError
    user = auth_service.find_user_by_email(email)
    if user is None:
        raise NotFoundError("No such user. Add them first with: python -m api add-user <email>")
    return user["id"]


def _add_user(email: str, name: str) -> int:
    from services import auth_service

    def go():
        user = auth_service.add_user(email, name or "")
        return (f"{user['email']} is allowlisted (user id {user['id']}) with: "
                f"{', '.join(user['permissions'])}. Their first Google sign-in links the account.")
    return _run(go)


def _deactivate(email: str) -> int:
    from services import auth_service

    def go():
        user = auth_service.deactivate_user(_user_id(email))
        return f"{user['email']} is deactivated; their sessions were ended."
    return _run(go)


def _grant(email: str, permission: str) -> int:
    from services import auth_service

    def go():
        user = auth_service.grant_permission(_user_id(email), permission)
        return f"{user['email']} now has: {', '.join(user['permissions'])}"
    return _run(go)


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
    add = sub.add_parser("add-user", help="allowlist an email with the household defaults")
    add.add_argument("email")
    add.add_argument("--name", default="", help="display name shown in the app")
    deactivate = sub.add_parser("deactivate", help="block a user and end their sessions")
    deactivate.add_argument("email")
    grant_perm = sub.add_parser("grant", help="grant one permission to a user")
    grant_perm.add_argument("email")
    grant_perm.add_argument("permission")
    sub.add_parser("list-users", help="list allowlisted users and their permissions")
    args = parser.parse_args(argv)
    if args.command == "grant-admin":
        return _grant_admin(args.email)
    if args.command == "add-user":
        return _add_user(args.email, args.name)
    if args.command == "deactivate":
        return _deactivate(args.email)
    if args.command == "grant":
        return _grant(args.email, args.permission)
    if args.command == "list-users":
        return _list_users()
    _serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
