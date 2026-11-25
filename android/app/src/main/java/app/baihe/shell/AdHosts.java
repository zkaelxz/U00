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

    private AdHosts() {}

    static boolean blocks(String host) {
        if (host == null) return false;
        String h = host.toLowerCase(java.util.Locale.ROOT);
        for (String bad : BLOCKED) {
            if (h.equals(bad) || h.endsWith("." + bad)) return true;
        }
        return false;
    }
}
