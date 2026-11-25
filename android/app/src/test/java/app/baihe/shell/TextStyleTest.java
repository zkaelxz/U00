package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class TextStyleTest {
    @Test
    public void theDefaultAsksForNothingSpecial() {
        TextStyle plain = new TextStyle(0, false, 0);
        assertTrue(plain.isDefault());
        assertEquals(0, plain.minFont());
        assertEquals("default", plain.fontName());
    }

    @Test
    public void aLargerSizeRaisesTheSmallestTextAndComicLetteringNamesItsFont() {
        assertEquals(22, new TextStyle(2, false, 0).minFont());
        assertFalse(new TextStyle(2, true, 1).isDefault());
        assertEquals("bangers", new TextStyle(0, false, 1).fontName());
    }

    @Test
    public void savedValuesOutOfRangeFallBackToTheNearestChoice() {
        assertEquals(30, new TextStyle(99, false, 0).minFont());
        assertEquals(0, new TextStyle(-4, false, 0).minFont());
        assertEquals("bangers", new TextStyle(0, false, 7).fontName());
        assertEquals("default", new TextStyle(0, false, -1).fontName());
    }
}
