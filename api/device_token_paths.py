"""
api/device_token_paths.py -- the paths of require_device_token() routes.

EarlyAuthGate demands a session for every non-public /api path; these
routes carry a bearer token that their own dependency checks instead, so the
gate treats their (parameter-free) paths like public ones and the route
decides.
"""


def device_token_paths(app) -> frozenset:
    from api.auth import iter_route_declarations
    return frozenset(path for _r, path, _m, decls in iter_route_declarations(app)
                     if any(kind == "device_token" for kind, _label in decls))
