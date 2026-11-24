package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class QuickReadSessionTest {
    private int fetched;

    private void open(int pages) {
        QuickReadSession.begin();
        for (int i = 0; i < pages; i++) QuickReadSession.add("page-" + i + ".jpg");
        QuickReadSession.finish("Read " + pages + " pages");
        QuickReadSession.offerNext(() -> fetched++);
    }

    @Test
    public void nextChapterIsFetchedOnceWhenTheLastPagesAreNear() {
        open(10);
        QuickReadSession.reached(5);
        assertEquals(0, fetched);
        QuickReadSession.reached(7);
        QuickReadSession.reached(9);
        assertEquals(1, fetched);
    }

    @Test
    public void nothingIsFetchedWhileTheChapterIsStillArriving() {
        QuickReadSession.begin();
        QuickReadSession.add("page-0.jpg");
        QuickReadSession.offerNext(() -> fetched++);
        QuickReadSession.reached(0);
        assertEquals(0, fetched);
    }

    @Test
    public void aShortNextChapterWaitsUntilTheReaderReachesIt() {
        open(10);
        QuickReadSession.reached(9);
        QuickReadSession.resume();
        assertFalse(QuickReadSession.isComplete());
        QuickReadSession.add("page-10.jpg");
        QuickReadSession.finish("Read 1 page");
        QuickReadSession.offerNext(() -> fetched++);
        // Page 9 is within three of the end, but it belongs to the chapter before.
        QuickReadSession.reached(9);
        assertEquals(1, fetched);
        QuickReadSession.reached(10);
        assertEquals(2, fetched);
    }

    @Test
    public void cancelOrClosingTheReaderDropsTheOffer() {
        open(4);
        QuickReadSession.cancel();
        QuickReadSession.reached(3);
        open(4);
        QuickReadSession.abandon();
        QuickReadSession.reached(3);
        QuickReadSession.resume();
        assertEquals(0, fetched);
        assertTrue(QuickReadSession.isComplete());
    }
}
