package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class TitleProgressTest {
    @Test
    public void waitingAndRunningKeepPolling() {
        assertFalse(TitleProgress.settled("waiting"));
        assertFalse(TitleProgress.settled("running"));
        assertTrue(TitleProgress.settled("idle"));
        assertTrue(TitleProgress.settled("done"));
        assertTrue(TitleProgress.settled("stopped"));
    }

    @Test
    public void linesNameTheStage() {
        assertEquals("Waiting to translate Owl", TitleProgress.line("waiting", "follow", "Owl"));
        assertEquals("Preparing Owl for translating", TitleProgress.line("running", "lines", "Owl"));
        assertEquals("Translating Owl", TitleProgress.line("running", "run_scanlate", "Owl"));
        assertEquals("Translated Owl", TitleProgress.line("done", null, "Owl"));
        assertEquals("Translating Owl stopped. See the Jobs page.", TitleProgress.line("stopped", "run_translate", "Owl"));
    }

    @Test
    public void idleShowsNothing() {
        assertNull(TitleProgress.line("idle", null, "Owl"));
        assertNull(TitleProgress.line(null, null, "Owl"));
    }
}
