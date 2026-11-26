package app.baihe.shell;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class BubbleHitTest {
    private final int[][] boxes = {{100, 100, 200, 300}, {150, 150, 50, 50}, {0, 0, 0, 0}};

    @Test
    public void aTapLandsOnTheBoxItIsInsideAndTheSmallestOfNestedOnes() {
        assertEquals(0, BubbleHit.at(boxes, 1000, 2000, 0.25f, 0.1f));
        assertEquals(1, BubbleHit.at(boxes, 1000, 2000, 0.17f, 0.085f));
    }

    @Test
    public void aTapOnNoLetteringOrOffThePageHitsNothing() {
        assertEquals(-1, BubbleHit.at(boxes, 1000, 2000, 0.9f, 0.9f));
        assertEquals(-1, BubbleHit.at(boxes, 1000, 2000, 1.2f, 0.1f));
        assertEquals(-1, BubbleHit.at(boxes, 0, 0, 0.5f, 0.5f));
        assertEquals(-1, BubbleHit.at(new int[0][], 1000, 2000, 0.5f, 0.5f));
    }

    @Test
    public void aShortTextIsToldApartByItsScript() {
        assertEquals("ja", BubbleHit.languageOf("これは何？"));
        assertEquals("ja", BubbleHit.languageOf("今日はいい天気"));
        assertEquals("ko", BubbleHit.languageOf("안녕"));
        assertEquals("zh", BubbleHit.languageOf("你好吗"));
        assertEquals("", BubbleHit.languageOf("Hello"));
        assertEquals("", BubbleHit.languageOf(null));
    }
}
