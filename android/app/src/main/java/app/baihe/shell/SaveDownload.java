package app.baihe.shell;

import android.app.DownloadManager;
import android.content.Context;
import android.net.Uri;
import android.os.Environment;
import android.webkit.CookieManager;
import android.webkit.URLUtil;
import android.widget.Toast;

/** Saves a file Baihe offers for download (a subtitled episode) into the phone's Downloads folder. */
final class SaveDownload {
    private SaveDownload() {}

    /**
     * Hands the download to the system's download manager with the signed-in cookie, so it works
     * with no signal later. Only the saved Baihe address is served: any other link is ignored, so
     * the cookie cannot be sent elsewhere.
     */
    static boolean enqueue(Context context, String address, String url, String userAgent,
                           String contentDisposition, String mimeType) {
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
