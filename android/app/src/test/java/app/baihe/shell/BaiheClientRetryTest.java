package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.HashSet;
import java.util.List;

import org.junit.Test;

public class BaiheClientRetryTest {
    @Test
    public void aProxyTimeoutOrADroppedConnectionIsAskedAgain() {
        assertTrue(BaiheClient.worthRetrying(new BaiheClient.BridgeException(502, "x")));
        assertTrue(BaiheClient.worthRetrying(new BaiheClient.BridgeException(503, "x")));
        assertTrue(BaiheClient.worthRetrying(new BaiheClient.BridgeException(504, "x")));
        assertTrue(BaiheClient.worthRetrying(new BaiheClient.BridgeException(0, "x", true)));
    }

    @Test
    public void refusalsAndCancelAreNotRetried() {
        for (int status : new int[] {400, 401, 403, 422, 429}) {
            assertFalse(BaiheClient.worthRetrying(new BaiheClient.BridgeException(status, "x")));
        }
        assertFalse(BaiheClient.worthRetrying(new BaiheClient.BridgeException(0, "Cancelled.")));
    }

    @Test
    public void aRetryAsksOnlyForPagesThatHaveNotArrived() {
        List<PageImage> batch = Arrays.asList(
                new PageImage("a", "https://example.com/a.jpg", "image/jpeg", ""),
                new PageImage("b", "https://example.com/b.jpg", "image/jpeg", ""),
                new PageImage("c", "https://example.com/c.jpg", "image/jpeg", ""));
        List<PageImage> rest = BaiheClient.notDelivered(batch, new HashSet<>(Arrays.asList("a", "c")));
        assertEquals(1, rest.size());
        assertEquals("b", rest.get(0).key);
    }
}
