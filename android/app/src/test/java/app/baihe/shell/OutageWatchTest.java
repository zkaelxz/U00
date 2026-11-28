package app.baihe.shell;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class OutageWatchTest {
    private static final long MIN = 60_000L;

    private static OutageWatch.State goodOn(String network) {
        OutageWatch.State state = new OutageWatch.State();
        OutageWatch.next(state, true, network, 0);
        return state;
    }

    @Test
    public void announcesOnceAfterAnHourOnTheSameNetwork() {
        OutageWatch.State state = goodOn("home");
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, false, "home", 10 * MIN));
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, false, "home", 69 * MIN));
        assertEquals(OutageWatch.Action.DOWN, OutageWatch.next(state, false, "home", 70 * MIN));
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, false, "home", 300 * MIN));
    }

    @Test
    public void saysWhenItAnswersAgainAndOnlyThen() {
        OutageWatch.State state = goodOn("home");
        OutageWatch.next(state, false, "home", MIN);
        OutageWatch.next(state, false, "home", 62 * MIN);
        assertEquals(OutageWatch.Action.BACK, OutageWatch.next(state, true, "home", 90 * MIN));
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, true, "home", 100 * MIN));
    }

    @Test
    public void aShortOutageAnnouncesNothing() {
        OutageWatch.State state = goodOn("home");
        OutageWatch.next(state, false, "home", MIN);
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, true, "home", 30 * MIN));
    }

    @Test
    public void anotherNetworkOrNoNetworkRestartsTheCount() {
        OutageWatch.State state = goodOn("home");
        OutageWatch.next(state, false, "home", MIN);
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, false, "mobile", 100 * MIN));
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, false, null, 200 * MIN));
        assertEquals(OutageWatch.Action.NONE, OutageWatch.next(state, false, "home", 300 * MIN));
    }

    @Test
    public void aNewOutageAnnouncesAgainAfterRecovery() {
        OutageWatch.State state = goodOn("home");
        OutageWatch.next(state, false, "home", MIN);
        OutageWatch.next(state, false, "home", 70 * MIN);
        OutageWatch.next(state, true, "home", 80 * MIN);
        OutageWatch.next(state, false, "home", 90 * MIN);
        assertEquals(OutageWatch.Action.DOWN, OutageWatch.next(state, false, "home", 150 * MIN));
    }
}
