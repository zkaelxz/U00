"""start.bat's update target: the newest fetched commit whose Tests run passed."""

import sys


def main(argv=None) -> int:
    """Prints "ok <sha>", or "no <reason>" and exits 1: start.bat then starts
    the version already on this PC."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("no This folder's git information could not be read.")
        return 1
    try:
        from services import source_update_service
        sha, problem = source_update_service.update_target(*argv)
    except Exception:   # a broken copy must still start; start.bat says it wasn't updated
        sha, problem = None, "Couldn't check which versions passed their tests."
    if sha is None:
        print(f"no {problem}")
        return 1
    print(f"ok {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
