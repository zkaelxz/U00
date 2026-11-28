package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

public class MediaStateTest {
    @Test
    public void readsAPlayingReport() {
        MediaState s = MediaState.parse("{\"state\":\"playing\",\"position\":12.5,\"duration\":600,"
                + "\"rate\":1.25,\"title\":\" Moonlit Hall 3 \",\"artist\":\"Example Studio\",\"at\":1000}");
        assertEquals(MediaState.Status.PLAYING, s.status);
        assertEquals(12_500, s.positionMs);
        assertEquals(600_000, s.durationMs);
        assertEquals(1.25f, s.speed, 0f);
        assertEquals(1.25f, s.extrapolationSpeed(), 0f);
        assertEquals("Moonlit Hall 3", s.title);
        assertEquals("Example Studio", s.artist);
    }

    @Test
    public void aPausedPlayersPlaceStandsStill() {
        MediaState s = MediaState.parse("{\"state\":\"paused\",\"position\":3,\"rate\":2}");
        assertEquals(MediaState.Status.PAUSED, s.status);
        assertEquals(0f, s.extrapolationSpeed(), 0f);
        assertEquals(MediaState.Status.STOPPED, MediaState.parse("{\"state\":\"stopped\"}").status);
    }

    @Test
    public void refusesWhatIsNotAReport() {
        assertNull(MediaState.parse(null));
        assertNull(MediaState.parse("not json"));
        assertNull(MediaState.parse("[]"));
        assertNull(MediaState.parse("{\"position\":3}"));
        assertNull(MediaState.parse("{\"state\":\"rewinding\"}"));
        assertNull(MediaState.parse("{\"state\":1}"));
        StringBuilder huge = new StringBuilder("{\"state\":\"playing\",\"title\":\"");
        for (int i = 0; i < 5000; i++) huge.append('a');
        assertNull(MediaState.parse(huge.append("\"}").toString()));
    }

    @Test
    public void unknownOrOddNumbersFallBackToSafeValues() {
        // JSON.stringify turns NaN and Infinity (a duration not read yet, a stream) into null.
        MediaState s = MediaState.parse("{\"state\":\"playing\",\"position\":-4,\"duration\":null,"
                + "\"rate\":0,\"title\":7,\"artist\":null}");
        assertEquals(0, s.positionMs);
        assertEquals(-1, s.durationMs);
        assertEquals(1f, s.speed, 0f);
        assertEquals("", s.title);
        assertEquals("", s.artist);
        assertEquals(1f, MediaState.parse("{\"state\":\"playing\",\"rate\":99}").speed, 0f);
        assertEquals(1f, MediaState.parse("{\"state\":\"playing\",\"rate\":\"2\"}").speed, 0f);
    }

    @Test
    public void thePlaceNeverPassesTheEnd() {
        assertEquals(90_000, MediaState.parse("{\"state\":\"paused\",\"position\":95,\"duration\":90}").positionMs);
    }

    @Test
    public void longTextIsCutWithoutSplittingACharacter() {
        StringBuilder title = new StringBuilder();
        for (int i = 0; i < MediaState.MAX_TEXT - 1; i++) title.append('a');
        title.append("🎧tail");
        MediaState s = MediaState.parse("{\"state\":\"playing\",\"title\":\"" + title + "\"}");
        assertEquals(MediaState.MAX_TEXT - 1, s.title.length());
    }

    @Test
    public void theReportsAgeIsCappedSoAClockChangeCannotMoveThePlace() {
        MediaState s = MediaState.parse("{\"state\":\"playing\",\"at\":10000}");
        assertEquals(250, s.ageMs(10_250));
        assertEquals(0, s.ageMs(9_000));
        assertEquals(MediaState.MAX_AGE_MS, s.ageMs(99_000));
        assertEquals(0, MediaState.parse("{\"state\":\"playing\"}").ageMs(99_000));
    }

    @Test
    public void aRetriedReportMovesOnWithThePlayerButNeverPastTheEnd() {
        MediaState s = MediaState.parse("{\"state\":\"playing\",\"position\":10,\"duration\":60,\"rate\":2,\"at\":1000}");
        MediaState later = s.advancedTo(6_000);
        assertEquals(20_000, later.positionMs);
        assertEquals(0, later.ageMs(6_000));
        assertEquals(60_000, s.advancedTo(100_000).positionMs);
        MediaState paused = MediaState.parse("{\"state\":\"paused\",\"position\":10,\"at\":1000}");
        assertEquals(10_000, paused.advancedTo(6_000).positionMs);
        MediaState undated = MediaState.parse("{\"state\":\"playing\",\"position\":10}");
        assertEquals(10_000, undated.advancedTo(6_000).positionMs);
    }

    @Test
    public void onlyKnownPressesReachThePage() {
        assertEquals("window.__baiheMedia&&window.__baiheMedia('seek',42.5)", MediaState.pageScript(MediaState.SEEK, 42.5));
        assertEquals("window.__baiheMedia&&window.__baiheMedia('play',0.0)", MediaState.pageScript(MediaState.PLAY, 0));
        assertEquals("window.__baiheMedia&&window.__baiheMedia('back',0.0)", MediaState.pageScript(MediaState.BACK, Double.NaN));
        assertEquals("window.__baiheMedia&&window.__baiheMedia('seek',0.0)", MediaState.pageScript(MediaState.SEEK, -3));
        assertNull(MediaState.pageScript("alert(1)", 0));
        assertNull(MediaState.pageScript(null, 0));
    }
}
