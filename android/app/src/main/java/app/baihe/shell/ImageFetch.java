package app.baihe.shell;

import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Downloads a page picture the site would not let the page's script read, the way the browser
 * did: with the page address as Referer and the page's own cookies and user agent.
 */
final class ImageFetch {
    private static final int TIMEOUT_MS = 20_000;

    private ImageFetch() {}

    /** The picture with its bytes filled in, or null when it is not an image or cannot be had. */
    static PageImage download(PageImage wanted, String referer, String cookie, String agent) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(wanted.url);
            if (!"https".equals(url.getProtocol()) && !"http".equals(url.getProtocol())) return null;
            conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(TIMEOUT_MS);
            conn.setReadTimeout(TIMEOUT_MS);
            conn.setRequestProperty("Referer", referer);
            conn.setRequestProperty("User-Agent", agent);
            conn.setRequestProperty("Accept", "image/*");
            if (cookie != null && !cookie.isEmpty()) conn.setRequestProperty("Cookie", cookie);
            if (conn.getResponseCode() != 200) return null;
            String type = conn.getContentType();
            if (!SharedImages.isImageType(type)) return null;
            type = type.split(";")[0].trim().toLowerCase(java.util.Locale.ROOT);
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buffer = new byte[8192];
                int n;
                while ((n = in.read(buffer)) > 0) {
                    if (out.size() + n > Batches.MAX_IMAGE_BYTES) return null;
                    out.write(buffer, 0, n);
                }
                if (out.size() == 0) return null;
                return new PageImage(wanted.key, wanted.url, type, Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP));
            }
        } catch (IOException | RuntimeException e) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }
}
