package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;
import org.junit.Test;

public class GrabBookmarksTest {
    @Test
    public void aBookmarkSurvivesSavingAndLoading() {
        List<GrabBookmarks.Mark> marks = GrabBookmarks.with(new ArrayList<>(), "Night Owl\tch 3\n", "https://a.example/owl/3");
        List<GrabBookmarks.Mark> back = GrabBookmarks.parse(GrabBookmarks.serialize(marks));
        assertEquals(1, back.size());
        assertEquals("Night Owl ch 3", back.get(0).name);
        assertEquals("https://a.example/owl/3", back.get(0).url);
    }

    @Test
    public void markingAgainMovesItToTheTopWithoutDuplicating() {
        List<GrabBookmarks.Mark> marks = GrabBookmarks.with(new ArrayList<>(), "One", "https://a.example/1");
        marks = GrabBookmarks.with(marks, "Two", "https://a.example/2");
        marks = GrabBookmarks.with(marks, "One again", "https://a.example/1");
        assertEquals(2, marks.size());
        assertEquals("One again", marks.get(0).name);
        assertTrue(GrabBookmarks.has(marks, "https://a.example/2"));
    }

    @Test
    public void onlyWebAddressesAreKeptAndTheListIsCapped() {
        assertEquals(0, GrabBookmarks.with(new ArrayList<>(), "x", "javascript:alert(1)").size());
        assertEquals(0, GrabBookmarks.parse("bad\tfile:///etc/passwd\nno tab here\n").size());
        List<GrabBookmarks.Mark> marks = new ArrayList<>();
        for (int i = 0; i < GrabBookmarks.MAX + 5; i++) marks = GrabBookmarks.with(marks, "p" + i, "https://a.example/" + i);
        assertEquals(GrabBookmarks.MAX, marks.size());
        assertEquals("p" + (GrabBookmarks.MAX + 4), marks.get(0).name);
    }

    @Test
    public void removingAndTellingSitesFromChapters() {
        List<GrabBookmarks.Mark> marks = GrabBookmarks.with(new ArrayList<>(), "Site", "https://a.example/");
        assertTrue(GrabBookmarks.isSite("https://a.example"));
        assertTrue(GrabBookmarks.isSite("https://a.example/"));
        assertFalse(GrabBookmarks.isSite("https://a.example/owl/3"));
        assertEquals(0, GrabBookmarks.without(marks, "https://a.example/").size());
    }

    @Test
    public void aShorterListKeepsOnlyTheNewestPages() {
        List<GrabBookmarks.Mark> marks = new ArrayList<>();
        for (int i = 0; i < 5; i++) marks = GrabBookmarks.with(marks, "p" + i, "https://a.example/" + i, 3);
        assertEquals(3, marks.size());
        assertEquals("p4", marks.get(0).name);
        assertEquals("p2", marks.get(2).name);
    }
}
