package app.baihe.shell;

import android.webkit.JavascriptInterface;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * The only object the browsed page's JavaScript can reach (as `Baihe`). It accepts images
 * only while a grab is running and only with that grab's secret (a page can read the
 * secret, so it is no more than a time window). Every method returns
 * nothing, and the token and server address are not reachable from here.
 */
final class GrabBridge {
    interface Listener {
        void onBegin(List<String> titles, int count, JSONArray chapters, String next);

        void onDone(List<PageImage> images, int unreadable, boolean truncated);

        /** The page held a novel chapter and no comic pages: its text, for a Quick read. */
        void onText(String text);

        /** The language the page's own text and lang attribute point to ("zh", "ja", "ko" or ""). */
        default void onLanguage(String language) {}

        /** The page asks for a strip of page {@code key} as the web view shows it: its place in page pixels. */
        default void onSnap(String id, String key, double left, double top, double width, double height, double viewWidth) {}

        /** The page has asked for the strips taken so far to be joined and filed under this key. */
        default void onStitch(String id, String key) {}
    }

    /** What the page said about its chapter and its work, kept for the PC's chapter label and blank title details. */
    static final class PageMeta {
        static final PageMeta NONE = new PageMeta(new ArrayList<>(), "", "", "");
        private static final java.util.regex.Pattern CHAPTER_NUMBER = java.util.regex.Pattern.compile(
                "第\\s*[\\d０-９一二三四五六七八九十百千零]+\\s*[章话話回集期]|(?<![A-Za-z])(?:chapter|chap|ch|episode|ep)\\.?\\s*\\d+|\\d+\\s*[화회話话章]",
                java.util.regex.Pattern.CASE_INSENSITIVE);
        final List<String> chapterTitles;
        final String author;
        final String summary;
        final String coverUrl;

        PageMeta(List<String> chapterTitles, String author, String summary, String coverUrl) {
            this.chapterTitles = chapterTitles;
            this.author = author;
            this.summary = summary;
            this.coverUrl = coverUrl;
        }

        static PageMeta from(JSONObject info) {
            List<String> titles = new ArrayList<>();
            JSONArray array = info.optJSONArray("chapter_titles");
            for (int i = 0; array != null && i < array.length() && i < 8; i++) {
                String title = array.optString(i, "");
                if (!title.isEmpty()) titles.add(cap(title, 300));
            }
            String cover = info.optString("cover", "");
            return new PageMeta(titles, cap(info.optString("author", ""), 200), cap(info.optString("summary", ""), 2000),
                    cover.startsWith("https://") && cover.length() <= 2048 ? cover : "");
        }

        /**
         * The first heading or title that names a chapter number, else `fallback`: some readers put
         * the book's name in the first heading and the chapter's in a later one.
         */
        String chapterHeading(String fallback) {
            for (String title : chapterTitles) {
                if (CHAPTER_NUMBER.matcher(title).find()) return title;
            }
            return fallback;
        }

        private static String cap(String text, int max) {
            return text.length() > max ? text.substring(0, max) : text;
        }
    }

    /** Past these the grab stops collecting and sends what it has. */
    static final int MAX_IMAGES = 300;
    static final int MAX_TEXT_CHARS = 400_000;
    static final long MAX_TOTAL_BASE64_CHARS = 24L * 1024 * 1024;

    private final Listener listener;
    private String nonce;
    private List<PageImage> images = new ArrayList<>();
    private long totalChars;
    private int unreadable;
    private boolean truncated;
    private boolean begun;
    private String novelText;
    private volatile PageMeta meta = PageMeta.NONE;

    GrabBridge(Listener listener) {
        this.listener = listener;
    }

    synchronized void start(String newNonce) {
        nonce = newNonce;
        images = new ArrayList<>();
        totalChars = 0;
        unreadable = 0;
        truncated = false;
        begun = false;
        novelText = null;
        meta = PageMeta.NONE;
    }

    /** The open grab's page details; NONE until the page has begun. */
    PageMeta meta() {
        return meta;
    }

    synchronized void cancel() {
        nonce = null;
    }

    private boolean accepts(String given) {
        return nonce != null && given != null && nonce.equals(given);
    }

    @JavascriptInterface
    public void begin(String given, String json) {
        synchronized (this) {
            if (!accepts(given)) return;
            begun = true;
        }
        try {
            JSONObject info = new JSONObject(json);
            List<String> titles = new ArrayList<>();
            JSONArray array = info.optJSONArray("titles");
            for (int i = 0; array != null && i < array.length() && i < 12; i++) {
                String title = array.optString(i, "");
                if (!title.isEmpty()) titles.add(title.length() > 300 ? title.substring(0, 300) : title);
            }
            meta = PageMeta.from(info);
            JSONArray chapters = info.optJSONArray("chapters");
            String sample = info.optString("sample", "");
            listener.onLanguage(NovelText.pageLanguage(info.optString("lang", ""),
                    sample.length() > 8000 ? sample.substring(0, 8000) : sample));
            listener.onBegin(titles, info.optInt("count", 0),
                    chapters == null ? new JSONArray() : chapters, info.optString("next", ""));
        } catch (Exception e) {
            synchronized (this) {
                nonce = null;
            }
        }
    }

    @JavascriptInterface
    public synchronized void image(String given, String key, String url, String type, String base64) {
        if (!accepts(given) || !begun || base64 == null || base64.isEmpty()) return;
        if (key == null || key.length() > 64) {
            unreadable++;
            return;
        }
        if (Batches.tooLarge(base64)) {
            unreadable++;
            return;
        }
        if (images.size() >= MAX_IMAGES || totalChars + base64.length() > MAX_TOTAL_BASE64_CHARS) {
            truncated = true;
            return;
        }
        String safeType = type != null && type.matches("image/[A-Za-z0-9.+-]{1,20}") ? type : "image/jpeg";
        images.add(new PageImage(key, url == null ? "" : url, safeType, base64));
        totalChars += base64.length();
    }

    /**
     * A picture the page would not let the script read. Only its address is kept, in page order
     * (an empty body marks it); the app downloads it itself with the page's cookies.
     */
    @JavascriptInterface
    public synchronized void pending(String given, String key, String url) {
        if (!accepts(given) || !begun || url == null) return;
        if (key == null || key.length() > 64) {
            unreadable++;
            return;
        }
        if (!(url.startsWith("https://") || url.startsWith("http://")) || url.length() > 2000) {
            unreadable++;
            return;
        }
        if (images.size() >= MAX_IMAGES) {
            truncated = true;
            return;
        }
        images.add(new PageImage(key, url, "image/jpeg", ""));
    }

    /** A chapter's prose from a page with no comic pictures; kept only up to a generous chapter's length. */
    @JavascriptInterface
    public synchronized void text(String given, String body) {
        if (!accepts(given) || !begun || body == null || body.trim().isEmpty()) return;
        novelText = body.length() > MAX_TEXT_CHARS ? body.substring(0, MAX_TEXT_CHARS) : body;
    }

    /** Only while this grab runs, and only a short id the page itself made up. */
    @JavascriptInterface
    public void snap(String given, String id, String key, double left, double top, double width, double height, double viewWidth) {
        synchronized (this) {
            if (!accepts(given) || !begun) return;
        }
        if (id == null || !id.matches("s[0-9]{1,6}") || key == null || !key.matches("p[0-9]{1,5}")) return;
        if (!(viewWidth >= 1 && viewWidth <= 10000 && width >= 1 && width <= 10000
                && height >= 1 && height <= 20000 && left > -10000 && left < 10000 && top > -10000 && top < 20000)) return;
        listener.onSnap(id, key, left, top, width, height, viewWidth);
    }

    @JavascriptInterface
    public void stitch(String given, String id, String key) {
        synchronized (this) {
            if (!accepts(given) || !begun) return;
        }
        if (id == null || !id.matches("s[0-9]{1,6}") || key == null || !key.matches("p[0-9]{1,5}")) return;
        listener.onStitch(id, key);
    }

    @JavascriptInterface
    public synchronized void skipped(String given, String key) {
        if (accepts(given)) unreadable++;
    }

    @JavascriptInterface
    public void done(String given) {
        List<PageImage> collected;
        int bad;
        boolean cut;
        String prose;
        synchronized (this) {
            if (!accepts(given) || !begun) return;
            nonce = null;
            prose = novelText;
            novelText = null;
            collected = images;
            bad = unreadable;
            cut = truncated;
            images = new ArrayList<>();
        }
        if (prose != null && collected.isEmpty()) listener.onText(prose);
        else listener.onDone(collected, bad, cut);
    }
}
