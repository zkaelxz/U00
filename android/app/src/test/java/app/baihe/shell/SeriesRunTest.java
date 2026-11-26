package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import org.junit.Test;

public class SeriesRunTest {
    private static final String PAGE = "https://www.site.test/series/owl";

    private static String links(String... pairs) {
        StringBuilder out = new StringBuilder("[");
        for (int i = 0; i < pairs.length; i += 2) {
            out.append(i == 0 ? "" : ",").append("{\"title\":\"").append(pairs[i]).append("\",\"url\":\"")
                    .append(pairs[i + 1]).append("\"}");
        }
        return out.append("]").toString();
    }

    private static List<GrabQueue.Item> listed() {
        return SeriesRun.parseLinks(links("Chapter 3", "https://site.test/c/3", "Chapter 2", "https://site.test/c/2",
                "Chapter 1", "https://site.test/c/1", "Chapter 9", "https://other.test/c/9"), PAGE);
    }

    @Test
    public void linksToAnotherSiteAreDropped() {
        assertEquals(3, listed().size());
    }

    @Test
    public void newestFirstListsAreReadOldestFirst() {
        SeriesRun.Plan plan = SeriesRun.plan(listed(), new ArrayList<>(), new ArrayList<>());
        assertEquals("https://site.test/c/1", plan.fresh.get(0).url);
        assertEquals("https://site.test/c/3", plan.fresh.get(2).url);
    }

    @Test
    public void doneAndWaitingChaptersAreSkippedButFailedOnesAreNot() {
        List<GrabQueue.Done> history = Arrays.asList(
                new GrabQueue.Done(1, "c1", "Sent 12 pages to Owl", "https://site.test/c/1/?x=1", false),
                new GrabQueue.Done(2, "c2", "Cancelled. Sent 0 pages", "https://site.test/c/2", false));
        List<GrabQueue.Item> queue = Arrays.asList(new GrabQueue.Item("https://site.test/c/3", 1, "c3"));
        SeriesRun.Plan plan = SeriesRun.plan(listed(), history, queue);
        assertEquals(1, plan.done);
        assertEquals(1, plan.waiting);
        assertEquals(1, plan.fresh.size());
        assertEquals("https://site.test/c/2", plan.fresh.get(0).url);
    }

    @Test
    public void theWarningStatesCountTimeAndCost() {
        SeriesRun.Plan plan = SeriesRun.plan(listed(), new ArrayList<>(), new ArrayList<>());
        String text = SeriesRun.warning(plan, 2);
        assertTrue(text.contains("adds 2 chapters"));
        assertTrue(text.contains("monthly cap"));
        assertTrue(text.contains("4 to 10 minutes"));
    }
}
