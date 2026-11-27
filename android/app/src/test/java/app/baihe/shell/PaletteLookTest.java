package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class PaletteLookTest {
    @Test
    public void sepiaUsesThePageSurfaceAndIsALightLook() {
        GrabUi.Palette sepia = new GrabUi.Palette("sepia");
        assertEquals(0xFFEBE0C6, sepia.surface);
        assertFalse(sepia.dark);
    }

    @Test
    public void oledIsADarkLookWithItsOwnBlackSurface() {
        GrabUi.Palette oled = new GrabUi.Palette("oled");
        assertTrue(oled.dark);
        assertEquals(0xFF000000, oled.bg);
    }

    @Test
    public void lightAndDarkKeepTheirColoursAndAnUnknownLookIsLight() {
        assertEquals(new GrabUi.Palette(true).surface, new GrabUi.Palette("dark").surface);
        assertEquals(new GrabUi.Palette(false).surface, new GrabUi.Palette("unknown").surface);
    }
}
