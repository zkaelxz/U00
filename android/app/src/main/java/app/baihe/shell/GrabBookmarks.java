package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** The sites and chapters saved in Grab's bookmark list, newest first, kept as one line of text per bookmark. */
final class GrabBookmarks {
    private GrabBookmarks() {}

    static final int MAX = 200;

    static final class Mark {
        final String name;
        final String url;

        Mark(String name, String url) {
            this.name = name;
            this.url = url;
        }
    }

    /** Tab and line breaks are the separators, so a title may not carry them. */
    private static String flat(String text) {
        return text == null ? "" : text.replaceAll("[\\t\\r\\n]+", " ").trim();
    }

    /** The title, or the host when a page has none, short enough for one line of the list. */
    private static String nameFor(String title, String url) {
        String name = flat(title);
        if (name.isEmpty()) {
            try {
                String host = new java.net.URI(url).getHost();
                name = host == null ? "Page" : host;
            } catch (java.net.URISyntaxException e) {
                name = "Page";
            }
        }
        return name.length() > 60 ? name.substring(0, 60) : name;
    }

    static List<Mark> parse(String saved) {
        List<Mark> out = new ArrayList<>();
        if (saved == null) return out;
        for (String line : saved.split("\n")) {
            int tab = line.indexOf('\t');
            if (tab < 0) continue;
            String url = ServerAddress.browsable(line.substring(tab + 1));
            if (url != null) out.add(new Mark(nameFor(line.substring(0, tab), url), url));
            if (out.size() >= MAX) break;
        }
        return out;
    }

    static String serialize(List<Mark> marks) {
        StringBuilder out = new StringBuilder();
        for (Mark mark : marks) out.append(flat(mark.name)).append('\t').append(flat(mark.url)).append('\n');
        return out.toString();
    }

    static boolean has(List<Mark> marks, String url) {
        for (Mark mark : marks) if (mark.url.equals(url)) return true;
        return false;
    }

    static List<Mark> with(List<Mark> marks, String name, String url) {
        return with(marks, name, url, MAX);
    }

    /** The list with this page first; an older entry for the same address is replaced, the oldest drops off past `limit`. */
    static List<Mark> with(List<Mark> marks, String name, String url, int limit) {
        String link = ServerAddress.browsable(url);
        if (link == null) return marks;
        List<Mark> out = new ArrayList<>();
        out.add(new Mark(nameFor(flat(name), link), link));
        for (Mark mark : marks) if (!mark.url.equals(link) && out.size() < limit) out.add(mark);
        return out;
    }

    static List<Mark> without(List<Mark> marks, String url) {
        List<Mark> out = new ArrayList<>();
        for (Mark mark : marks) if (!mark.url.equals(url)) out.add(mark);
        return out;
    }

    /** True for a site's front page, so the list can tell a site from a chapter. */
    static boolean isSite(String url) {
        try {
            String path = new java.net.URI(url).getRawPath();
            return path == null || path.isEmpty() || path.equals("/");
        } catch (java.net.URISyntaxException e) {
            return false;
        }
    }
}
