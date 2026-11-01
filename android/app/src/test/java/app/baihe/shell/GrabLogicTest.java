package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class GrabLogicTest {
    @Test
    public void serverMustBeHttpsAndKeepsOnlyTheOrigin() {
        assertEquals("https://baihe.example.com", ServerAddress.normalizeServer("baihe.example.com"));
        assertEquals("https://baihe.example.com:8443",
                ServerAddress.normalizeServer("https://Baihe.Example.com:8443/api?x=1"));
        assertNull(ServerAddress.normalizeServer("http://baihe.example.com"));
        assertNull(ServerAddress.normalizeServer("https://user:pw@baihe.example.com"));
        assertNull(ServerAddress.normalizeServer("javascript:alert(1)"));
        assertNull(ServerAddress.normalizeServer("  "));
    }

    @Test
    public void addressBarLoadsOnlyWebPages() {
        assertEquals("https://site.example/a", ServerAddress.browsable("site.example/a"));
        assertEquals("http://site.example", ServerAddress.browsable("http://site.example"));
        assertNull(ServerAddress.browsable("file:///sdcard/x"));
        assertNull(ServerAddress.browsable("intent://x#Intent;end"));
        assertNull(ServerAddress.browsable("two words"));
    }

    @Test
    public void sourceLinkIsPublicHttpsWithoutQuery() {
        assertEquals("https://site.example/c/1", ServerAddress.publicSourceUrl("https://site.example/c/1?t=secret#x"));
        assertEquals("", ServerAddress.publicSourceUrl("http://site.example/c/1"));
        assertEquals("", ServerAddress.publicSourceUrl("https://192.168.1.5/c"));
        assertEquals("", ServerAddress.publicSourceUrl("https://nas.local/c"));
        assertEquals("", ServerAddress.publicSourceUrl("https://nas/c"));
        assertEquals("", ServerAddress.publicSourceUrl(null));
    }

    private static PageImage image(int n, int chars) {
        return new PageImage("p" + n, "", "image/png", "A".repeat(chars));
    }

    @Test
    public void batchesKeepOrderAndRespectTheServersCount() {
        List<PageImage> all = new ArrayList<>();
        for (int i = 0; i < 25; i++) all.add(image(i, 10));
        List<List<PageImage>> batches = Batches.plan(all, 12);
        assertEquals(3, batches.size());
        assertEquals(12, batches.get(0).size());
        assertEquals(1, batches.get(2).size());
        assertEquals("p24", batches.get(2).get(0).key);
    }

    @Test
    public void batchesCloseOnTheByteBudgetWithoutSplittingAnImage() {
        List<PageImage> all = new ArrayList<>();
        all.add(image(0, 30 * 1024 * 1024));
        all.add(image(1, 30 * 1024 * 1024));
        assertEquals(2, Batches.plan(all, 12).size());
        assertTrue(Batches.plan(new ArrayList<>(), 12).isEmpty());
    }

    @Test
    public void oversizedImagesAreRecognised() {
        assertTrue(Batches.tooLarge("A".repeat(17 * 1024 * 1024)));
        assertFalse(Batches.tooLarge("A".repeat(1024)));
    }

    @Test
    public void refusalShowsTheServersTextNotTheRawBody() {
        assertEquals("no such drama", BaiheClient.refusalText(404, "{\"error\":\"no such drama\"}"));
        assertEquals("Baihe did not accept the token.", BaiheClient.refusalText(401, "<html>secret</html>"));
        assertEquals("Baihe refused the request (500).", BaiheClient.refusalText(500, ""));
    }

    @Test
    public void bridgeIgnoresImagesWithoutTheGrabSecret() {
        final List<PageImage> got = new ArrayList<>();
        GrabBridge bridge = new GrabBridge(new GrabBridge.Listener() {
            public void onBegin(List<String> titles, int count) {}

            public void onDone(List<PageImage> images, int unreadable, boolean truncated) {
                got.addAll(images);
            }
        });
        bridge.start("secret");
        bridge.begin("secret", "{\"titles\":[\"T\"],\"count\":2}");
        bridge.image("wrong", "p0", "", "image/png", "AAAA");
        bridge.image("secret", "p1", "", "text/html", "BBBB");
        bridge.done("wrong");
        assertTrue(got.isEmpty());
        bridge.done("secret");
        assertEquals(1, got.size());
        assertEquals("image/jpeg", got.get(0).contentType);
        bridge.image("secret", "p2", "", "image/png", "CCCC");
        bridge.done("secret");
        assertEquals(1, got.size());
    }

    @Test
    public void hostShownIsTheOneTheTokenWillGoTo() {
        assertEquals("baihe.example.com:8443", ServerAddress.hostOf("https://baihe.example.com:8443"));
        assertEquals("baihe.example.com", ServerAddress.hostOf(ServerAddress.normalizeServer("https://baihe.example.com/x")));
    }

    @Test
    public void aSavedTokenIsNeverCarriedToAnotherServer() {
        assertEquals("new", ServerAddress.tokenToCheck(" new ", "old", "https://a.example", "https://a.example"));
        assertEquals("old", ServerAddress.tokenToCheck("", "old", "https://a.example", "https://a.example"));
        assertEquals("", ServerAddress.tokenToCheck("", "old", "https://evil.example", "https://a.example"));
        assertEquals("", ServerAddress.tokenToCheck(null, null, "https://a.example", ""));
    }

    @Test
    public void capsKeepThePhoneInsideItsMemory() {
        assertEquals(24L * 1024 * 1024, GrabBridge.MAX_TOTAL_BASE64_CHARS);
        assertEquals(16L * 1024 * 1024, Batches.MAX_BATCH_BASE64_CHARS);
        // The largest allowed image still fits one batch on its own.
        assertTrue(Batches.MAX_IMAGE_BYTES * 4 / 3 <= Batches.MAX_BATCH_BASE64_CHARS);
        List<PageImage> all = new ArrayList<>();
        for (int i = 0; i < 3; i++) all.add(image(i, 10 * 1024 * 1024));
        assertEquals(3, Batches.plan(all, 12).size());
    }

    @Test
    public void bridgeStopsCollectingPastTheTotalCap() {
        final boolean[] cut = {false};
        final int[] kept = {0};
        GrabBridge bridge = new GrabBridge(new GrabBridge.Listener() {
            public void onBegin(List<String> titles, int count) {}

            public void onDone(List<PageImage> images, int unreadable, boolean truncated) {
                kept[0] = images.size();
                cut[0] = truncated;
            }
        });
        bridge.start("n");
        bridge.begin("n", "{\"titles\":[],\"count\":3}");
        String big = "A".repeat(10 * 1024 * 1024);
        for (int i = 0; i < 3; i++) bridge.image("n", "p" + i, "", "image/png", big);
        bridge.done("n");
        assertEquals(2, kept[0]);
        assertTrue(cut[0]);
    }
}
