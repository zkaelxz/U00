"""Copies the Quick read debug pages (picture + read-step and final regions) into a folder for the comic thread."""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
from services import quick_read_keep  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", help="folder to copy into (made if missing)")
    parser.add_argument("--newest", type=int, default=0, help="copy only the newest N pages (default: all)")
    args = parser.parse_args()
    source = os.path.join(db.LIBRARY_DIR, quick_read_keep.DIRNAME)
    if not os.path.isdir(source):
        print("Nothing kept yet: turn on Settings > Developer Mode > Keep Quick read pages, then read a chapter.")
        return 1
    stems = sorted((n[:-5] for n in os.listdir(source) if n.endswith(".json")),
                   key=lambda s: os.path.getmtime(os.path.join(source, s + ".json")), reverse=True)
    if args.newest:
        stems = stems[:args.newest]
    os.makedirs(args.dest, exist_ok=True)
    copied = 0
    for stem in stems:
        for name in os.listdir(source):
            if name.startswith(stem + "."):
                shutil.copy2(os.path.join(source, name), os.path.join(args.dest, name))
        copied += 1
    print(f"Copied {copied} pages to {args.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
