package app.baihe.shell;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class NightFilterTest {
    @Test
    public void fullSliderLeavesBrightnessToTheSystem() {
        assertEquals(-1f, NightFilter.windowBrightness(100), 0f);
    }

    @Test
    public void sliderNeverGoesDark() {
        assertEquals(NightFilter.MIN_BRIGHTNESS, NightFilter.windowBrightness(0), 0f);
        assertEquals(0.5f, NightFilter.windowBrightness(50), 0.001f);
    }

    @Test
    public void tintIsClearWhenOffAndAmberWhenOn() {
        assertEquals(0, NightFilter.tint(false, 100));
        assertEquals(0x66FF8C1A, NightFilter.tint(true, 100));
        assertEquals(0x00FF8C1A, NightFilter.tint(true, -5));
    }
}
