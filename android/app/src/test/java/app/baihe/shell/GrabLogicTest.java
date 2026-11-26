package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.json.JSONArray;
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
    public void shellAddressAllowsCleartextOnlyOnPrivateHosts() {
        assertEquals("https://baihe.example.com", ServerAddress.shellAddress("baihe.example.com/x?y=1"));
        assertEquals("http://192.168.1.5:8600", ServerAddress.shellAddress("http://192.168.1.5:8600/"));
        assertEquals("http://100.101.2.3:8600", ServerAddress.shellAddress("http://100.101.2.3:8600"));
        assertNull(ServerAddress.shellAddress("http://baihe.example.com"));
        assertNull(ServerAddress.shellAddress("http://8.8.8.8"));
        assertNull(ServerAddress.shellAddress("http://172.32.0.1"));
        assertNull(ServerAddress.shellAddress("https://user:pw@192.168.1.5"));
        assertNull(ServerAddress.shellAddress("javascript:alert(1)"));
    }

    @Test
    public void onlyTheSavedOriginStaysInTheShell() {
        assertTrue(ServerAddress.sameOrigin("https://b.example", "https://B.example/#/sources"));
        assertFalse(ServerAddress.sameOrigin("https://b.example", "https://accounts.google.com/o/oauth2"));
        assertFalse(ServerAddress.sameOrigin("https://b.example", "http://b.example/"));
        assertFalse(ServerAddress.sameOrigin("https://b.example", "https://b.example:8443/"));
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
        assertEquals(BaiheClient.TOKEN_HELP, BaiheClient.refusalText(401, "<html>secret</html>"));
        assertEquals(BaiheClient.TOKEN_HELP, BaiheClient.refusalText(401,
                "{\"error\":{\"code\":\"unauthenticated\",\"message\":\"Authentication required.\"}}"));
        assertEquals("Not allowed.", BaiheClient.refusalText(403,
                "{\"error\":{\"code\":\"forbidden\",\"message\":\"Not allowed.\"}}"));
        assertEquals("Baihe refused the request (500).", BaiheClient.refusalText(500, ""));
    }

    @Test
    public void bridgeIgnoresImagesWithoutTheGrabSecret() {
        final List<PageImage> got = new ArrayList<>();
        GrabBridge bridge = new GrabBridge(new GrabBridge.Listener() {
            public void onBegin(List<String> titles, int count, JSONArray chapters, String next) {}

            public void onDone(List<PageImage> images, int unreadable, boolean truncated) {
                got.addAll(images);
            }

            public void onText(String text) {}
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
    public void bridgeAnswersSnapshotRequestsOnlyForTheRunningGrabAndSaneValues() {
        final List<String> asked = new ArrayList<>();
        GrabBridge bridge = new GrabBridge(new GrabBridge.Listener() {
            public void onBegin(List<String> titles, int count, JSONArray chapters, String next) {}

            public void onDone(List<PageImage> images, int unreadable, boolean truncated) {}

            public void onText(String text) {}

            public void onSnap(String id, double left, double top, double width, double height, double viewWidth) {
                asked.add("snap " + id);
            }

            public void onStitch(String id, String key) {
                asked.add("stitch " + id + " " + key);
            }
        });
        bridge.start("secret");
        bridge.snap("secret", "s1", 0, 0, 400, 800, 412);
        assertTrue(asked.isEmpty());
        bridge.begin("secret", "{\"titles\":[\"T\"],\"count\":2}");
        bridge.snap("wrong", "s2", 0, 0, 400, 800, 412);
        bridge.snap("secret", "x'); steal();//", 0, 0, 400, 800, 412);
        bridge.snap("secret", "s3", 0, 0, 400, 800, 0);
        bridge.snap("secret", "s4", 0, 0, 400, 800, 412);
        bridge.stitch("secret", "s5", "p7");
        bridge.stitch("secret", "s6", "p7'); x(");
        bridge.stitch("wrong", "s7", "p7");
        assertEquals(java.util.Arrays.asList("snap s4", "stitch s5 p7"), asked);
    }

    @Test
    public void bilibiliMangaOpensAsItsDesktopPage() {
        assertEquals("https://manga.bilibili.com/mc38822/1952411?from=x",
                DesktopSite.rewrite("https://manga.bilibili.com/m/mc38822/1952411?from=x"));
        assertEquals("https://manga.bilibili.com/mc1/2", DesktopSite.rewrite("https://manga.bilibili.com/mc1/2"));
        assertEquals("https://manga.bilibili.com/detail/mc5", DesktopSite.rewrite("https://manga.bilibili.com/detail/mc5"));
        assertEquals(null, DesktopSite.rewrite("https://example.com/m/mc1/2"));
        assertEquals(null, DesktopSite.rewrite("https://manga.bilibili.com.evil.example/m/mc1/2"));
        assertEquals(null, DesktopSite.rewrite(null));
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
            public void onBegin(List<String> titles, int count, JSONArray chapters, String next) {}

            public void onDone(List<PageImage> images, int unreadable, boolean truncated) {
                kept[0] = images.size();
                cut[0] = truncated;
            }

            public void onText(String text) {}
        });
        bridge.start("n");
        bridge.begin("n", "{\"titles\":[],\"count\":3}");
        String big = "A".repeat(10 * 1024 * 1024);
        for (int i = 0; i < 3; i++) bridge.image("n", "p" + i, "", "image/png", big);
        bridge.done("n");
        assertEquals(2, kept[0]);
        assertTrue(cut[0]);
    }

    @Test
    public void onlyImagesFromContentUrisAreAccepted() {
        assertTrue(SharedImages.isImageType("image/jpeg"));
        assertTrue(SharedImages.isImageType("IMAGE/PNG"));
        assertFalse(SharedImages.isImageType("text/plain"));
        assertFalse(SharedImages.isImageType(null));
        assertTrue(SharedImages.isReadableScheme("content"));
        assertFalse(SharedImages.isReadableScheme("file"));
        assertFalse(SharedImages.isReadableScheme(null));
    }

    @Test
    public void sharedImagesAreCappedByCountAndTotalSize() {
        List<Integer> many = new ArrayList<>();
        for (int i = 0; i < 150; i++) many.add(i);
        assertEquals(SharedImages.MAX_IMAGES, SharedImages.limit(many).size());
        assertEquals(0, (int) SharedImages.limit(many).get(0));
        assertTrue(SharedImages.fitsTotal(0, SharedImages.MAX_TOTAL_BASE64_CHARS));
        assertFalse(SharedImages.fitsTotal(SharedImages.MAX_TOTAL_BASE64_CHARS, 1));
    }

    @Test
    public void nextChapterStaysOnTheSameSite() {
        String here = "https://www.site.example/c/1";
        assertEquals("https://site.example/c/2", ServerAddress.nextChapterUrl(here, "https://site.example/c/2"));
        assertNull(ServerAddress.nextChapterUrl(here, "https://other.example/c/2"));
        assertNull(ServerAddress.nextChapterUrl(here, "https://site.example/c/1"));
        assertNull(ServerAddress.nextChapterUrl(here, "javascript:alert(1)"));
        assertNull(ServerAddress.nextChapterUrl(here, "https://u:p@site.example/c/2"));
        assertNull(ServerAddress.nextChapterUrl(here, ""));
        assertNull(ServerAddress.nextChapterUrl("", "https://site.example/c/2"));
    }

    @Test
    public void linksFromTheShellOpenInGrabExceptGoogle() {
        assertTrue(ServerAddress.opensInGrab("https://comic.example/c/1"));
        assertFalse(ServerAddress.opensInGrab("https://accounts.google.com/o/oauth2"));
        assertFalse(ServerAddress.opensInGrab("file:///sdcard/x"));
        assertFalse(ServerAddress.opensInGrab("intent://x#Intent;end"));
    }

    @Test
    public void quickReadKeepsOnlyServerJpegPagesInOrder() throws Exception {
        String jpeg = "data:image/jpeg;base64,/9j/";
        org.json.JSONObject answer = new org.json.JSONObject()
                .put("pages", new org.json.JSONArray()
                        .put(new org.json.JSONObject().put("typeset_image", jpeg))
                        .put(new org.json.JSONObject().put("typeset", false))
                        .put(new org.json.JSONObject().put("typeset_image", "data:text/html;base64,AAAA"))
                        .put(new org.json.JSONObject().put("typeset_image", "data:image/jpeg;base64,!!!"))
                        .put(new org.json.JSONObject().put("typeset_image", jpeg)));
        List<byte[]> pages = QuickRead.typesetPages(answer);
        assertEquals(2, pages.size());
        assertEquals((byte) 0xFF, pages.get(0)[0]);
        assertEquals(0, QuickRead.typesetPages(new org.json.JSONObject()).size());
    }

    @Test
    public void quickReadSummaryNamesWhatHappened() {
        assertEquals("Opened 3 of 4 pages", QuickRead.summary(3, 4, ""));
        assertEquals("Nothing could be translated and redrawn on this page. The monthly cap is spent.",
                QuickRead.summary(0, 2, "The monthly cap is spent."));
    }

    @Test
    public void aPictureWithOnlyAnAddressNeedsDownloading() {
        assertTrue(new PageImage("p0", "https://cdn.example/1.jpg", "image/jpeg", "").needsDownload());
        assertFalse(new PageImage("p1", "https://cdn.example/2.jpg", "image/jpeg", "AAAA").needsDownload());
        assertFalse(new PageImage("p2", "", "image/png", "").needsDownload());
    }

    @Test
    public void quickReadNamesTheFirstWarningOfAPageWithNothingToShow() throws Exception {
        org.json.JSONObject answer = new org.json.JSONObject().put("pages", new org.json.JSONArray()
                .put(new org.json.JSONObject().put("notes", new org.json.JSONArray()
                        .put(new org.json.JSONArray().put("info").put("fine"))
                        .put(new org.json.JSONArray().put("warning").put("no translation engine is set")))));
        assertEquals("no translation engine is set", QuickRead.firstWarning(answer));
        assertEquals("", QuickRead.firstWarning(new org.json.JSONObject()));
    }

    @Test
    public void imageFetchRefusesPrivateAddressesAndScopesCookiesAndReferer() throws Exception {
        for (String ip : new String[] {"127.0.0.1", "192.168.1.5", "10.0.0.2", "169.254.169.254", "100.64.0.1", "0.0.0.0", "fd00::1"}) {
            assertFalse(ip, ImageFetch.isPublic(java.net.InetAddress.getByName(ip)));
        }
        assertTrue(ImageFetch.isPublic(java.net.InetAddress.getByName("93.184.216.34")));
        assertTrue(ImageFetch.sameSite("www.site.com", "cdn.site.com"));
        assertFalse(ImageFetch.sameSite("www.site.com", "evil.example"));
        java.net.URL page = new java.net.URL("https://www.site.com/ch/1?x=2#top");
        assertEquals("https://www.site.com/ch/1?x=2", ImageFetch.refererFor(page, new java.net.URL("https://www.site.com/a.jpg")));
        assertEquals("https://www.site.com/", ImageFetch.refererFor(page, new java.net.URL("https://cdn.other.net/a.jpg")));
        assertNull(ImageFetch.refererFor(page, new java.net.URL("http://cdn.other.net/a.jpg")));
    }

    @Test
    public void onlyACleanSuccessFadesAndFailuresStayOnScreen() {
        assertEquals(GrabUi.Kind.OK, GrabUi.kindOf("Sent 12 pages", true));
        assertEquals(GrabUi.Kind.OK, GrabUi.kindOf("Opened 5 of 5 pages", false));
        assertEquals(GrabUi.Kind.INFO, GrabUi.kindOf("Sent 9 pages. Not sent: 3", true));
        assertEquals(GrabUi.Kind.BAD, GrabUi.kindOf("Could not read the pages.", false));
    }


    @Test
    public void quickReadSessionGrowsAndStopsWhenTheReaderCloses() {
        QuickReadSession.begin();
        assertFalse(QuickReadSession.isComplete());
        QuickReadSession.add("page-000.jpg");
        QuickReadSession.add("page-001.jpg");
        assertEquals(2, QuickReadSession.snapshot().size());
        QuickReadSession.finish();
        assertTrue(QuickReadSession.isComplete());
        QuickReadSession.abandon();
        assertTrue(QuickReadSession.isAbandoned());
        assertEquals(0, QuickReadSession.snapshot().size());
    }

    @Test
    public void cancellingKeepsReadPagesAndTellsTheGrabToStop() {
        QuickReadSession.begin();
        QuickReadSession.add("page-000.jpg");
        boolean[] stopped = {false};
        QuickReadSession.onCancel(() -> stopped[0] = true);
        QuickReadSession.cancel();
        assertTrue(stopped[0]);
        assertTrue(QuickReadSession.isAbandoned());
        assertTrue(QuickReadSession.isComplete());
        assertEquals(1, QuickReadSession.snapshot().size());
        QuickReadSession.onCancel(null);
    }

    @Test
    public void aChapterSplitInPartsIsRecognisedFromItsTitle() {
        int[] part = ServerAddress.partOf(java.util.Arrays.asList("A", "第112話(1/2)"));
        assertEquals(1, part[0]);
        assertEquals(2, part[1]);
        assertEquals(2, ServerAddress.partOf(java.util.Arrays.asList("第5話（2／3）"))[0]);
        assertNull(ServerAddress.partOf(java.util.Arrays.asList("Chapter 5", "Title 2024/05")));
    }

    @Test
    public void aPageWithNoRedrawFallsBackToThePictureThePhoneSent() {
        String data = java.util.Base64.getEncoder().encodeToString(new byte[] {1, 2, 3});
        List<PageImage> batch = new ArrayList<>();
        batch.add(new PageImage("a", "u1", "image/png", data));
        batch.add(new PageImage("b", "u2", "image/png", ""));
        assertEquals(3, QuickRead.original(batch, "a").length);
        assertNull(QuickRead.original(batch, "b"));
        assertNull(QuickRead.original(batch, "missing"));
        assertNull(QuickRead.original(batch, ""));
    }

    @Test
    public void theClosingLineSaysWhatTheLinkCost() {
        UploadStats stats = new UploadStats();
        assertEquals("", stats.describe());
        stats.add(2L * 1048576, 10_000, 4_000);
        stats.add(2L * 1048576, 10_000, 8_000);
        assertEquals("sent 4.0 MB in 2 requests at 205 KB/s each, PC first answer 6 s after", stats.describe());
    }

    @Test
    public void onlyWidePicturesAreScaledDownBeforeAnUpload() {
        assertEquals(ImageShrink.MAX_WIDTH, ImageShrink.targetWidth(3000));
        assertEquals(0, ImageShrink.targetWidth(ImageShrink.MAX_WIDTH));
        assertEquals(0, ImageShrink.targetWidth(900));
    }

    @Test
    public void smallPicturesAreSentAsTheyAre() {
        PageImage small = new PageImage("k", "u", "image/png", "AAAA");
        assertFalse(ImageShrink.worthShrinking(small));
        assertTrue(ImageShrink.forUpload(small) == small);
    }

    @Test
    public void onlyClearNonPagesAreLeftOutOfAnUpload() {
        int[] w = {844, 844, 844, 844, 844, 300, 1600, 0};
        int[] h = {1200, 1200, 1200, 1200, 1200, 420, 1200, 0};
        boolean[] keep = PageFilter.keep(w, h);
        assertTrue(keep[0] && keep[4]);
        assertFalse(keep[5]);
        assertTrue(keep[6]);
        assertTrue(keep[7]);
        boolean[] few = PageFilter.keep(new int[] {844, 300}, new int[] {1200, 420});
        assertTrue(few[0] && few[1]);
    }

    @Test
    public void theFirstRequestCarriesOnePicture() {
        List<PageImage> images = new ArrayList<>();
        for (int i = 0; i < 5; i++) images.add(new PageImage("k" + i, "u", "image/png", "AAAA"));
        List<List<PageImage>> plan = Batches.plan(images, 2, 1);
        assertEquals(1, plan.get(0).size());
        assertEquals(2, plan.get(1).size());
        assertEquals(5, plan.get(0).size() + plan.get(1).size() + plan.get(2).size());
    }

    @Test
    public void onlyPicturesWithAWebAddressAreLeftToThePcToDownload() {
        List<PageImage> batch = new ArrayList<>();
        batch.add(new PageImage("a", "https://cdn.example/1.jpg", "image/jpeg", ""));
        batch.add(new PageImage("b", "", "image/png", "AAAA"));
        List<PageImage> marked = PcFetch.mark(batch);
        assertTrue(marked.get(0).fetchFirst);
        assertFalse(marked.get(1).fetchFirst);
        List<PageImage> none = new ArrayList<>();
        none.add(new PageImage("c", "data:image/png;base64,AAAA", "image/png", "AAAA"));
        assertNull(PcFetch.mark(none));
    }

    @Test
    public void picturesThePcCouldNotFetchAreTheOnesSentFromThePhone() throws Exception {
        List<PageImage> batch = new ArrayList<>();
        batch.add(new PageImage("a", "https://cdn.example/1.jpg", "image/jpeg", ""));
        batch.add(new PageImage("b", "https://cdn.example/2.jpg", "image/jpeg", ""));
        batch.add(new PageImage("c", "https://cdn.example/3.jpg", "image/jpeg", ""));
        JSONArray unfetched = new JSONArray("[{\"key\":\"c\",\"reason\":\"blocked\"},{\"key\":\"a\",\"reason\":\"missing\"}]");
        List<PageImage> again = PcFetch.unfetched(batch, unfetched);
        assertEquals(2, again.size());
        assertEquals("a", again.get(0).key);
        assertEquals("c", again.get(1).key);
        assertTrue(PcFetch.unfetched(batch, null).isEmpty());
    }

    @Test
    public void bridgeHandsOverANovelChapterOnlyWhenNoPicturesCame() {
        final List<String> texts = new ArrayList<>();
        final int[] pictureRuns = {0};
        GrabBridge bridge = new GrabBridge(new GrabBridge.Listener() {
            public void onBegin(List<String> titles, int count, JSONArray chapters, String next) {}

            public void onDone(List<PageImage> images, int unreadable, boolean truncated) {
                pictureRuns[0]++;
            }

            public void onText(String text) {
                texts.add(text);
            }
        });
        bridge.start("n");
        bridge.begin("n", "{\"titles\":[],\"count\":0}");
        bridge.text("wrong", "ignored");
        bridge.text("n", "第一章\n正文");
        bridge.done("n");
        assertEquals(1, texts.size());
        assertEquals("第一章\n正文", texts.get(0));
        assertEquals(0, pictureRuns[0]);
        bridge.start("m");
        bridge.begin("m", "{\"titles\":[],\"count\":1}");
        bridge.text("m", "prose");
        bridge.image("m", "p0", "", "image/png", "AAAA");
        bridge.done("m");
        assertEquals(1, texts.size());
        assertEquals(1, pictureRuns[0]);
    }
}
