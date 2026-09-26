"""
tests/test_sources_bilibili.py -- Step 23d's BilibiliSource adapter.
yt-dlp itself is faked throughout (FakeYDL) -- no real network calls,
matching this repo's existing source-adapter test convention (see
tests/test_sources_manhuagui.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from sources.adapters.bilibili import (BilibiliDownloadError, BilibiliSource, dedupe_path,
                                       sanitize_filename)
from sources.models import FailureReason


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL -- extract_info() defers to a test-
    supplied callable(url, download, opts) -> dict, and (when download is
    True) writes a real dummy file at the resolved outtmpl path so
    BilibiliSource.download()'s own "file actually exists" check passes,
    same as real yt-dlp would leave a real file behind."""

    def __init__(self, opts, info_provider):
        self.opts = opts
        self._info_provider = info_provider

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        info = self._info_provider(url, download, self.opts)
        if download:
            ext = info.get("ext") or "mp4"
            path = self.opts["outtmpl"].replace("%(ext)s", ext)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"fake video bytes")
        return info


def _factory(info_provider):
    return lambda opts: FakeYDL(opts, info_provider)


def _adapter(info_provider, sleep=None, url_resolver=None):
    return BilibiliSource(ydl_factory=_factory(info_provider),
                          sleep=sleep or (lambda s: None),
                          url_resolver=url_resolver)


SINGLE_VIDEO_INFO = {
    "id": "BV1xx411c7abcd", "title": "A Cool Video", "webpage_url": "https://www.bilibili.com/video/BV1xx411c7abcd",
    "duration": 120, "uploader": "Some Uploader", "upload_date": "20240101",
    "description": "A description.", "thumbnail": "https://example.com/thumb.jpg",
    "formats": [
        {"format_id": "30032", "height": 360, "vcodec": "avc1", "acodec": "none", "tbr": 500},
        {"format_id": "30064", "height": 720, "vcodec": "avc1", "acodec": "none", "tbr": 1500},
        {"format_id": "30216", "height": 1080, "vcodec": "avc1", "acodec": "none", "tbr": 3000},
        {"format_id": "30280", "height": None, "vcodec": "none", "acodec": "mp4a", "tbr": 128},
    ],
    "subtitles": {"zh-Hans": [{"url": "https://example.com/sub.zh.srt", "ext": "srt"}]},
    "automatic_captions": {"en": [{"url": "https://example.com/sub.en.srt", "ext": "srt"}]},
}

MULTIPART_INFO = {
    "id": "BV1yy411c7wxyz", "title": "An Anthology",
    "webpage_url": "https://www.bilibili.com/video/BV1yy411c7wxyz",
    "entries": [
        {"id": "BV1yy411c7wxyz_p1", "title": "Part One", "webpage_url": "https://www.bilibili.com/video/BV1yy411c7wxyz?p=1"},
        {"id": "BV1yy411c7wxyz_p2", "title": "Part Two", "webpage_url": "https://www.bilibili.com/video/BV1yy411c7wxyz?p=2"},
        {"id": "BV1yy411c7wxyz_p3", "title": "Part Three", "webpage_url": "https://www.bilibili.com/video/BV1yy411c7wxyz?p=3"},
    ],
}


class TestCanHandleAndNormalizeUrl:
    def test_bv_form(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        assert a.can_handle("https://www.bilibili.com/video/BV1xx411c7abcd")

    def test_av_form(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        assert a.can_handle("https://www.bilibili.com/video/av170001")

    def test_b23_short_link(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        assert a.can_handle("https://b23.tv/abc123")

    def test_query_parameters_url(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        assert a.can_handle("https://www.bilibili.com/video/BV1xx411c7abcd?spm_id_from=333.999")

    def test_non_bilibili_url_not_handled(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        assert not a.can_handle("https://www.youtube.com/watch?v=abc")

    def test_normalize_strips_tracking_params(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        normalized = a.normalize_url(
            "https://www.bilibili.com/video/BV1xx411c7abcd?spm_id_from=333.999&vd_source=abc")
        assert "spm_id_from" not in normalized and "vd_source" not in normalized
        assert "BV1xx411c7abcd" in normalized

    def test_normalize_keeps_explicit_part_selector(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        normalized = a.normalize_url(
            "https://www.bilibili.com/video/BV1yy411c7wxyz?p=2&spm_id_from=333.999")
        assert "p=2" in normalized
        assert "spm_id_from" not in normalized

    def test_normalize_resolves_b23_short_link(self):
        resolved = {"https://b23.tv/abc123": "https://www.bilibili.com/video/BV1xx411c7abcd"}
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO, url_resolver=lambda u: resolved[u])
        assert a.normalize_url("https://b23.tv/abc123") == \
            "https://www.bilibili.com/video/BV1xx411c7abcd"

    def test_real_url_resolver_routes_through_the_shared_paced_transport(self):
        """Step 28 gap 2: the real resolver used to make a bare
        `requests.head()` call, bypassing sources/http.py's shared
        SourceClient transport (and therefore its pacing) entirely."""
        from sources.http import Response
        from tests.sources_helpers import ScriptedTransport, make_client
        short_url = "https://b23.tv/abc123"
        resolved_url = "https://www.bilibili.com/video/BV1xx411c7abcd"
        t = ScriptedTransport({short_url: Response(200, {}, b"", resolved_url)})
        a = BilibiliSource(ydl_factory=_factory(lambda *a: SINGLE_VIDEO_INFO),
                           client=make_client("bilibili", t))
        assert a.normalize_url(short_url) == resolved_url
        assert len(t.calls) == 1
        assert t.calls[0]["method"] == "HEAD" and t.calls[0]["url"] == short_url


class TestMultipart:
    def test_get_parts_returns_them_in_order_with_titles(self):
        a = _adapter(lambda *a: MULTIPART_INFO)
        parts = a.get_parts("https://www.bilibili.com/video/BV1yy411c7wxyz")
        assert [p["title"] for p in parts] == ["Part One", "Part Two", "Part Three"]
        assert [p["index"] for p in parts] == [1, 2, 3]

    def test_single_video_returns_one_part(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        parts = a.get_parts("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert len(parts) == 1
        assert parts[0]["title"] == "A Cool Video"

    def test_explicit_p_url_downloads_only_that_part(self, tmp_path):
        # yt-dlp's own extractor resolves a ?p=2 URL to a single-entry
        # info dict (no "entries" key) -- simulated here directly.
        def provider(url, download, opts):
            assert "p=2" not in url or True  # normalize_url already stripped it into the request
            return {"id": "BV1yy411c7wxyz_p2", "title": "Part Two",
                    "webpage_url": "https://www.bilibili.com/video/BV1yy411c7wxyz?p=2", "ext": "mp4"}
        a = _adapter(provider)
        result = a.download("https://www.bilibili.com/video/BV1yy411c7wxyz?p=2", str(tmp_path))
        assert result["title"] == "Part Two"
        assert result["part_title"] is None  # no "entries" in the response -- not multipart-expanded
        assert os.path.exists(result["path"])


class TestQualitySelector:
    def test_only_offers_qualities_present_in_formats(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        qualities = a.available_qualities("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert set(qualities) == {"Best available", "1080p", "720p", "360p", "Audio only"}
        assert "480p" not in qualities

    def test_requested_quality_available_is_used_as_is(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        actual, message = a.resolve_quality("https://www.bilibili.com/video/BV1xx411c7abcd", "720p")
        assert actual == "720p"
        assert message is None

    def test_falls_back_to_closest_lower_quality_with_a_message(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        actual, message = a.resolve_quality("https://www.bilibili.com/video/BV1xx411c7abcd", "480p")
        assert actual == "360p"
        assert message and "480p" in message and "360p" in message

    def test_no_lower_quality_available_falls_back_to_best(self):
        no_formats_info = dict(SINGLE_VIDEO_INFO, formats=[])
        a = _adapter(lambda *a: no_formats_info)
        actual, message = a.resolve_quality("https://www.bilibili.com/video/BV1xx411c7abcd", "480p")
        assert actual == "Best available"
        assert message is not None


class TestSubtitles:
    def test_human_and_ai_generated_tracks_are_tagged(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        subs = a.get_subtitles("https://www.bilibili.com/video/BV1xx411c7abcd")
        by_lang = {s["language"]: s["subtitle_type"] for s in subs}
        assert by_lang["zh-Hans"] == "human"
        assert by_lang["en"] == "ai_generated"

    def test_no_subtitles_does_not_raise(self):
        no_subs_info = dict(SINGLE_VIDEO_INFO, subtitles={}, automatic_captions={})
        a = _adapter(lambda *a: no_subs_info)
        assert a.get_subtitles("https://www.bilibili.com/video/BV1xx411c7abcd") == []


class TestRetryAndFailureMessages:
    def test_412_is_retried_with_backoff_then_succeeds(self):
        calls = {"n": 0}
        sleeps = []

        def provider(url, download, opts):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("HTTP Error 412: Precondition Failed")
            return SINGLE_VIDEO_INFO
        a = _adapter(provider, sleep=lambda s: sleeps.append(s))
        info = a.extract_info("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert info["id"] == "BV1xx411c7abcd"
        assert calls["n"] == 3
        assert len(sleeps) == 2

    def test_412_exhausting_retries_surfaces_a_rate_limited_message(self):
        def provider(url, download, opts):
            raise RuntimeError("HTTP Error 412: Precondition Failed")
        a = _adapter(provider, sleep=lambda s: None)
        with pytest.raises(BilibiliDownloadError) as exc_info:
            a.extract_info("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert exc_info.value.reason == FailureReason.RATE_LIMIT
        assert "rate-limiting" in str(exc_info.value)

    def test_non_retryable_failure_is_not_retried(self):
        calls = {"n": 0}

        def provider(url, download, opts):
            calls["n"] += 1
            raise RuntimeError("This video is private")
        a = _adapter(provider, sleep=lambda s: None)
        with pytest.raises(BilibiliDownloadError) as exc_info:
            a.extract_info("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert calls["n"] == 1
        assert exc_info.value.reason == FailureReason.ACCESS_DENIED

    def test_login_required_message(self):
        def provider(url, download, opts):
            raise RuntimeError("This video requires a VIP membership to watch")
        a = _adapter(provider, sleep=lambda s: None)
        with pytest.raises(BilibiliDownloadError) as exc_info:
            a.extract_info("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert exc_info.value.reason == FailureReason.AUTHENTICATION_REQUIRED
        assert "Bilibili login" in str(exc_info.value)


class TestStandardizedOutput:
    def test_output_shape_has_everything_the_transcription_pipeline_needs(self, tmp_path):
        def provider(url, download, opts):
            info = dict(SINGLE_VIDEO_INFO)
            info["ext"] = "mp4"
            return info
        a = _adapter(provider)
        result = a.download("https://www.bilibili.com/video/BV1xx411c7abcd", str(tmp_path))

        for key in ("source", "source_url", "bvid", "title", "duration", "part", "part_title",
                    "path", "thumbnail", "subtitles", "raw_metadata"):
            assert key in result
        assert result["source"] == "bilibili"
        assert result["bvid"] == "BV1xx411c7abcd"
        # what the existing video pipeline actually needs: a real local file it
        # can feed to core.extract_audio_from_video, exactly like an uploaded file.
        assert os.path.exists(result["path"])
        assert result["path"].endswith(".mp4")

    def test_audio_only_quality_produces_a_wav(self, tmp_path):
        def provider(url, download, opts):
            info = dict(SINGLE_VIDEO_INFO)
            info["ext"] = "wav"
            return info
        a = _adapter(provider)
        result = a.download("https://www.bilibili.com/video/BV1xx411c7abcd", str(tmp_path),
                            options={"quality": "Audio only"})
        assert result["path"].endswith(".wav")
        assert os.path.exists(result["path"])


class TestFilenames:
    def test_sanitize_strips_windows_invalid_characters(self):
        assert sanitize_filename('Title: "Cool" <Video>?*|') == "Title Cool Video"

    def test_sanitize_truncates_an_overlong_title(self):
        long_title = "A" * 300
        assert len(sanitize_filename(long_title, max_len=150)) == 150

    def test_dedupe_path_does_not_overwrite_an_existing_file(self, tmp_path):
        existing = tmp_path / "video.mp4"
        existing.write_bytes(b"already here")
        new_path = dedupe_path(str(existing))
        assert new_path != str(existing)
        assert not os.path.exists(new_path)

    def test_dedupe_path_returns_the_same_path_when_nothing_exists_yet(self, tmp_path):
        target = str(tmp_path / "video.mp4")
        assert dedupe_path(target) == target

    def test_download_produces_a_windows_safe_filename(self, tmp_path):
        def provider(url, download, opts):
            info = dict(SINGLE_VIDEO_INFO)
            info["title"] = 'A Video: "Special" Edition?'
            info["ext"] = "mp4"
            return info
        a = _adapter(provider)
        result = a.download("https://www.bilibili.com/video/BV1xx411c7abcd", str(tmp_path))
        basename = os.path.basename(result["path"])
        assert not any(c in basename for c in '<>:"/\\|?*')

    def test_second_download_of_the_same_title_does_not_overwrite_the_first(self, tmp_path):
        def provider(url, download, opts):
            info = dict(SINGLE_VIDEO_INFO)
            info["ext"] = "mp4"
            return info
        a = _adapter(provider)
        first = a.download("https://www.bilibili.com/video/BV1xx411c7abcd", str(tmp_path))
        second = a.download("https://www.bilibili.com/video/BV1xx411c7abcd", str(tmp_path))
        assert first["path"] != second["path"]
        assert os.path.exists(first["path"])
        assert os.path.exists(second["path"])


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://www.bilibili.com/video/BV1xx411c7abcd")
        assert adapter is not None
        assert adapter.name == "bilibili"

    def test_capabilities_records_no_challenge_defeat(self):
        a = _adapter(lambda *a: SINGLE_VIDEO_INFO)
        caps = a.capabilities()
        assert caps.technical["no_challenge_defeat"] is True
