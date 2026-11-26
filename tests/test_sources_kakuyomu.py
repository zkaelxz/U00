"""
tests/test_sources_kakuyomu.py -- the カクヨム (kakuyomu.jp) adapter, against small
invented fixtures shaped like the live pages seen 2026-11-26. No request ever
reaches the real site.
"""
import json

import pytest

from sources import registry
from sources.adapters import kakuyomu
from sources.models import AutomationPermission, ChapterInfo, FailureReason, FetchFailed, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = kakuyomu.BASE_URL
WORK = "1" * 19
WORK_B = "2" * 19
EP1, EP2, EP3 = "3" * 19, "4" * 19, "5" * 19

pytestmark = pytest.mark.usefixtures("isolated_db")


def _page(state: dict, body: str = "") -> str:
    data = {"props": {"pageProps": {"__APOLLO_STATE__": state}}}
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(data)}</script>{body}</body></html>")


def _work_state(work_id=WORK, title="試作の物語", status="RUNNING"):
    return {
        f"Work:{work_id}": {
            "__typename": "Work", "id": work_id, "title": title, "introduction": "あらすじ\n二行目",
            "serialStatus": status, "publicEpisodeCount": 3, "author": {"__ref": "UserAccount:9"},
            "tableOfContentsV2": [{"__ref": "TableOfContentsChapter:a"},
                                  {"__ref": "TableOfContentsChapter:b"}]},
        "UserAccount:9": {"__typename": "UserAccount", "id": "9", "name": "sample_author",
                          "activityName": "試作者"},
        "TableOfContentsChapter:a": {"chapter": {"__ref": "Chapter:a"},
                                     "episodeUnions": [{"__ref": f"Episode:{EP1}"},
                                                       {"__ref": f"Episode:{EP2}"}]},
        "TableOfContentsChapter:b": {"chapter": None,
                                     "episodeUnions": [{"__ref": f"Episode:{EP2}"},
                                                       {"__ref": f"Episode:{EP3}"},
                                                       {"__ref": "Episode:missing"}]},
        "Chapter:a": {"__typename": "Chapter", "id": "a", "level": 1, "title": "第一章"},
        f"Episode:{EP1}": {"__typename": "Episode", "id": EP1, "title": "第1話"},
        f"Episode:{EP2}": {"__typename": "Episode", "id": EP2, "title": "第2話"},
        f"Episode:{EP3}": {"__typename": "Episode", "id": EP3, "title": "第3話"},
    }


EPISODE = ('<html><body><div class="widget-episodeBody js-episode-body">'
           '<p id="p1">　最初の段落。</p><p id="p2" class="blank"><br /></p>'
           '<p id="p3"><ruby>妹<rp>(</rp><rt>わたし</rt><rp>)</rp></ruby>の話。<br />改行あり。</p>'
           '<p id="p4" class="blank"><br /></p></div></body></html>')


def _search_state():
    state = _work_state(WORK, "検索A")
    state.update(_work_state(WORK_B, "検索B", "COMPLETED"))
    state["UserAccount:9"] = {"__typename": "UserAccount", "id": "9", "name": "sample_author",
                              "activityName": "試作者"}
    return state


def _adapter(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("kakuyomu", t, clock, max_retries=0)
    return kakuyomu.KakuyomuSource(client=client), t


class TestUrls:
    @pytest.mark.parametrize("url", [f"{BASE}/works/{WORK}", f"{BASE}/works/{WORK}/episodes/{EP1}"])
    def test_work_and_episode_links_match(self, url):
        assert kakuyomu.KakuyomuSource.matches_url(url)
        assert registry.adapter_class_for_url(url) is kakuyomu.KakuyomuSource

    @pytest.mark.parametrize("url", [f"https://kakuyomu.jp.example/works/{WORK}",
                                     f"https://notkakuyomu.jp/works/{WORK}",
                                     f"{BASE}/search?q=x", f"{BASE}/works/12"])
    def test_other_links_do_not_match(self, url):
        assert not kakuyomu.KakuyomuSource.matches_url(url)

    def test_parse_url(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/works/{WORK}") == ("series", WORK)
        kind, ch = a.parse_url(f"{BASE}/works/{WORK}/episodes/{EP1}")
        assert kind == "chapter" and (ch.series_id, ch.chapter_id) == (WORK, EP1)


class TestSeries:
    def test_series_fields(self):
        a, t = _adapter({f"{BASE}/works/{WORK}": html(_page(_work_state()))})
        s = a.get_series(WORK)
        assert (s.title, s.authors, s.status, s.language) == ("試作の物語", ["試作者"], "ongoing", "ja")
        assert s.description == "あらすじ\n二行目"
        a.get_chapters(WORK)
        assert t.urls() == [f"{BASE}/works/{WORK}"], "series and chapter list share one request"

    def test_completed_status(self):
        a, _ = _adapter({f"{BASE}/works/{WORK}": html(_page(_work_state(status="COMPLETED")))})
        assert a.get_series(WORK).status == "completed"

    def test_bad_id_is_refused_before_any_request(self):
        a, t = _adapter({})
        with pytest.raises(SourceError):
            a.get_series("../etc")
        assert t.urls() == []

    def test_missing_page_data_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/works/{WORK}": html("<html><body>none</body></html>")})
        with pytest.raises(SourceError) as e:
            a.get_series(WORK)
        assert "layout" in str(e.value).lower()

    def test_removed_work_says_so(self):
        a, _ = _adapter({})
        with pytest.raises(FetchFailed) as e:
            a.get_series(WORK)
        assert "doesn't exist" in str(e.value)


class TestChapters:
    def test_parts_order_and_duplicates(self):
        a, _ = _adapter({f"{BASE}/works/{WORK}": html(_page(_work_state()))})
        chapters = a.get_chapters(WORK)
        assert [(c.chapter_id, c.title, c.group) for c in chapters] == [
            (EP1, "第1話", "第一章"), (EP2, "第2話", "第一章"), (EP3, "第3話", "")]
        assert chapters[0].url == f"{BASE}/works/{WORK}/episodes/{EP1}"


class TestText:
    def test_text_keeps_blank_lines_and_drops_ruby_readings(self):
        url = f"{BASE}/works/{WORK}/episodes/{EP1}"
        a, _ = _adapter({url: html(EPISODE)})
        text = a.get_chapter_text(ChapterInfo("kakuyomu", WORK, EP1, "第1話", url))
        assert text == "　最初の段落。\n\n妹の話。\n改行あり。"

    def test_page_without_body_is_a_layout_change(self):
        url = f"{BASE}/works/{WORK}/episodes/{EP1}"
        a, _ = _adapter({url: html("<html><body>x</body></html>")})
        with pytest.raises(SourceError):
            a.get_chapter_text(ChapterInfo("kakuyomu", WORK, EP1, "第1話", url))


class TestSearch:
    def test_results_follow_page_order_and_carry_metadata(self):
        body = f'<a href="/works/{WORK_B}">b</a><a href="/works/{WORK}">a</a><a href="/works/{WORK_B}">b</a>'
        a, t = _adapter({f"{BASE}/search?q=%E8%A9%A6": html(_page(_search_state(), body))})
        results = a.search("試")
        assert [r.series_id for r in results] == [WORK_B, WORK]
        assert results[0].extra["status"] == "completed" and results[0].extra["author"] == "試作者"
        assert results[1].url == f"{BASE}/works/{WORK}"

    def test_second_page_and_empty_query(self):
        a, t = _adapter({f"{BASE}/search?q=x&page=2": html(_page({}, ""))})
        assert a.search("x", page=2) == []
        assert a.search("  ") == []

    def test_links_without_matching_data_are_a_layout_change(self):
        body = f'<a href="/works/{WORK}">a</a>'
        a, _ = _adapter({f"{BASE}/search?q=x": html(_page({}, body))})
        with pytest.raises(SourceError):
            a.search("x")


def test_capabilities_record_unread_automation_terms():
    caps = kakuyomu.KakuyomuSource.__new__(kakuyomu.KakuyomuSource).capabilities()
    assert caps.automation_permission == AutomationPermission.UNKNOWN.value
