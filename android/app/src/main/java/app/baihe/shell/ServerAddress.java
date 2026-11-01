package app.baihe.shell;

import java.net.URI;
import java.util.Locale;

/** Which addresses the app will talk to: the saved Baihe server and the pages it browses. */
final class ServerAddress {
    private ServerAddress() {}

    /**
     * The Baihe server as an https origin, or null. https only, because the device token
     * rides every request; a path, query or login in the text is dropped so the token can
     * only ever be sent to that one host.
     */
    static String normalizeServer(String text) {
        String raw = text == null ? "" : text.trim();
        if (raw.isEmpty()) return null;
        if (!raw.contains("://")) raw = "https://" + raw;
        try {
            URI uri = new URI(raw);
            if (!"https".equalsIgnoreCase(uri.getScheme())) return null;
            if (uri.getRawUserInfo() != null || uri.getHost() == null) return null;
            String origin = "https://" + uri.getHost().toLowerCase(Locale.ROOT);
            return uri.getPort() > 0 ? origin + ":" + uri.getPort() : origin;
        } catch (Exception e) {
            return null;
        }
    }

    /** What the address bar loads: http(s) only (a bare name gets https://), else null. */
    static String browsable(String text) {
        String raw = text == null ? "" : text.trim();
        if (raw.isEmpty() || raw.matches(".*\\s.*")) return null;
        if (!raw.contains("://")) raw = "https://" + raw;
        try {
            URI uri = new URI(raw);
            String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
            if (!scheme.equals("http") && !scheme.equals("https")) return null;
            return uri.getHost() == null ? null : raw;
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * The page address that may be kept as a title's source link: public https only. An
     * address on this phone's network or an IP literal is left out, as the browser
     * extension does for its pages.
     */
    static String publicSourceUrl(String url) {
        try {
            URI uri = new URI(url == null ? "" : url);
            String host = uri.getHost();
            if (!"https".equalsIgnoreCase(uri.getScheme()) || host == null) return "";
            String h = host.toLowerCase(Locale.ROOT);
            if (!h.contains(".") || h.endsWith(".local") || h.endsWith(".lan") || h.endsWith(".internal")
                    || h.matches("[0-9.]+") || h.contains(":")) {
                return "";
            }
            return new URI("https", null, h, uri.getPort(), uri.getRawPath(), null, null).toString();
        } catch (Exception e) {
            return "";
        }
    }
}
