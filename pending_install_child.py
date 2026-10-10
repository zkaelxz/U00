"""
pending_install_child.py -- the two jobs pending_install.py hands to a
short-lived child process, so the apply step itself never imports the app
(see that module). `derive KEYS` prints the pip command re-derived from the
registry; `redact PATH` cleans the text file at PATH for storage with diagnostics_report.redact_for_support.
"""

import json
import sys


def derive(arg: str) -> dict:
    import install_registry
    keys = arg.split(",") if arg else []
    reason = install_registry.unqueueable_reason(keys)
    if not keys or reason:
        return {"ok": False, "message": "The queued install was not run: "
                + (reason or "nothing was listed") + " Nothing was changed."}
    pins_path = install_registry.write_torch_pins(keys)
    return {"ok": True, "argv": install_registry.install_argv(keys, torch_pins_path=pins_path),
            "temp_files": [pins_path] if pins_path else []}


def redact(text: str) -> dict:
    import diagnostics_report
    return {"lines": diagnostics_report.redact_for_support(text).splitlines()}


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "derive":
        out = derive(argv[1])
    elif len(argv) >= 2 and argv[0] == "redact":
        with open(argv[1], encoding="utf-8") as f:
            out = redact(f.read())
    else:
        out = {"ok": False, "message": "unknown command"}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
