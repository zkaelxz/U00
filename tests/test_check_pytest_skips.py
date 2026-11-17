"""tools/check_pytest_skips.py: the optional-library job fails on a skip it doesn't expect."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import check_pytest_skips as check  # noqa: E402

TORCH = "could not import 'torch': No module named 'torch'"
EBOOKLIB = "could not import 'ebooklib': No module named 'ebooklib'"


def _junit(tmp_path, cases: str) -> str:
    path = tmp_path / "optional.xml"
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{cases}'
                    '</testsuite></testsuites>', encoding="utf-8")
    return str(path)


def test_allowed_skips_and_passes_are_fine(tmp_path, capsys):
    xml = _junit(tmp_path,
                 '<testcase classname="tests.test_scanlate.TestInpaint" name="a"/>'
                 f'<testcase classname="tests.test_scanlate.TestLama" name="b">'
                 f'<skipped message="{TORCH}">x</skipped></testcase>'
                 f'<testcase classname="tests.test_scanlate" name="c">'
                 f'<skipped message="{TORCH}">x</skipped></testcase>')
    assert check.main(["", xml]) == 0
    assert capsys.readouterr().out == ""


def test_a_known_reason_in_another_file_fails(tmp_path, capsys):
    xml = _junit(tmp_path, f'<testcase classname="tests.test_segment.TestX" name="a">'
                           f'<skipped message="{TORCH}">x</skipped></testcase>')
    assert check.main(["", xml]) == 1
    assert "tests/test_segment.py" in capsys.readouterr().out


def test_a_whole_file_skipped_at_import_is_named_with_its_reason(tmp_path, capsys):
    text = f"('/ci/tests/test_epub_io.py', 12, \"Skipped: {EBOOKLIB}\")"
    xml = _junit(tmp_path, '<testcase classname="" name="tests.test_epub_io">'
                           f'<skipped message="collection skipped">{text}</skipped></testcase>')
    assert check.main(["", xml]) == 1
    assert f"tests/test_epub_io.py: {EBOOKLIB}" in capsys.readouterr().out
