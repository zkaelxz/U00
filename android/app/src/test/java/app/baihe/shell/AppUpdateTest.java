package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import android.content.pm.PackageInstaller;

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

    private static final long DAY = AppUpdate.CHECK_EVERY_MS;
    private static final long HOUR = AppUpdate.POLL_EVERY_MS;
    private static final long NOW = 100 * DAY;

    @Test
    public void aFirstCheckIsDue() {
        assertTrue(AppUpdate.checkDue(NOW, 0, false, 0));
    }

    @Test
    public void anAnsweredCheckWaitsADay() {
        // Opening the app, coming back to it and the hourly timer all leave an up-to-date app alone.
        assertFalse(AppUpdate.checkDue(NOW, NOW - HOUR, false, 0));
        assertFalse(AppUpdate.checkDue(NOW, NOW - DAY + HOUR, false, NOW - HOUR));
        assertTrue(AppUpdate.checkDue(NOW, NOW - DAY, false, NOW - HOUR));
    }

    @Test
    public void aFailedCheckIsTriedAtEveryOpenAndEveryHour() {
        // A launch after a failed check in an earlier run, a return to the app and the hourly timer all ask again.
        assertTrue(AppUpdate.checkDue(NOW, NOW - HOUR, true, 0));
        assertTrue(AppUpdate.checkDue(NOW, NOW - HOUR, true, NOW - 5 * 60_000));
        assertTrue(AppUpdate.checkDue(NOW, NOW - 2 * DAY, true, NOW - HOUR));
    }

    @Test
    public void aLaunchAsksOnceNotTwice() {
        assertFalse(AppUpdate.checkDue(NOW, 0, false, NOW - 1000));
        assertFalse(AppUpdate.checkDue(NOW, 0, true, NOW - 1000));
    }

    @Test
    public void aClockSetBackDoesNotHoldChecksOff() {
        assertTrue(AppUpdate.checkDue(NOW, NOW + DAY, false, 0));
        assertTrue(AppUpdate.checkDue(NOW, NOW - HOUR, true, NOW + DAY));
    }

    @Test
    public void aSavedApkIsClearedOnceThisAppHasItsBuild() {
        assertTrue(AppUpdate.savedApkDone(1100, 1100));
        assertTrue(AppUpdate.savedApkDone(1100, 1200));
        assertTrue(AppUpdate.savedApkDone(0, 1100));
        // Still waiting to be installed, for example while the person allows installs from Baihe.
        assertFalse(AppUpdate.savedApkDone(1200, 1100));
    }

    @Test
    public void onlyANewerBuildOfThisAppGoesToTheInstaller() {
        assertNull(AppUpdate.apkProblem("app.baihe.shell", "app.baihe.shell", 1200, 1100));
        assertNotEquals(null, AppUpdate.apkProblem("app.baihe.shell", null, 0, 1100));
        assertNotEquals(null, AppUpdate.apkProblem("app.baihe.shell", "com.example.other", 1200, 1100));
        assertNotEquals(null, AppUpdate.apkProblem("app.baihe.shell", "app.baihe.shell", 1100, 1100));
    }

    @Test
    public void aRefusedDownloadSaysWhy() {
        assertTrue(AppUpdate.downloadProblem(401).contains("Sign in again"));
        assertTrue(AppUpdate.downloadProblem(404).contains("Get the newest phone app"));
        assertTrue(AppUpdate.downloadProblem(500).contains("HTTP 500"));
    }

    @Test
    public void everyInstallerFailureHasItsOwnWords() {
        String general = InstallResult.failureText(PackageInstaller.STATUS_FAILURE);
        assertTrue(general.startsWith("The update did not install"));
        assertNotEquals(general, InstallResult.failureText(PackageInstaller.STATUS_FAILURE_STORAGE));
        assertNotEquals(general, InstallResult.failureText(PackageInstaller.STATUS_FAILURE_CONFLICT));
        assertNotEquals(general, InstallResult.failureText(PackageInstaller.STATUS_FAILURE_INVALID));
        assertNotEquals(general, InstallResult.failureText(PackageInstaller.STATUS_FAILURE_BLOCKED));
    }
}
