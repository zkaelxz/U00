package app.baihe.shell;

import android.graphics.BitmapFactory;
import android.util.Base64;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Leaves out pictures that are clearly not comic pages (thumbnails, banners) before a Quick read
 * upload: on a slow link each one costs real time, and the PC would drop them anyway.
 */
final class PageFilter {
    /** Fewer pictures than this give no reliable idea of what a page looks like. */
    static final int MIN_SAMPLE = 5;

    private PageFilter() {}

    /**
     * Which pictures to keep, given each one's size (0 when unknown, which is always kept). A
     * picture goes only when it is far smaller than the typical page in both area and width, so
     * wide spreads and pages near the typical size are never dropped.
     */
    static boolean[] keep(int[] widths, int[] heights) {
        int n = widths.length;
        boolean[] keep = new boolean[n];
        Arrays.fill(keep, true);
        List<Long> areas = new ArrayList<>();
        List<Integer> sides = new ArrayList<>();
        for (int i = 0; i < n; i++) {
            if (widths[i] > 0 && heights[i] > 0) {
                areas.add((long) widths[i] * heights[i]);
                sides.add(widths[i]);
            }
        }
        if (areas.size() < MIN_SAMPLE) return keep;
        java.util.Collections.sort(areas);
        java.util.Collections.sort(sides);
        long medianArea = areas.get(areas.size() / 2);
        int medianWidth = sides.get(sides.size() / 2);
        for (int i = 0; i < n; i++) {
            if (widths[i] <= 0 || heights[i] <= 0) continue;
            boolean smallArea = (long) widths[i] * heights[i] < medianArea / 4;
            boolean narrow = widths[i] < medianWidth * 0.6;
            if (smallArea && narrow) keep[i] = false;
        }
        return keep;
    }

    static List<PageImage> pagesOnly(List<PageImage> images) {
        int[] widths = new int[images.size()];
        int[] heights = new int[images.size()];
        for (int i = 0; i < images.size(); i++) {
            String data = images.get(i).base64;
            if (data.isEmpty()) continue;
            try {
                byte[] raw = Base64.decode(data, Base64.DEFAULT);
                BitmapFactory.Options bounds = new BitmapFactory.Options();
                bounds.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(raw, 0, raw.length, bounds);
                widths[i] = Math.max(0, bounds.outWidth);
                heights[i] = Math.max(0, bounds.outHeight);
            } catch (RuntimeException e) {
                // Unknown size: kept.
            }
        }
        boolean[] keep = keep(widths, heights);
        List<PageImage> out = new ArrayList<>();
        for (int i = 0; i < images.size(); i++) if (keep[i]) out.add(images.get(i));
        return out;
    }
}
