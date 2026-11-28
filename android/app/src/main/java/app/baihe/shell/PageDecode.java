package app.baihe.shell;

/** The rules for decoding Quick read pages off the touch thread: cache keys, sample size and a waiting row's height. */
final class PageDecode {
    private PageDecode() {}

    /**
     * The cache key of one picture. The generation moves when a Redo rewrites the file, so a decode of the old
     * picture that finishes late is never shown; the original art has a path of its own.
     */
    static String key(String path, int generation) {
        return path + "#" + generation;
    }

    /** Halves the decode while the page is still at least the screen's width at half size. */
    static int sampleSize(int pageWidth, int screenWidth) {
        int sample = 1;
        if (screenWidth <= 0) return sample;
        while (pageWidth / (sample * 2) >= screenWidth) sample *= 2;
        return sample;
    }

    /** A row waiting for its picture takes the page's own shape at the row's width, so nothing jumps when it lands. */
    static int placeholderHeight(int rowWidth, int pageWidth, int pageHeight, int fallback) {
        if (rowWidth <= 0 || pageWidth <= 0 || pageHeight <= 0) return fallback;
        return (int) Math.round((double) rowWidth * pageHeight / pageWidth);
    }

    /** How long a page file that could not be read is left alone before it is tried again (a file still being written). */
    static final long RETRY_AFTER_MS = 3000;

    /** Whether a read that failed at failedAt (null: never failed) may be tried at now; a bind must not retry it each time. */
    static boolean mayTry(Long failedAt, long now) {
        return failedAt == null || now - failedAt >= RETRY_AFTER_MS;
    }

    /**
     * The picture cache's size in bytes: a quarter of the app's heap. A tall strip decodes to 20-35 MB, so a cache
     * held to a count of pictures could outgrow a small phone's heap.
     */
    static int cacheBytes(long maxHeapBytes) {
        return (int) Math.min(Integer.MAX_VALUE, Math.max(1, maxHeapBytes / 4));
    }

    /** A decoded picture goes into a row only while the row still shows that page: the list recycles rows. */
    static boolean rowStillShows(Object rowTag, String key) {
        return key.equals(rowTag);
    }

    /** A screen reader reads a description in one go; text past this is still in the page's own text. */
    static final int DESCRIPTION_MAX = 4000;

    /** "Page N: " and the page's English in reading order (the source where a box has none), as the web reader's alt does. */
    static String description(int number, org.json.JSONArray regions) {
        if (regions == null) return "Page " + number;
        java.util.TreeMap<Integer, String> byIdx = new java.util.TreeMap<>();
        for (int i = 0; i < regions.length(); i++) {
            org.json.JSONObject r = regions.optJSONObject(i);
            if (r == null) continue;
            String text = r.optString("translated_text", "").trim();
            if (text.isEmpty()) text = r.optString("source_text", "").trim();
            if (!text.isEmpty()) byIdx.put(r.optInt("idx", i), text);
        }
        if (byIdx.isEmpty()) return "Page " + number + ", no text";
        String all = "Page " + number + ": " + String.join(" ", byIdx.values());
        return all.length() > DESCRIPTION_MAX ? all.substring(0, DESCRIPTION_MAX - 1).trim() + "\u2026" : all;
    }
}
