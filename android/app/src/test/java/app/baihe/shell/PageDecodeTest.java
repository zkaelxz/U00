package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class PageDecodeTest {
    @Test
    public void aRedoneOrFlippedPageGetsANewKey() {
        assertNotEquals(PageDecode.key("/q/p1.jpg", 0), PageDecode.key("/q/p1.jpg", 1));
        assertNotEquals(PageDecode.key("/q/p1.jpg", 0), PageDecode.key("/q/p1.orig.jpg", 0));
        assertEquals(PageDecode.key("/q/p1.jpg", 2), PageDecode.key("/q/p1.jpg", 2));
    }

    @Test
    public void aRecycledRowNeverTakesAnotherPagesPicture() {
        String shown = PageDecode.key("/q/p2.jpg", 0);
        assertTrue(PageDecode.rowStillShows(shown, PageDecode.key("/q/p2.jpg", 0)));
        assertFalse(PageDecode.rowStillShows(PageDecode.key("/q/p9.jpg", 0), PageDecode.key("/q/p2.jpg", 0)));
        assertFalse(PageDecode.rowStillShows(PageDecode.key("/q/p2.jpg", 1), PageDecode.key("/q/p2.jpg", 0)));
        assertFalse(PageDecode.rowStillShows(null, shown));
    }

    @Test
    public void theWaitingRowHasThePagesShape() {
        assertEquals(1600, PageDecode.placeholderHeight(1100, 1100, 1600, 0));
        assertEquals(800, PageDecode.placeholderHeight(550, 1100, 1600, 0));
        assertEquals(12000, PageDecode.placeholderHeight(720, 720, 12000, 0));
        assertEquals(900, PageDecode.placeholderHeight(0, 1100, 1600, 900));
        assertEquals(900, PageDecode.placeholderHeight(1080, 0, 0, 900));
    }

    @Test
    public void wideScansAreHalvedOnlyWhileStillScreenWide() {
        assertEquals(1, PageDecode.sampleSize(1080, 1080));
        assertEquals(2, PageDecode.sampleSize(2160, 1080));
        assertEquals(2, PageDecode.sampleSize(3000, 1080));
        assertEquals(4, PageDecode.sampleSize(4400, 1080));
        assertEquals(1, PageDecode.sampleSize(4400, 0));
    }
}
