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

    /**
     * The Baihe address the shell may keep: https, or http only for a host on a private
     * network (this phone's LAN, Tailscale, localhost), because cleartext is allowed
     * app-wide and must not reach a public host. A path, query or login is dropped.
     */
    static String shellAddress(String text) {
        String raw = text == null ? "" : text.trim();
        if (raw.isEmpty() || raw.matches(".*\\s.*")) return null;
        if (!raw.contains("://")) raw = "https://" + raw;
        try {
            URI uri = new URI(raw);
            String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
            if (uri.getRawUserInfo() != null || uri.getHost() == null) return null;
            if (!scheme.equals("https") && !(scheme.equals("http") && isPrivateHost(uri.getHost()))) return null;
            String origin = scheme + "://" + uri.getHost().toLowerCase(Locale.ROOT);
            return uri.getPort() > 0 ? origin + ":" + uri.getPort() : origin;
        } catch (Exception e) {
            return null;
        }
    }

    /** True for localhost, a .local/.lan name, or an IPv4 literal in a private range. */
    static boolean isPrivateHost(String host) {
        String h = host == null ? "" : host.toLowerCase(Locale.ROOT);
        if (h.equals("localhost") || h.endsWith(".local") || h.endsWith(".lan")) return true;
        String[] p = h.split("\\.");
        if (p.length != 4) return false;
        int[] n = new int[4];
        for (int i = 0; i < 4; i++) {
            if (!p[i].matches("\\d{1,3}")) return false;
            n[i] = Integer.parseInt(p[i]);
            if (n[i] > 255) return false;
        }
        return n[0] == 10 || n[0] == 127
                || (n[0] == 192 && n[1] == 168)
                || (n[0] == 172 && n[1] >= 16 && n[1] <= 31)
                || (n[0] == 100 && n[1] >= 64 && n[1] <= 127);
    }

    /** True when `url` is on the same scheme, host and port as the saved Baihe address. */
    static boolean sameOrigin(String address, String url) {
        try {
            URI a = new URI(address);
            URI u = new URI(url);
            return a.getScheme() != null && a.getScheme().equalsIgnoreCase(u.getScheme())
                    && a.getHost() != null && a.getHost().equalsIgnoreCase(u.getHost())
                    && a.getPort() == u.getPort();
        } catch (Exception e) {
            return false;
        }
    }

    /** The host (and port) to show for a normalized origin. */
    static String hostOf(String origin) {
        return origin.substring("https://".length());
    }

    /**
     * The token to test for a Save: the typed one, or the saved one when the server is
     * unchanged. A saved token is never carried over to a different server.
     */
    static String tokenToCheck(String typed, String saved, String newOrigin, String savedOrigin) {
        String token = typed == null ? "" : typed.trim();
        if (!token.isEmpty()) return token;
        return newOrigin != null && newOrigin.equals(savedOrigin) && saved != null ? saved : "";
    }
}
