package app.baihe.shell;

import android.util.Base64;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.URL;

/**
 * Downloads a page picture the site would not let the page's script read, the way the browser
 * did: with the page's cookies and user agent, but only toward public hosts. The page picks the
 * address, so every hop is checked here instead of trusting it.
 */
final class ImageFetch {
    private static final int TIMEOUT_MS = 20_000;
    private static final long TOTAL_MS = 60_000;
    private static final int MAX_HOPS = 4;

    private ImageFetch() {}

    /** Cookies for an address; the app passes the WebView's store. */
    interface Cookies {
        String forUrl(String url);
    }

    /** The picture with its bytes filled in, or null when it is not an image or cannot be had. */
    static PageImage download(PageImage wanted, String pageUrl, Cookies cookies, String agent) {
        long deadline = System.currentTimeMillis() + TOTAL_MS;
        HttpURLConnection conn = null;
        try {
            URL page = new URL(pageUrl);
            URL url = new URL(wanted.url);
            for (int hop = 0; ; hop++) {
                if (!allowed(url)) return null;
                // Redirects are followed by hand: the built-in follower would resend our Cookie
                // header to whatever host an open redirect points at.
                conn = (HttpURLConnection) url.openConnection();
                conn.setInstanceFollowRedirects(false);
                conn.setConnectTimeout(TIMEOUT_MS);
                conn.setReadTimeout(TIMEOUT_MS);
                conn.setRequestProperty("User-Agent", agent);
                conn.setRequestProperty("Accept", "image/*");
                String referer = refererFor(page, url);
                if (referer != null) conn.setRequestProperty("Referer", referer);
                String cookie = sameSite(page.getHost(), url.getHost()) ? cookies.forUrl(url.toString()) : null;
                if (cookie != null && !cookie.isEmpty()) conn.setRequestProperty("Cookie", cookie);
                int status = conn.getResponseCode();
                if (status >= 300 && status < 400) {
                    String location = conn.getHeaderField("Location");
                    if (location == null || hop >= MAX_HOPS) return null;
                    URL next = new URL(url, location);
                    boolean downgrade = "https".equals(url.getProtocol()) && !"https".equals(next.getProtocol());
                    if (downgrade) return null;
                    conn.disconnect();
                    url = next;
                    continue;
                }
                if (status != 200) return null;
                break;
            }
            String type = conn.getContentType();
            if (!SharedImages.isImageType(type)) return null;
            type = type.split(";")[0].trim().toLowerCase(java.util.Locale.ROOT);
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buffer = new byte[8192];
                int n;
                while ((n = in.read(buffer)) > 0) {
                    // The read timeout only covers a silent host; a trickle needs this limit.
                    if (System.currentTimeMillis() > deadline) return null;
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

    /** Only http(s) to hosts on the public internet: never the phone, its LAN or metadata addresses. */
    static boolean allowed(URL url) {
        String scheme = url.getProtocol();
        if (!"https".equals(scheme) && !"http".equals(scheme)) return false;
        try {
            for (InetAddress a : InetAddress.getAllByName(url.getHost())) {
                if (!isPublic(a)) return false;
            }
            return true;
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }

    static boolean isPublic(InetAddress a) {
        if (a.isAnyLocalAddress() || a.isLoopbackAddress() || a.isLinkLocalAddress()
                || a.isSiteLocalAddress() || a.isMulticastAddress()) return false;
        byte[] b = a.getAddress();
        if (b.length == 4 && (b[0] & 0xFF) == 100 && (b[1] & 0xC0) == 64) return false; // CGNAT
        return !(b.length == 16 && (b[0] & 0xFE) == 0xFC); // fc00::/7
    }

    /** What a browser would send: the page's origin, and nothing when going from https to http. */
    static String refererFor(URL page, URL target) {
        if ("https".equals(page.getProtocol()) && !"https".equals(target.getProtocol())) return null;
        if (page.getHost().equalsIgnoreCase(target.getHost())) return page.toString().split("#")[0];
        return page.getProtocol() + "://" + page.getHost() + "/";
    }

    /** Same host or same last two labels (cdn.site.com for site.com), as the cookies would have matched. */
    static boolean sameSite(String pageHost, String imageHost) {
        if (pageHost == null || imageHost == null) return false;
        return registrable(pageHost).equalsIgnoreCase(registrable(imageHost));
    }

    private static String registrable(String host) {
        String[] parts = host.split("\\.");
        if (parts.length <= 2) return host;
        return parts[parts.length - 2] + "." + parts[parts.length - 1];
    }
}
