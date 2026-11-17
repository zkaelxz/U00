"""Fails when CI's optional-library pytest job skips something its allowlist does not expect.

    python tools/check_pytest_skips.py optional-tests.xml

That job exists to run the tests that need jieba, OpenCV, ebooklib and the like, and pytest
counts a skip as a pass: if one of those libraries stopped being installed, whole test files would
skip and the job would stay green. A skip is keyed by its test file and reason (not line or test
name, which move with every edit), so a known reason in a known file may skip any number of tests,
and a new reason anywhere fails. The allowlist is the skips that job has today: libraries it
deliberately does not install.
"""
import ast
import sys
import xml.etree.ElementTree as ET

ALLOWED = {
    ("tests/test_hardsub_ocr.py", "could not import 'paddleocr': No module named 'paddleocr'"),
    ("tests/test_reader_service.py",
     "could not import 'playwright.sync_api': No module named 'playwright'"),
    ("tests/test_scanlate.py",
     "could not import 'huggingface_hub': No module named 'huggingface_hub'"),
    ("tests/test_scanlate.py", "could not import 'torch': No module named 'torch'"),
}


def skipped(junit_xml: str) -> set[tuple[str, str]]:
    found = set()
    for case in ET.parse(junit_xml).getroot().iter("testcase"):
        skip = case.find("skipped")
        if skip is None:
            continue
        reason = skip.get("message", "")
        # classname is "tests.test_x" or "tests.test_x.TestClass"; a whole file
        # skipped at import has no classname, its module as the name and its
        # reason only inside the ('path', line, 'Skipped: ...') text.
        module = (case.get("classname") or case.get("name", "")).split(".")
        if not case.get("classname"):
            try:
                reason = str(ast.literal_eval(skip.text or "")[2]).removeprefix("Skipped: ")
            except (ValueError, SyntaxError, IndexError, TypeError):
                pass
        path = "/".join(part for part in module if not part[:1].isupper()) + ".py"
        found.add((path, reason))
    return found


def main(argv: list[str]) -> int:
    extra = sorted(skipped(argv[1]) - ALLOWED)
    for path, reason in extra:
        print(f"unexpected skip in {path}: {reason}")
    if extra:
        print("Install what the job is missing, or (if the skip is intended) add it to ALLOWED in "
              "tools/check_pytest_skips.py.")
    return 1 if extra else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
