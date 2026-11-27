package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class LoadFailureTest {
    @Test
    public void onlyAFailedMainFrameCoversThePage() {
        assertTrue(LoadFailure.coversPage(true, -6));
        assertFalse(LoadFailure.coversPage(false, -6));
    }

    @Test
    public void aCancelledLoadIsNotAFailure() {
        assertFalse(LoadFailure.coversPage(true, -999));
    }

    @Test
    public void quietRetriesBackOffThenStop() {
        assertEquals(2000, LoadFailure.retryDelayMs(0));
        assertEquals(5000, LoadFailure.retryDelayMs(1));
        assertEquals(-1, LoadFailure.retryDelayMs(LoadFailure.AUTO_RETRIES));
        assertEquals(-1, LoadFailure.retryDelayMs(-1));
    }
}
