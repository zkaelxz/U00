"""
run_tests.py -- convenience wrapper for the test suite.

Just runs pytest with this project's config, but gives a clear message
if pytest isn't installed rather than a bare ImportError.

Usage:
    python run_tests.py            # run everything
    python run_tests.py -k history # run only tests matching "history"
"""

import subprocess
import sys
import importlib.util


def main():
    if importlib.util.find_spec("pytest") is None:
        print("pytest isn't installed. Install it with:\n")
        print("    pip install pytest\n")
        print("(or `pip install -r requirements.txt`, which includes it)")
        return 1

    args = [sys.executable, "-m", "pytest"] + sys.argv[1:]
    return subprocess.call(args)


if __name__ == "__main__":
    sys.exit(main())
