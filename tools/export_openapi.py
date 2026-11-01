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


def build_document():
    sys.path.insert(0, str(ROOT))
    os.environ["BAIHE_API_AUTH"] = "off"
    from api.server import create_app
    return create_app().openapi()


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
