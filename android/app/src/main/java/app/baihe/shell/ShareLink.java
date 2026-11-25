package app.baihe.shell;

import android.net.Uri;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Turns shared text into the Baihe page that receives a link. */
final class ShareLink {
    private static final Pattern URL = Pattern.compile("https?://\\S+");
    // Same cap as the web app's router, so an oversized share never reaches the page.
    private static final int MAX_LINK = 2000;

    private ShareLink() {}

    /** First http(s) link in the shared text, or null. */
    static String firstLink(String shared) {
        if (shared == null) return null;
        Matcher m = URL.matcher(shared);
        if (!m.find()) return null;
        String link = m.group();
        return link.length() <= MAX_LINK ? link : null;
    }

    /** Baihe address plus the share picker with the link filled in. */
    static String pageFor(String address, String link) {
        String base = address.replaceAll("/+$", "");
        return base + "/#/share?url=" + Uri.encode(link);
    }

    /**
     * The page named by "baihegrab://quickread?url=...", or null for any other link. It must be an
     * ordinary web page: the share picker is Baihe's own page, but the link inside came from another app.
     */
    static String quickReadLink(String grabLink) {
        if (grabLink == null || !grabLink.startsWith("baihegrab://quickread?url=")) return null;
        try {
            String raw = java.net.URLDecoder.decode(grabLink.substring("baihegrab://quickread?url=".length()), "UTF-8");
            if (raw.length() > MAX_LINK) return null;
            String link = firstLink(raw);
            return link == null ? null : ServerAddress.browsable(link);
        } catch (java.io.UnsupportedEncodingException | IllegalArgumentException e) {
            return null;
        }
    }
}
