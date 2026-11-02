package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class AppUpdateTest {
    @Test
    public void readsTheOfferedVersion() {
        assertEquals(7, AppUpdate.offeredVersion("{\"version_code\": 7}"));
    }

    @Test
    public void noBuildOrNonsenseOffersNothing() {
        assertEquals(0, AppUpdate.offeredVersion("{\"version_code\": null}"));
        assertEquals(0, AppUpdate.offeredVersion("{}"));
        assertEquals(0, AppUpdate.offeredVersion("<html>"));
    }

    @Test
    public void onlyAHigherVersionIsAnUpdate() {
        assertTrue(AppUpdate.isNewer(1, 2));
        assertFalse(AppUpdate.isNewer(2, 2));
        assertFalse(AppUpdate.isNewer(3, 2));
        assertFalse(AppUpdate.isNewer(1, 0));
    }
}
