package app.baihe.shell;

/** Tells a page picture that has not been drawn yet from a real one, by how little its pixels differ. */
final class StripCheck {
    /** A picture whose samples differ by less than this is taken for a page still being drawn. */
    private static final int BLANK_RANGE = 10;

    private StripCheck() {}

    /** True when the sampled brightness values (0 to 255) are all but identical; no samples is not blank. */
    static boolean looksBlank(int[] samples) {
        if (samples == null || samples.length == 0) return false;
        int low = 255;
        int high = 0;
        for (int sample : samples) {
            low = Math.min(low, sample);
            high = Math.max(high, sample);
        }
        return high - low < BLANK_RANGE;
    }
}
