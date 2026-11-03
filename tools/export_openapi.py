"""Write the API's OpenAPI document as JSON, for the generated frontend types.

    python tools/export_openapi.py OUT.json

`cd frontend && npm run gen:api` runs this and then openapi-typescript. The app
is built with auth off, because with auth on the schema is not served at all,
and the document must not depend on the machine's settings.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _refs(node, out):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            out.add(ref.rsplit("/", 1)[1])
        for v in node.values():
            _refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _refs(v, out)


def require_served_fields(doc, skip=frozenset()):
    """Mark every property of a response model required.

    FastAPI serializes defaulted fields, so a client always receives them, but
    the schema lists them as optional and the generated types come out
    `x?: T`. Models of routes that drop None or unset fields (`skip`) keep
    their optional fields, since there the key really may be missing. A model that is also a request body is left alone: there the
    default means "may be left out".
    """
    schemas = doc.get("components", {}).get("schemas", {})
    requests, responses = set(), set()
    for item in doc.get("paths", {}).values():
        for op in item.values():
            if not isinstance(op, dict):
                continue
            _refs(op.get("requestBody", {}), requests)
            _refs(op.get("parameters", []), requests)
            _refs(op.get("responses", {}), responses)
    seen = set()
    todo = list(responses)
    while todo:
        name = todo.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        more = set()
        _refs(schemas[name], more)
        todo.extend(more)
    shared = set()
    todo = list(requests)
    while todo:
        name = todo.pop()
        if name in shared or name not in schemas:
            continue
        shared.add(name)
        more = set()
        _refs(schemas[name], more)
        todo.extend(more)
    kept = set()
    todo = [n for n in skip if n in schemas]
    while todo:
        name = todo.pop()
        if name in kept:
            continue
        kept.add(name)
        more = set()
        _refs(schemas[name], more)
        todo.extend(n for n in more if n in schemas)
    for name in seen - shared - kept:
        schema = schemas[name]
        props = schema.get("properties")
        if props:
            schema["required"] = sorted(props)
    return doc


def build_document():
    sys.path.insert(0, str(ROOT))
    os.environ["BAIHE_API_AUTH"] = "off"
    from api.server import create_app
    app = create_app()
    skip = set()
    for route in app.routes:
        model = getattr(route, "response_model", None)
        if model is not None and (route.response_model_exclude_none or route.response_model_exclude_unset
                                  or route.response_model_exclude_defaults):
            skip.add(model.__name__)
    return require_served_fields(app.openapi(), skip)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__)
        return 2
    Path(argv[0]).write_text(json.dumps(build_document(), indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
