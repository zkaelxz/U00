package app.baihe.shell;

/** The Quick read reader's zoom arithmetic, kept free of Android classes so it is unit-tested. */
final class QuickReadZoom {
    static final float MIN = 1f;
    static final float MAX = 4f;
    static final float DOUBLE_TAP = 2f;

    private QuickReadZoom() {}

    static float clampScale(float scale) {
        return Math.max(MIN, Math.min(MAX, scale));
    }

    /** The sideways offset limited so neither page edge can be dragged inside the screen. */
    static float clampPan(float pan, float scale, float width) {
        return Math.max(-(scale - 1f) * width, Math.min(0f, pan));
    }

    /** The offset that keeps the point under the fingers (focusX) in place while the scale changes. */
    static float panAfterZoom(float pan, float oldScale, float newScale, float focusX, float width) {
        float pagePoint = (focusX - pan) / oldScale;
        return clampPan(focusX - pagePoint * newScale, newScale, width);
    }

    /** A scaled list draws taller than it is laid out, so it is laid out shorter to end at the screen's bottom edge. */
    static int listHeight(int screenHeight, float scale) {
        return (int) Math.ceil(screenHeight / scale);
    }

    static float afterDoubleTap(float scale) {
        return scale > MIN ? MIN : DOUBLE_TAP;
    }
}
