import os

import pytest

from lib.safe_paths import contained, download_name, plain_file_in


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


class TestContained:
    def test_inside_the_root_or_the_root_itself(self, tmp_path):
        (tmp_path / "a").mkdir()
        assert contained(str(tmp_path), str(tmp_path / "a" / "x.txt"))
        assert contained(str(tmp_path), str(tmp_path))

    def test_dotdot_and_sibling_prefix_escape(self, tmp_path):
        (tmp_path / "root").mkdir()
        (tmp_path / "root-evil").mkdir()
        assert not contained(str(tmp_path / "root"), str(tmp_path / "root" / ".." / "x"))
        assert not contained(str(tmp_path / "root"), str(tmp_path / "root-evil" / "x"))

    @pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="needs POSIX symlinks")
    def test_symlink_pointing_out_is_not_contained(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "link").symlink_to(outside)
        assert not contained(str(root), str(root / "link" / "x"))

    def test_windows_case_and_other_drive(self, monkeypatch):
        import ntpath
        import lib.safe_paths as sp
        monkeypatch.setattr(sp.os, "path", ntpath)
        # realpath of a path that does not exist keeps the text as given.
        assert sp.contained("C:\\Titles\\One", "c:\\TITLES\\one\\x.srt")
        assert not sp.contained("C:\\Titles\\One", "D:\\Titles\\One\\x.srt")


@pytest.mark.parametrize("name,expected", [
    ("backup 2026.zip", "backup_2026.zip"), (".env", ".env"), ("../x.db", ".._x.db"),
    ("名前.srt", "__.srt"), ("", "download"), ("...", "...")])
def test_download_name_is_ascii(name, expected):
    assert download_name(name) == expected


@pytest.mark.parametrize("name,expected", [
    ("backup 2026.zip", "backup_2026.zip"), (".env", "env"), ("../x.db", "_x.db"),
    ("名前.srt", "__.srt"), ("", "download"), ("...", "download")])
def test_backup_download_name_is_never_hidden(name, expected):
    from api.routers.library_admin_routes import _backup_name
    assert _backup_name(name) == expected
