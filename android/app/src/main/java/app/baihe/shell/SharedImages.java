package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** Which shared items from another app Baihe Grab accepts as page images, and how many. */
final class SharedImages {
    /** A share sheet can hand over a whole gallery; a chapter is far smaller than this. */
    static final int MAX_IMAGES = 100;
    /**
     * Same ceiling as a grab: the phone holds every image's base64 at once before it is
     * batched, so the total, not just one image, has to stay bounded.
     */
    static final long MAX_TOTAL_BASE64_CHARS = 24L * 1024 * 1024;

    private SharedImages() {}

    static boolean isImageType(String mime) {
        return mime != null && mime.toLowerCase(java.util.Locale.ROOT).startsWith("image/");
    }

    /** Only content:// is read. A file:// or other scheme from a sender could point at this app's own files. */
    static boolean isReadableScheme(String scheme) {
        return "content".equalsIgnoreCase(scheme);
    }

    /** The first MAX_IMAGES entries; the caller reports that some were left out. */
    static <T> List<T> limit(List<T> items) {
        return new ArrayList<>(items.subList(0, Math.min(items.size(), MAX_IMAGES)));
    }

    static boolean fitsTotal(long usedChars, long nextChars) {
        return usedChars + nextChars <= MAX_TOTAL_BASE64_CHARS;
    }
}
