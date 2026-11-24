package app.baihe.shell;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class QuickReadZoomTest {
    @Test
    public void scaleStaysBetweenFitAndFourTimes() {
        assertEquals(1f, QuickReadZoom.clampScale(0.4f), 0f);
        assertEquals(2.5f, QuickReadZoom.clampScale(2.5f), 0f);
        assertEquals(4f, QuickReadZoom.clampScale(9f), 0f);
    }

    @Test
    public void panNeverShowsPastEitherPageEdge() {
        assertEquals(0f, QuickReadZoom.clampPan(50f, 2f, 1000f), 0f);
        assertEquals(-1000f, QuickReadZoom.clampPan(-5000f, 2f, 1000f), 0f);
        assertEquals(-300f, QuickReadZoom.clampPan(-300f, 2f, 1000f), 0f);
        assertEquals(0f, QuickReadZoom.clampPan(-300f, 1f, 1000f), 0f);
    }

    @Test
    public void zoomKeepsThePointUnderTheFingers() {
        // The page point at 600 px stays under a finger at 600 px: 600 - 600 * 2.
        assertEquals(-600f, QuickReadZoom.panAfterZoom(0f, 1f, 2f, 600f, 1000f), 0f);
        // Zooming back out to fit returns the page to the left edge.
        assertEquals(0f, QuickReadZoom.panAfterZoom(-600f, 2f, 1f, 600f, 1000f), 0f);
    }

    @Test
    public void zoomScrollsSoTheRowUnderTheFingersStays() {
        // At 2x the row 800 px down the screen sits 400 px down the list, so the list moves up by 400.
        assertEquals(400, QuickReadZoom.scrollAfterZoom(800f, 1f, 2f));
        assertEquals(-400, QuickReadZoom.scrollAfterZoom(800f, 2f, 1f));
        assertEquals(0, QuickReadZoom.scrollAfterZoom(0f, 1f, 4f));
    }

    @Test
    public void scaledListEndsAtTheScreenBottom() {
        assertEquals(2000, QuickReadZoom.listHeight(2000, 1f));
        assertEquals(1000, QuickReadZoom.listHeight(2000, 2f));
        assertEquals(667, QuickReadZoom.listHeight(2000, 3f));
    }

    @Test
    public void doubleTapTogglesBetweenFitAndTwoTimes() {
        assertEquals(2f, QuickReadZoom.afterDoubleTap(1f), 0f);
        assertEquals(1f, QuickReadZoom.afterDoubleTap(3.2f), 0f);
    }
}
