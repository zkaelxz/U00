package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.List;
import org.json.JSONObject;
import org.junit.Test;

public class AlertFeedTest {
    private static final String LIST = "{\"items\":["
            + "{\"id\":30,\"at\":300.0,\"kind\":\"live\",\"text\":\"Chime is live\",\"target\":{\"page\":\"live\",\"follow_id\":4}},"
            + "{\"id\":20,\"at\":200.0,\"kind\":\"job_done\",\"text\":\"Finished: Translation - Moonlit\",\"target\":{\"page\":\"watch\",\"drama_id\":7}},"
            + "{\"id\":15,\"at\":150.0,\"kind\":\"resource\",\"text\":\"GPU hot\"},"
            + "{\"id\":10,\"at\":100.0,\"kind\":\"job_failed\",\"text\":\"Failed: Export\"}]}";

    @Test
    public void routesAreBuiltFromTheTargetOnly() {
        List<AlertFeed.Item> items = AlertFeed.parse(LIST);
        assertEquals("#/live?follow=4", items.get(0).route);
        assertEquals("#/watch/7", items.get(1).route);
        assertNull(items.get(3).route);
    }

    @Test
    public void aLiveEntryWithoutATargetStillOpensLive() {
        assertEquals("#/live", AlertFeed.route("live", null));
        assertNull(AlertFeed.route("job_done", null));
    }

    @Test
    public void unknownPagesAndBadIdsOpenNothing() throws Exception {
        assertNull(AlertFeed.route("waiting", new JSONObject("{\"page\":\"admin\"}")));
        assertNull(AlertFeed.route("waiting", new JSONObject("{\"page\":\"watch\",\"drama_id\":0}")));
        assertEquals("#/drama/3/translate?tab=glossary",
                AlertFeed.route("waiting", new JSONObject("{\"page\":\"glossary\",\"drama_id\":3}")));
    }

    @Test
    public void onlyAHashRouteInsideBaiheIsSafe() {
        assertTrue(AlertFeed.safeRoute("#/watch/7"));
        assertFalse(AlertFeed.safeRoute("https://evil.example/"));
        assertFalse(AlertFeed.safeRoute("javascript:alert(1)"));
        assertFalse(AlertFeed.safeRoute(null));
    }

    @Test
    public void freshIsWhatIsNewerThanTheMarkOldestFirstAndOnlyActionableKinds() {
        List<AlertFeed.Item> items = AlertFeed.parse(LIST);
        List<AlertFeed.Item> fresh = AlertFeed.fresh(items, 10, 100.0);
        assertEquals(2, fresh.size());
        assertEquals(20, fresh.get(0).id);
        assertEquals(30, fresh.get(1).id);
        assertTrue(AlertFeed.fresh(items, 30, 300.0).isEmpty());
    }

    @Test
    public void aRestartedServerIsStillNewByTime() {
        List<AlertFeed.Item> items = AlertFeed.parse("{\"items\":[{\"id\":1,\"at\":900.0,\"kind\":\"job_done\",\"text\":\"Finished\"}]}");
        assertEquals(1, AlertFeed.fresh(items, 5000, 800.0).size());
    }

    @Test
    public void anUnreadableAnswerIsAnEmptyList() {
        assertTrue(AlertFeed.parse("<html>").isEmpty());
        assertTrue(AlertFeed.parse("{\"items\":\"x\"}").isEmpty());
    }
}
