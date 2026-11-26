package app.baihe.shell;

/** Sites Grab opens as a desktop page, because their phone reader is one canvas that cannot be walked. */
final class DesktopSite {
    // Without this agent the site sends phones to a reader that holds a single canvas and turns
    // pages only for a real finger; the desktop reader keeps every page mounted and scrolls by script.
    static final String AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36";

    private DesktopSite() {}

    /** The address to load as a desktop page, or null when the site is not one of these. */
    static String rewrite(String url) {
        if (url == null) return null;
        java.util.regex.Matcher m = java.util.regex.Pattern
                .compile("^(https?://manga\\.bilibili\\.com)/m(/.*)$", java.util.regex.Pattern.CASE_INSENSITIVE).matcher(url);
        if (m.matches()) return m.group(1) + m.group(2);
        return url.matches("(?i)^https?://manga\\.bilibili\\.com([/?#].*)?$") ? url : null;
    }
}
