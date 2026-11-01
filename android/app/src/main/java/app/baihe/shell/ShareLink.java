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

    /** Baihe address plus the Sources page with the link filled in. */
    static String pageFor(String address, String link) {
        String base = address.replaceAll("/+$", "");
        return base + "/#/sources?url=" + Uri.encode(link);
    }
}
