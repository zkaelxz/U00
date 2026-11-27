package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;

import org.junit.Test;

public class PaletteLookTest {
    @Test
    public void sepiaUsesThePageSurfaceAndIsALightLook() {
        GrabUi.Palette sepia = new GrabUi.Palette(false, true);
        assertEquals(0xFFEBE0C6, sepia.surface);
        assertFalse(sepia.dark);
    }

    @Test
    public void darkWinsOverSepia() {
        assertEquals(new GrabUi.Palette(true).surface, new GrabUi.Palette(true, true).surface);
    }
}
