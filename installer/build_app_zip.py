"""
installer/build_app_zip.py -- builds BaiheStudio-App-<version>.zip, the app
layer an installed copy can update to without the full installer
(docs/windows-installer-design.md, "App-only updates").

    python installer/build_app_zip.py --version 0.2.0 [--payload build/installer/payload]
                                      [--out build/installer/output]

Run after build_installer.py has staged and checked the payload: the zip is
exactly payload\\app plus `app-manifest.json`, and the manifest's `base` is
taken from the same payload's manifest.json, so an installed copy can tell
whether its Python, wheels, WinSW and Caddy are the ones this app was built
against. Writes the zip and a copy of its manifest
(BaiheStudio-App-<version>.json, which the app reads before downloading).
The release job hashes and signs the zip like the installer.

Standard library only.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAYLOAD = REPO_ROOT / "build" / "installer" / "payload"
DEFAULT_OUT = REPO_ROOT / "build" / "installer" / "output"
# The oldest full install whose launcher and swap helper can take an app
# layer. Raise it when an app layer needs something only a newer installer
# puts in place.
MIN_BASE_VERSION = "0.1.0"


class BuildError(Exception):
    """A plain-words reason the app zip can't be built."""


def _app_swap():
    """installer/app_swap.py, which the installed app uses to check the zip,
    loaded by path like build_installer loads lib/release_signature."""
    spec = importlib.util.spec_from_file_location("_app_swap", REPO_ROOT / "installer" / "app_swap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fresh_schema_fingerprint(app_dir) -> str:
    """The library database this app version makes, from a throwaway data
    folder: the version's db.init_db is run in a child process so this
    script's own process imports nothing from the app."""
    import subprocess
    with tempfile.TemporaryDirectory(prefix="baihe_schema_") as data:
        env = {k: v for k, v in os.environ.items() if not k.startswith("BAIHE_")}
        env["BAIHE_DATA_DIR"] = data
        result = subprocess.run([sys.executable, "-I", "-S", "-c",
                                 "import sys; sys.path.insert(0, sys.argv[1]); "
                                 "import db; db.init_db()", str(app_dir)],
                                env=env, stdin=subprocess.DEVNULL, timeout=300)
        if result.returncode != 0:
            raise BuildError("The app's database setup failed while measuring its schema "
                             "(its error is printed above).")
        return _app_swap().schema_fingerprint_of(Path(data) / "library" / "library.db")


def app_manifest(version, payload_dir, schema_sha256) -> dict:
    swap = _app_swap()
    try:
        installer_manifest = json.loads((Path(payload_dir) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BuildError("The payload has no readable manifest.json; run build_installer.py first.")
    base = swap.base_fingerprint(installer_manifest)
    if base is None:
        raise BuildError("The payload's manifest.json doesn't list the base layer.")
    return {"product": swap.PRODUCT, "layer": "app", "version": version,
            "min_base_version": MIN_BASE_VERSION, "base": base,
            "db_schema_sha256": schema_sha256}


def build_zip(version, payload_dir=DEFAULT_PAYLOAD, out_dir=DEFAULT_OUT, schema_sha256=None) -> Path:
    swap = _app_swap()
    if not swap.valid_version(version):
        raise BuildError("--version may only use letters, digits, '.', '+' and '-'.")
    app_dir = Path(payload_dir) / "app"
    if not (app_dir / "installer" / "launcher.py").is_file():
        raise BuildError(f"{app_dir} isn't a staged app; run build_installer.py first.")
    if schema_sha256 is None:
        schema_sha256 = fresh_schema_fingerprint(app_dir)
    manifest = app_manifest(version, payload_dir, schema_sha256)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"BaiheStudio-App-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(app_dir.rglob("*")):
            if path.is_symlink() or not path.is_file() or "__pycache__" in path.parts:
                continue
            zf.write(path, path.relative_to(app_dir).as_posix())
        zf.writestr(swap.APP_MANIFEST_NAME, json.dumps(manifest, indent=2) + "\n")
    # The installed app refuses what this check refuses; fail here instead.
    try:
        with zipfile.ZipFile(zip_path) as zf:
            swap.check_zip(zf)
            swap.read_manifest(zf)
    except swap.SwapError as e:
        zip_path.unlink()
        raise BuildError(f"The app zip wouldn't be accepted: {e}") from None
    (out_dir / f"BaiheStudio-App-{version}.json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                            encoding="utf-8")
    return zip_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the Baihe Studio app-only update zip.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--payload", default=str(DEFAULT_PAYLOAD))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", args.version):
        print("ERROR: --version may only use letters, digits, '.', '+' and '-'.", file=sys.stderr)
        return 2
    try:
        path = build_zip(args.version, args.payload, args.out)
    except BuildError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
