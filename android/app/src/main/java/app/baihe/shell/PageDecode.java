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

    /** A decoded picture goes into a row only while the row still shows that page: the list recycles rows. */
    static boolean rowStillShows(Object rowTag, String key) {
        return key.equals(rowTag);
    }
}
