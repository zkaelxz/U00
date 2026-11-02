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

    /** The offered versionCode, or 0 when none is offered or the check failed. */
    static long check(String address, String cookie) {
        try {
            HttpURLConnection conn = open(address + "/api/app/android", cookie);
            try {
                if (conn.getResponseCode() != 200) return 0;
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
            return 0;
        }
    }

    /** Saves the offered APK to {@code target}; false when the server did not hand over a file. */
    static boolean download(String address, String cookie, File target) {
        try {
            HttpURLConnection conn = open(address + "/api/app/android/apk", cookie);
            try {
                if (conn.getResponseCode() != 200) return false;
                long total = 0;
                try (InputStream in = conn.getInputStream(); OutputStream out = new FileOutputStream(target)) {
                    byte[] buffer = new byte[16 * 1024];
                    int n;
                    while ((n = in.read(buffer)) > 0) {
                        total += n;
                        if (total > MAX_APK_BYTES) {
                            target.delete();
                            return false;
                        }
                        out.write(buffer, 0, n);
                    }
                }
                return total > 0;
            } finally {
                conn.disconnect();
            }
        } catch (IOException | RuntimeException e) {
            target.delete();
            return false;
        }
    }

    /**
     * Hands the saved APK to Android's installer, which asks the user to confirm (and, the
     * first time, to allow installs from Baihe). Returns false if the session could not start.
     */
    static boolean install(Context context, File apk) {
        PackageInstaller installer = context.getPackageManager().getPackageInstaller();
        try {
            int id = installer.createSession(
                    new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL));
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

    private static HttpURLConnection open(String url, String cookie) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setInstanceFollowRedirects(false);
        conn.setConnectTimeout(TIMEOUT_MS);
        conn.setReadTimeout(TIMEOUT_MS);
        if (cookie != null) conn.setRequestProperty("Cookie", cookie);
        return conn;
    }
}
