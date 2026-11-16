"""
pending_install_child.py -- the two jobs pending_install.py hands to a
short-lived child process, so the apply step itself never imports the app
(see that module). `derive KEYS` prints the pip command (plus any plain-OpenCV
uninstall steps that must run first) re-derived from the registry; `redact PATH` cleans the text file at PATH for storage with diagnostics_report.redact_for_support.
"""

import json
import sys


def derive(arg: str, pins_arg: str = "") -> dict:
    import install_registry
    keys = arg.split(",") if arg else []
    reason = install_registry.unqueueable_reason(keys)
    if not keys or reason:
        return {"ok": False, "message": "The queued install was not run: "
                + (reason or "nothing was listed") + " Nothing was changed."}
    pins = dict(item.split("=", 1) for item in pins_arg.split(",") if "=" in item)
    # An update pins each package to the version the owner was shown.
    specs = ([f"{install_registry.diagnostics.pip_install_name(k)}=={pins[k]}" if k in pins
              else install_registry.diagnostics.pip_install_name(k) for k in keys] if pins else None)
    pins_path = install_registry.write_torch_pins(keys)
    import pending_install
    swap = install_registry.cv2_swap_names(keys, pending_install.snapshot())
    return {"ok": True, "argv": install_registry.install_argv(keys, torch_pins_path=pins_path, specs=specs),
            "uninstall": [] if specs else [install_registry.uninstall_argv(n) for n in swap],
            "temp_files": [pins_path] if pins_path else []}


def redact(text: str) -> dict:
    import diagnostics_report
    return {"lines": diagnostics_report.redact_for_support(text).splitlines()}


def main(argv) -> int:
    if len(argv) >= 2 and argv[0] == "derive":
        out = derive(argv[1], argv[2] if len(argv) > 2 else "")
    elif len(argv) >= 2 and argv[0] == "redact":
        with open(argv[1], encoding="utf-8") as f:
            out = redact(f.read())
    else:
        out = {"ok": False, "message": "unknown command"}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
