package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** Splits a chapter into requests the bridge accepts. */
final class Batches {
    private Batches() {}

    /** The bridge refuses an image over this many decoded bytes (page_server.MAX_IMAGE_BYTES). */
    static final long MAX_IMAGE_BYTES = 12L * 1024 * 1024;
    /** Stays well under the bridge's 64 MB request cap, base64 and JSON framing included. */
    static final long MAX_BATCH_BASE64_CHARS = 40L * 1024 * 1024;

    static boolean tooLarge(String base64) {
        return base64.length() * 3L / 4 > MAX_IMAGE_BYTES;
    }

    /**
     * Keeps the page order. A batch closes at maxImages (from the server's health answer) or
     * when the next image would push it past the byte budget; a single image is never split.
     */
    static List<List<PageImage>> plan(List<PageImage> images, int maxImages) {
        int cap = Math.max(1, maxImages);
        List<List<PageImage>> out = new ArrayList<>();
        List<PageImage> current = new ArrayList<>();
        long size = 0;
        for (PageImage image : images) {
            long chars = image.base64.length();
            if (!current.isEmpty() && (current.size() >= cap || size + chars > MAX_BATCH_BASE64_CHARS)) {
                out.add(current);
                current = new ArrayList<>();
                size = 0;
            }
            current.add(image);
            size += chars;
        }
        if (!current.isEmpty()) out.add(current);
        return out;
    }
}
