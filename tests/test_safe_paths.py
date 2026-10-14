import os

import pytest

from lib.safe_paths import plain_file_in


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "title"
    d.mkdir()
    (d / "audio.wav").write_bytes(b"x")
    return str(d)


def test_plain_name_is_joined(folder):
    assert plain_file_in(folder, "audio.wav") == os.path.join(folder, "audio.wav")
    assert plain_file_in(folder, "not_there.wav") == os.path.join(folder, "not_there.wav")


@pytest.mark.parametrize("name", [
    None, "", ".", "..", 5, "../x.wav", "a/b.wav", "..\\x.wav", "sub\\x.wav",
    "/etc/passwd", "C:\\Windows\\x.wav", "C:x.wav", "\\\\server\\share\\x.wav",
    "//server/share/x.wav", "a\x00.wav",
    "con", "NUL.txt", "aux.wav", "com1", "LPT9.wav", "nul .wav",
])
def test_refuses_anything_but_a_plain_name(folder, name):
    assert plain_file_in(folder, name) is None


def test_refuses_absolute_path_to_a_real_file(folder, tmp_path):
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"s")
    assert plain_file_in(folder, str(outside)) is None


def test_refuses_symlink_pointing_out_of_the_folder(folder, tmp_path):
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"s")
    link = os.path.join(folder, "link.wav")
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert plain_file_in(folder, "link.wav") is None


def test_allows_symlink_that_stays_inside_the_folder(folder):
    link = os.path.join(folder, "alias.wav")
    try:
        os.symlink(os.path.join(folder, "audio.wav"), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert plain_file_in(folder, "alias.wav") == link
