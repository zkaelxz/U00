package app.baihe.shell;

/** Hosts of well-known ad networks that Grab's browser refuses to load. */
final class AdHosts {
    // The phone's own DNS (a Pi-hole at home included) only applies on that network and with
    // Private DNS off, so Grab blocks the common ad networks itself. Only dedicated ad domains
    // are listed, since a wrong entry would break the page being grabbed.
    private static final String[] BLOCKED = {
        "doubleclick.net", "googlesyndication.com", "googleadservices.com", "adservice.google.com",
        "adnxs.com", "adsrvr.org", "adform.net", "amazon-adsystem.com", "criteo.com", "criteo.net",
        "taboola.com", "outbrain.com", "revcontent.com", "mgid.com", "popads.net", "popcash.net",
        "propellerads.com", "exoclick.com", "exosrv.com", "juicyads.com", "trafficjunky.net",
        "adsterra.com", "pubmatic.com", "rubiconproject.com", "openx.net", "casalemedia.com",
        "smartadserver.com", "moatads.com", "adcolony.com", "applovin.com", "unityads.unity3d.com",
        "yandexadexchange.net", "ad.admitad.com", "hilltopads.net", "clickadu.com", "adskeeper.com",
    };

    /** Capped so a pasted wall of text cannot slow every request down. */
    static final int MAX_EXTRA = 200;

    // Read on the WebView's request threads, written from the main thread.
    private static volatile java.util.List<String> extra = java.util.Collections.emptyList();

    private AdHosts() {}

    /** The person's own additions, one host per line; addresses pasted whole are cut down to their host. */
    static java.util.List<String> parseExtra(String text) {
        java.util.List<String> out = new java.util.ArrayList<>();
        if (text == null) return out;
        for (String line : text.split("[\\s,;]+")) {
            String h = line.trim().toLowerCase(java.util.Locale.ROOT).replaceFirst("^[a-z]+://", "");
            int end = h.indexOf('/');
            if (end >= 0) h = h.substring(0, end);
            h = h.replaceFirst("^www\\.", "");
            // Needs a dot and only host characters: a stray word would otherwise block a whole site by accident.
            if (h.matches("[a-z0-9-]+(\\.[a-z0-9-]+)+") && !out.contains(h) && out.size() < MAX_EXTRA) out.add(h);
        }
        return out;
    }

    static void setExtra(String text) {
        extra = parseExtra(text);
    }

    static boolean blocks(String host) {
        if (host == null) return false;
        String h = host.toLowerCase(java.util.Locale.ROOT);
        for (String bad : BLOCKED) {
            if (h.equals(bad) || h.endsWith("." + bad)) return true;
        }
        for (String bad : extra) {
            if (h.equals(bad) || h.endsWith("." + bad)) return true;
        }
        return false;
    }
}
