package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class GrabQueueTest {
    @Test
    public void aSpentCapOrARefusedTokenStopsTheRun() {
        assertTrue(GrabQueue.refusesTheRest(401));
        assertTrue(GrabQueue.refusesTheRest(403));
        assertTrue(GrabQueue.refusesTheRest(429));
        assertFalse(GrabQueue.refusesTheRest(0));
        assertFalse(GrabQueue.refusesTheRest(409));
        assertFalse(GrabQueue.refusesTheRest(422));
        assertFalse(GrabQueue.refusesTheRest(503));
    }

    @Test
    public void nameFallsBackToTheHostAndStaysShort() {
        assertEquals("My Comic", GrabQueue.nameFor("  My Comic ", "https://a.example/x"));
        assertEquals("a.example", GrabQueue.nameFor("", "https://a.example/x"));
        assertEquals("Page", GrabQueue.nameFor(null, null));
        assertEquals(60, GrabQueue.nameFor("x".repeat(100), "https://a.example").length());
    }

    @Test
    public void aChapterAlreadyWaitingIsNotAddedTwice() {
        List<GrabQueue.Item> queue = GrabQueue.withItem(new ArrayList<>(), new GrabQueue.Item("https://a/1", 3, "A"));
        assertSame(queue, GrabQueue.withItem(queue, new GrabQueue.Item("https://a/1", 5, "A")));
        assertEquals(2, GrabQueue.withItem(queue, new GrabQueue.Item("https://a/2", 1, "B")).size());
    }

    @Test
    public void aFullQueueTakesNoMore() {
        List<GrabQueue.Item> queue = new ArrayList<>();
        for (int i = 0; i < GrabQueue.MAX_QUEUED; i++) queue = GrabQueue.withItem(queue, new GrabQueue.Item("https://a/" + i, 1, "A"));
        assertSame(queue, GrabQueue.withItem(queue, new GrabQueue.Item("https://a/new", 1, "A")));
    }

    @Test
    public void historyKeepsTheNewestFirstAndDropsTheOldest() {
        List<GrabQueue.Done> history = new ArrayList<>();
        for (int i = 0; i < GrabQueue.MAX_HISTORY + 5; i++) history = GrabQueue.withDone(history, new GrabQueue.Done(i, "n" + i, "m"));
        assertEquals(GrabQueue.MAX_HISTORY, history.size());
        assertEquals(GrabQueue.MAX_HISTORY + 4, history.get(0).at);
        assertEquals(5, history.get(GrabQueue.MAX_HISTORY - 1).at);
    }

    @Test
    public void listsSurviveSavingAndDamagedOnesComeBackEmpty() {
        List<GrabQueue.Item> queue = GrabQueue.parseQueue(GrabQueue.queueJson(
                GrabQueue.withItem(new ArrayList<>(), new GrabQueue.Item("https://a/1", 3, "A"))));
        assertEquals(1, queue.size());
        assertEquals(3, queue.get(0).chapters);
        List<GrabQueue.Done> history = GrabQueue.parseHistory(GrabQueue.historyJson(
                GrabQueue.withDone(new ArrayList<>(), new GrabQueue.Done(7, "A", "Sent 12 pages"))));
        assertEquals("Sent 12 pages", history.get(0).message);
        assertEquals(0, GrabQueue.parseQueue("{not json").size());
        assertEquals(0, GrabQueue.parseHistory(null).size());
    }

    @Test
    public void historyRemembersWhereARunStartedAndAnOlderEntryHasNoAddress() {
        List<GrabQueue.Done> history = GrabQueue.parseHistory(GrabQueue.historyJson(
                GrabQueue.withDone(new ArrayList<>(), new GrabQueue.Done(7, "A", "Opened 5 pages", "https://a/ch1", true))));
        assertEquals("https://a/ch1", history.get(0).url);
        assertTrue(history.get(0).quick);
        List<GrabQueue.Done> old = GrabQueue.parseHistory("[{\"at\":1,\"name\":\"B\",\"message\":\"m\"}]");
        assertEquals("", old.get(0).url);
        assertFalse(old.get(0).quick);
    }

    @Test
    public void removingAChapterTakesOnlyThatOne() {
        List<GrabQueue.Item> queue = GrabQueue.withItem(GrabQueue.withItem(new ArrayList<>(),
                new GrabQueue.Item("https://a/1", 1, "A")), new GrabQueue.Item("https://a/2", 1, "B"));
        List<GrabQueue.Item> left = GrabQueue.withoutUrl(queue, "https://a/1");
        assertEquals(1, left.size());
        assertEquals("https://a/2", left.get(0).url);
        assertEquals(2, GrabQueue.withoutUrl(queue, "https://a/none").size());
    }
}
