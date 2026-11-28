package app.baihe.shell;

import android.app.DownloadManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.widget.Toast;
import java.io.IOException;
import java.io.OutputStream;
import java.util.Base64;

/** Saves a file Baihe offers for download (a subtitled episode) into the phone's Downloads folder. */
final class SaveDownload {
    private SaveDownload() {}

    /**
     * Hands the download to the system's download manager with the signed-in cookie, so it works
     * with no signal later. Only the saved Baihe address is served: any other link is ignored, so
     * the cookie cannot be sent elsewhere.
     */
    /** Decoded, so a page cannot hand the bridge a file that fills the phone. */
    static final int MAX_FILE_BYTES = 50 * 1024 * 1024;
    static final String TOO_OLD = "This phone's Android can't save files from the app. "
            + "Open Baihe in the phone's browser to save it.";

    /** A Downloads name with no folder part or control characters, kept short. */
    static String cleanName(String name) {
        String clean = name == null ? "" : name.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_").trim();
        while (clean.startsWith(".")) clean = clean.substring(1);
        if (clean.length() > 120) {
            int dot = clean.lastIndexOf('.');
            String ext = dot > 0 && clean.length() - dot <= 10 ? clean.substring(dot) : "";
            clean = clean.substring(0, 120 - ext.length()) + ext;
        }
        return clean.isEmpty() ? "baihe-download" : clean;
    }

    /**
     * Saves a file the page made itself (a blob: link, which the download manager cannot fetch)
     * into Downloads. Called from the page through the bridge, off the main thread.
     */
    static boolean saveBytes(Context context, String name, String mimeType, String base64) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            toast(context, TOO_OLD);
            return false;
        }
        String fileName = cleanName(name);
        byte[] data;
        try {
            data = Base64.getDecoder().decode(base64 == null ? "" : base64);
        } catch (IllegalArgumentException e) {
            toast(context, "Could not save the file.");
            return false;
        }
        if (data.length > MAX_FILE_BYTES) {
            toast(context, "That file is too big to save from the app. Open Baihe in the phone's browser to save it.");
            return false;
        }
        ContentResolver resolver = context.getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, fileName);
        if (mimeType != null && !mimeType.isEmpty()) values.put(MediaStore.MediaColumns.MIME_TYPE, mimeType);
        values.put(MediaStore.MediaColumns.IS_PENDING, 1);
        Uri item = null;
        try {
            item = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
            if (item == null) throw new IOException("Downloads refused the file.");
            try (OutputStream out = resolver.openOutputStream(item)) {
                if (out == null) throw new IOException("Downloads refused the file.");
                out.write(data);
            }
            values.clear();
            values.put(MediaStore.MediaColumns.IS_PENDING, 0);
            resolver.update(item, values, null, null);
            toast(context, "Saved " + fileName + " to Downloads.");
            return true;
        } catch (IOException | RuntimeException e) {
            // A half-written entry would show in Downloads as a broken file.
            if (item != null) {
                try {
                    resolver.delete(item, null, null);
                } catch (RuntimeException ignored) {
                    // Nothing more to tidy.
                }
            }
            toast(context, "Could not save the file. The phone may be full.");
            return false;
        }
    }

    private static void toast(Context context, String text) {
        new Handler(Looper.getMainLooper()).post(() -> Toast.makeText(context, text, Toast.LENGTH_LONG).show());
    }

    static boolean enqueue(Context context, String address, String url, String userAgent,
                           String contentDisposition, String mimeType) {
        if (url != null && (url.startsWith("blob:") || url.startsWith("data:"))) {
            // The download manager can't fetch a page's own file. Baihe's pages hand those to saveFile, except
            // an older Baihe and a file over MAX_FILE_BYTES, which land here.
            Toast.makeText(context, "This download doesn't work in the app. Open Baihe in the phone's browser to save it.",
                    Toast.LENGTH_LONG).show();
            return false;
        }
        if (address == null || url == null || !ServerAddress.sameOrigin(address, url)) return false;
        try {
            DownloadManager.Request request = new DownloadManager.Request(Uri.parse(url));
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) request.addRequestHeader("Cookie", cookie);
            if (userAgent != null) request.addRequestHeader("User-Agent", userAgent);
            if (mimeType != null && !mimeType.isEmpty()) request.setMimeType(mimeType);
            request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS,
                    URLUtil.guessFileName(url, contentDisposition, mimeType));
            DownloadManager manager = (DownloadManager) context.getSystemService(Context.DOWNLOAD_SERVICE);
            if (manager == null) return false;
            manager.enqueue(request);
            Toast.makeText(context, "Saving to Downloads...", Toast.LENGTH_SHORT).show();
            return true;
        } catch (RuntimeException e) {
            Toast.makeText(context, "Could not save the file.", Toast.LENGTH_LONG).show();
            return false;
        }
    }
}
