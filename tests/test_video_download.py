"""
tests/test_video_download.py -- video_download.py's yt-dlp wrapper,
exercised against a fake yt_dlp module (the real thing needs network
access this sandbox doesn't have).
"""
import os
import sys
import types
import importlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _install_fake_yt_dlp(extract_info_fn=None, raise_exc=None):
    calls = {"opts": None, "url": None}

    class FakeYoutubeDL:
        def __init__(self, opts):
            calls["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            calls["url"] = url
            if raise_exc:
                raise raise_exc
            return extract_info_fn(url) if extract_info_fn else {"id": "abc", "ext": "m4a"}

        def prepare_filename(self, info):
            return calls["opts"]["outtmpl"].replace("%(ext)s", info["ext"])

    fake_module = types.ModuleType("yt_dlp")
    fake_module.YoutubeDL = FakeYoutubeDL
    sys.modules["yt_dlp"] = fake_module
    return calls


def _teardown():
    sys.modules.pop("yt_dlp", None)


class TestImportErrorWhenNotInstalled:
    def test_raises_import_error_with_install_hint(self, monkeypatch):
        sys.modules.pop("yt_dlp", None)
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "yt_dlp":
                raise ImportError("No module named 'yt_dlp'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        import video_download
        with pytest.raises(ImportError, match="pip install yt-dlp"):
            video_download.download("https://example.com/video", "/tmp/out")


class TestAudioOnlyDownload:
    def teardown_method(self):
        _teardown()

    def test_returns_wav_path_and_creates_out_dir(self, tmp_path):
        out_dir = str(tmp_path / "drama_1")
        calls = _install_fake_yt_dlp(extract_info_fn=lambda url: {"id": "abc", "ext": "m4a"})

        import video_download
        importlib.reload(video_download)

        # Simulate the actual file yt-dlp+ffmpeg would have produced on disk.
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, "downloaded_audio.wav"), "wb").close()

        path = video_download.download("https://youtu.be/xyz", out_dir, audio_only=True)
        assert path == os.path.join(out_dir, "downloaded_audio.wav")
        assert calls["url"] == "https://youtu.be/xyz"
        assert calls["opts"]["format"] == "bestaudio/best"
        assert calls["opts"]["postprocessors"][0]["key"] == "FFmpegExtractAudio"
        assert os.path.isdir(out_dir)

    def test_js_runtimes_is_a_dict_not_a_list(self, tmp_path):
        """Regression test: yt-dlp's real, current js_runtimes option is a
        dict of {runtime: {config}}, not a flat list -- a list raises
        "Invalid js_runtimes format" and breaks every download."""
        out_dir = str(tmp_path)
        calls = _install_fake_yt_dlp(extract_info_fn=lambda url: {"id": "abc", "ext": "m4a"})
        open(os.path.join(out_dir, "downloaded_audio.wav"), "wb").close()

        import video_download
        video_download.download("https://youtu.be/xyz", out_dir, audio_only=True)

        assert calls["opts"]["js_runtimes"] == {"deno": {}, "node": {}, "bun": {}, "quickjs": {}}

    def test_progress_callback_receives_fraction_and_message(self, tmp_path):
        out_dir = str(tmp_path)
        calls = _install_fake_yt_dlp(extract_info_fn=lambda url: {"id": "abc", "ext": "m4a"})
        open(os.path.join(out_dir, "downloaded_audio.wav"), "wb").close()

        import video_download
        importlib.reload(video_download)

        progress_events = []
        video_download.download("https://youtu.be/xyz", out_dir, audio_only=True,
                                 progress_cb=lambda frac, msg: progress_events.append((frac, msg)))
        # The hook itself isn't invoked by our fake YoutubeDL (no real download
        # loop), but the final progress_cb(1.0, "Done.") call must still fire.
        assert (1.0, "Done.") in progress_events

    def test_title_cb_receives_the_fetched_title(self, tmp_path):
        out_dir = str(tmp_path)
        _install_fake_yt_dlp(extract_info_fn=lambda url: {
            "id": "abc", "ext": "m4a", "title": "沈清疑的百合广播剧"})
        open(os.path.join(out_dir, "downloaded_audio.wav"), "wb").close()

        import video_download
        importlib.reload(video_download)

        titles = []
        video_download.download("https://youtu.be/xyz", out_dir, audio_only=True,
                                 title_cb=titles.append)
        assert titles == ["沈清疑的百合广播剧"]

    def test_title_cb_not_called_when_yt_dlp_has_no_title(self, tmp_path):
        out_dir = str(tmp_path)
        _install_fake_yt_dlp(extract_info_fn=lambda url: {"id": "abc", "ext": "m4a"})
        open(os.path.join(out_dir, "downloaded_audio.wav"), "wb").close()

        import video_download
        importlib.reload(video_download)

        titles = []
        video_download.download("https://youtu.be/xyz", out_dir, audio_only=True,
                                 title_cb=titles.append)
        assert titles == []

    def test_missing_output_file_raises_download_error(self, tmp_path):
        out_dir = str(tmp_path)
        _install_fake_yt_dlp(extract_info_fn=lambda url: {"id": "abc", "ext": "m4a"})
        # Deliberately don't create the expected .wav file.

        import video_download
        importlib.reload(video_download)

        with pytest.raises(video_download.DownloadError, match="expected output file is missing"):
            video_download.download("https://youtu.be/xyz", out_dir, audio_only=True)

    def test_extraction_failure_wraps_original_exception(self, tmp_path):
        _install_fake_yt_dlp(raise_exc=RuntimeError("Video unavailable"))

        import video_download
        importlib.reload(video_download)

        with pytest.raises(video_download.DownloadError, match="Video unavailable"):
            video_download.download("https://youtu.be/xyz", str(tmp_path), audio_only=True)


class TestVideoDownload:
    def teardown_method(self):
        _teardown()

    def test_returns_video_path_with_source_extension_unchanged(self, tmp_path):
        out_dir = str(tmp_path)
        calls = _install_fake_yt_dlp(extract_info_fn=lambda url: {"id": "abc", "ext": "mp4"})
        open(os.path.join(out_dir, "downloaded_video.mp4"), "wb").close()

        import video_download
        importlib.reload(video_download)

        path = video_download.download("https://youtu.be/xyz", out_dir, audio_only=False)
        assert path == os.path.join(out_dir, "downloaded_video.mp4")
        assert calls["opts"]["format"] == "bestvideo+bestaudio/best"
        assert "postprocessors" not in calls["opts"]
        assert calls["opts"]["js_runtimes"] == {"deno": {}, "node": {}, "bun": {}, "quickjs": {}}

    @pytest.mark.parametrize("audio_only", [True, False])
    def test_asks_yt_dlp_for_uncoloured_output(self, tmp_path, audio_only):
        """yt-dlp error text ("ERROR: [youtube] ...") must not carry ANSI
        colour codes into the job error, the log or the support report."""
        out_dir = str(tmp_path)
        ext = "m4a" if audio_only else "mp4"
        calls = _install_fake_yt_dlp(extract_info_fn=lambda url: {"id": "abc", "ext": ext})
        name = "downloaded_audio.wav" if audio_only else "downloaded_video.mp4"
        open(os.path.join(out_dir, name), "wb").close()

        import video_download
        importlib.reload(video_download)

        video_download.download("https://youtu.be/xyz", out_dir, audio_only=audio_only)
        assert calls["opts"]["no_color"] is True


class TestCookieOptions:
    def test_no_cookies_gives_empty_dict(self):
        import video_download
        assert video_download.cookie_options() == {}
        assert video_download.cookie_options(None, None) == {}

    def test_browser_alone(self):
        import video_download
        assert video_download.cookie_options(browser="chrome") == {"cookiesfrombrowser": ("chrome",)}

    def test_file_alone(self):
        import video_download
        assert video_download.cookie_options(cookies_file="/tmp/cookies.txt") == {
            "cookiefile": "/tmp/cookies.txt"}

    def test_file_wins_over_browser_if_both_given(self):
        import video_download
        opts = video_download.cookie_options(browser="chrome", cookies_file="/tmp/cookies.txt")
        assert opts == {"cookiefile": "/tmp/cookies.txt"}


class TestDownloadCookiesReachYtDlp:
    def _stub_output(self, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, "downloaded_audio.wav"), "wb").close()

    def test_cookies_browser_reaches_ydl_opts(self, tmp_path):
        out_dir = str(tmp_path)
        calls = _install_fake_yt_dlp()
        self._stub_output(out_dir)
        try:
            import video_download
            importlib.reload(video_download)
            video_download.download("https://example.com/v", out_dir, cookies_browser="firefox")
            assert calls["opts"]["cookiesfrombrowser"] == ("firefox",)
        finally:
            _teardown()

    def test_cookies_file_reaches_ydl_opts(self, tmp_path):
        out_dir = str(tmp_path)
        calls = _install_fake_yt_dlp()
        self._stub_output(out_dir)
        try:
            import video_download
            importlib.reload(video_download)
            video_download.download("https://example.com/v", out_dir, cookies_file="/tmp/mycookies.txt")
            assert calls["opts"]["cookiefile"] == "/tmp/mycookies.txt"
        finally:
            _teardown()

    def test_no_cookies_means_no_cookie_keys(self, tmp_path):
        out_dir = str(tmp_path)
        calls = _install_fake_yt_dlp()
        self._stub_output(out_dir)
        try:
            import video_download
            importlib.reload(video_download)
            video_download.download("https://example.com/v", out_dir)
            assert "cookiesfrombrowser" not in calls["opts"]
            assert "cookiefile" not in calls["opts"]
        finally:
            _teardown()

    def test_error_message_hints_at_cookies_when_none_are_set(self, tmp_path):
        calls = _install_fake_yt_dlp(raise_exc=RuntimeError("HTTP Error 403: Forbidden"))
        try:
            import video_download
            importlib.reload(video_download)
            with pytest.raises(video_download.DownloadError, match="cookie-based login"):
                video_download.download("https://example.com/v", str(tmp_path))
        finally:
            _teardown()

    def test_error_message_omits_the_hint_when_cookies_are_already_set(self, tmp_path):
        calls = _install_fake_yt_dlp(raise_exc=RuntimeError("HTTP Error 403: Forbidden"))
        try:
            import video_download
            importlib.reload(video_download)
            with pytest.raises(video_download.DownloadError) as exc_info:
                video_download.download("https://example.com/v", str(tmp_path), cookies_browser="chrome")
            assert "cookie-based login" not in str(exc_info.value)
        finally:
            _teardown()
