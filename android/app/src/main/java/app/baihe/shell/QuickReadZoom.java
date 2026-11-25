package app.baihe.shell;

/** The Quick read reader's zoom arithmetic, kept free of Android classes so it is unit-tested. */
final class QuickReadZoom {
    /** The page fitted to the screen's width. */
    static final float FIT = 1f;
    /** Zoomed out far enough to see about two screens of pages at once. */
    static final float MIN = 0.5f;
    static final float MAX = 4f;
    static final float DOUBLE_TAP = 2f;

    private QuickReadZoom() {}

    static float clampScale(float scale) {
        return Math.max(MIN, Math.min(MAX, scale));
    }

    /**
     * The sideways offset limited so neither page edge can be dragged inside the screen. A page
     * smaller than the screen has nowhere to be dragged to, so it sits in the middle.
     */
    static float clampPan(float pan, float scale, float width) {
        if (scale <= FIT) return (FIT - scale) * width / 2f;
        return Math.max(-(scale - 1f) * width, Math.min(0f, pan));
    }

    /** The offset that keeps the point under the fingers (focusX) in place while the scale changes. */
    static float panAfterZoom(float pan, float oldScale, float newScale, float focusX, float width) {
        float pagePoint = (focusX - pan) / oldScale;
        return clampPan(focusX - pagePoint * newScale, newScale, width);
    }

    /** How far to scroll the list (in its own, unscaled pixels) so the point under the fingers (focusY) stays put. */
    static int scrollAfterZoom(float focusY, float oldScale, float newScale) {
        return Math.round(focusY / oldScale - focusY / newScale);
    }

    /** A scaled list draws taller than it is laid out, so it is laid out shorter to end at the screen's bottom edge. */
    static int listHeight(int screenHeight, float scale) {
        return (int) Math.ceil(screenHeight / scale);
    }

    static float afterDoubleTap(float scale) {
        return scale != FIT ? FIT : DOUBLE_TAP;
    }
}
