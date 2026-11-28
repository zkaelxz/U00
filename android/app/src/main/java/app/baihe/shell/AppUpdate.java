package app.baihe.shell;

import org.json.JSONException;
import org.json.JSONObject;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Asks the saved Baihe address whether it holds a newer build of this app and fetches it.
 * The session cookie goes only to that origin and a redirect is never followed, so the
 * APK can't be steered to another host. Android itself refuses an install that was not
 * signed by the same key as the installed app.
 */
final class AppUpdate {
    private static final int TIMEOUT_MS = 15_000;
    /** The server downloads the APK from GitHub before it answers a refreshing check. */
    private static final int REFRESH_TIMEOUT_MS = 90_000;
    /** The server answer is a few bytes; the cap is only a guard against a wrong host. */
    private static final int MAX_ANSWER_BYTES = 4096;
    private static final long MAX_APK_BYTES = 200L * 1024 * 1024;

    private AppUpdate() {}

    /** The offered versionCode from the server's answer, or 0 when it holds none or sent nonsense. */
    static long offeredVersion(String body) {
        try {
            return new JSONObject(body).optLong("version_code", 0);
        } catch (JSONException e) {
            return 0;
        }
    }

    static boolean isNewer(long installed, long offered) {
        return offered > installed;
    }

    /** Returned by {@link #check} when the server could not be asked, so the caller tries again sooner. */
    static final long CHECK_FAILED = -1;

    /** The PC's rule for its own updates: a check that got an answer waits a day for the next one. */
    static final long CHECK_EVERY_MS = 24L * 60 * 60_000;
    /** After a failed check, every open of or return to the app asks again, and so does this timer while it stays open. */
    static final long POLL_EVERY_MS = 60L * 60_000;
    /** A launch fires both a resume and a page load; one ask covers both. */
    static final long OPEN_GAP_MS = 60_000;

    /**
     * Whether an automatic check is due: always after a failed one, otherwise a day after the server last
     * answered. {@code lastAnsweredAt} is 0 before any answer; {@code lastTriedAt} is when this process last asked.
     */
    static boolean checkDue(long now, long lastAnsweredAt, boolean failed, long lastTriedAt) {
        if (lastTriedAt <= now && now - lastTriedAt < OPEN_GAP_MS) return false;
        if (failed) return true;
        // A clock set back must not hold the next check off for longer than a day.
        return lastAnsweredAt <= 0 || lastAnsweredAt > now || now - lastAnsweredAt >= CHECK_EVERY_MS;
    }

    /** Where Install update keeps the downloaded APK until this app is on its build. */
    static File savedApk(Context context) {
        File dir = new File(context.getCacheDir(), "updates");
        dir.mkdirs();
        return new File(dir, "baihe-shell.apk");
    }

    /** A saved APK has served its purpose once this app is on its build or newer; 0 is a file Android can't read. */
    static boolean savedApkDone(long savedVersion, long installed) {
        return savedVersion <= 0 || savedVersion <= installed;
    }

    /** Why a downloaded file must not go to the installer, or null when it is a newer build of this app. */
    static String apkProblem(String ownPackage, String apkPackage, long apkVersion, long installed) {
        if (apkPackage == null) return "The downloaded update isn't an Android app the phone can read, so it was deleted. Try again.";
        if (!apkPackage.equals(ownPackage)) return "The PC handed over a different app instead of the Baihe update, so it was not installed.";
        if (apkVersion <= installed) return "The update on the PC is not newer than this app, so there is nothing to install.";
        return null;
    }

    /** What to tell the person when the APK download got an HTTP answer other than 200. */
    static String downloadProblem(int status) {
        if (status == 401 || status == 403) return "Baihe on the PC did not accept this phone's sign-in. Sign in again, then try the update.";
        if (status == 404) return "The PC no longer holds a phone-app build. On the PC, press Get the newest phone app in Settings > Updates, then try again.";
        return "The PC could not hand over the update (HTTP " + status + "). Try again in a minute.";
    }

    static final String DOWNLOAD_UNREACHABLE = "Could not reach the PC to download the update. Check the phone can open Baihe, then try again.";
    static final String DOWNLOAD_CUT_OFF = "The update download was cut off before it finished. Try again.";

    /** The offered versionCode, 0 when the server holds none, or {@link #CHECK_FAILED} when it could not be asked.
     * {@code refresh} makes the server look at GitHub first, for a build published since it last looked. */
    static long check(String address, String cookie, boolean refresh) {
        try {
            HttpURLConnection conn = open(address + "/api/app/android" + (refresh ? "?refresh=true" : ""), cookie);
            try {
                if (conn.getResponseCode() != 200) return CHECK_FAILED;
                try (InputStream in = conn.getInputStream()) {
                    byte[] buffer = new byte[MAX_ANSWER_BYTES];
                    int filled = 0;
                    int n;
                    // One read() can return part of the body, so read until it ends or the cap is hit.
                    while (filled < buffer.length && (n = in.read(buffer, filled, buffer.length - filled)) > 0) {
                        filled += n;
                    }
                    return filled == 0 ? 0 : offeredVersion(new String(buffer, 0, filled, "UTF-8"));
                }
            } finally {
                conn.disconnect();
            }
        } catch (IOException | RuntimeException e) {
            return CHECK_FAILED;
        }
    }

    /** Saves the offered APK to {@code target}; null when the whole file arrived, otherwise what went wrong. */
    static String download(String address, String cookie, File target) {
        try {
            HttpURLConnection conn = open(address + "/api/app/android/apk", cookie);
            try {
                int status = conn.getResponseCode();
                if (status != 200) return downloadProblem(status);
                long expected = conn.getContentLengthLong();
                long total = 0;
                try (InputStream in = conn.getInputStream(); OutputStream out = new FileOutputStream(target)) {
                    byte[] buffer = new byte[16 * 1024];
                    int n;
                    while ((n = in.read(buffer)) > 0) {
                        total += n;
                        if (total > MAX_APK_BYTES) {
                            target.delete();
                            return "The update on the PC is larger than any phone-app build, so it was not downloaded.";
                        }
                        out.write(buffer, 0, n);
                    }
                }
                // A dropped Wi-Fi connection can end the stream early without an error.
                if (total == 0 || (expected >= 0 && total != expected)) {
                    target.delete();
                    return DOWNLOAD_CUT_OFF;
                }
                return null;
            } finally {
                conn.disconnect();
            }
        } catch (IOException | RuntimeException e) {
            target.delete();
            return DOWNLOAD_UNREACHABLE;
        }
    }

    /**
     * Hands the saved APK to Android's installer, which asks the person to confirm. The caller has
     * already made sure installs from Baihe are allowed. Returns false if the session could not start.
     */
    static boolean install(Context context, File apk) {
        PackageInstaller installer = context.getPackageManager().getPackageInstaller();
        try {
            PackageInstaller.SessionParams params =
                    new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
            int id = installer.createSession(params);
            try (PackageInstaller.Session session = installer.openSession(id);
                 InputStream in = new FileInputStream(apk);
                 OutputStream out = session.openWrite("baihe-shell", 0, apk.length())) {
                byte[] buffer = new byte[16 * 1024];
                int n;
                while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
                session.fsync(out);
                out.close();
                // The installer fills this intent in, so it must be mutable.
                PendingIntent result = PendingIntent.getBroadcast(context, id,
                        new Intent(context, InstallResult.class).setPackage(context.getPackageName()),
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
                session.commit(result.getIntentSender());
            }
            return true;
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }

    /** A small text answer (at most 64 KB) from the signed-in server, or null when it could not be had. */
    static String fetchText(String url, String cookie) {
        try {
            HttpURLConnection conn = open(url, cookie);
            try {
                if (conn.getResponseCode() != 200) return null;
                try (InputStream in = conn.getInputStream()) {
                    byte[] buffer = new byte[64 * 1024];
                    int filled = 0;
                    int n;
                    while (filled < buffer.length && (n = in.read(buffer, filled, buffer.length - filled)) > 0) filled += n;
                    return new String(buffer, 0, filled, "UTF-8");
                }
            } finally {
                conn.disconnect();
            }
        } catch (IOException | RuntimeException e) {
            return null;
        }
    }

    private static HttpURLConnection open(String url, String cookie) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setInstanceFollowRedirects(false);
        conn.setConnectTimeout(TIMEOUT_MS);
        conn.setReadTimeout(url.endsWith("refresh=true") ? REFRESH_TIMEOUT_MS : TIMEOUT_MS);
        if (cookie != null) conn.setRequestProperty("Cookie", cookie);
        return conn;
    }
}
