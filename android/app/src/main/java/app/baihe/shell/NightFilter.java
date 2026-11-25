package app.baihe.shell;

/** What Quick read's brightness slider and warm night filter turn into: a window brightness and a tint colour. */
final class NightFilter {
    /** Below this the screen is unreadable on most phones, so the slider cannot reach it. */
    static final float MIN_BRIGHTNESS = 0.05f;
    static final int MAX_WARM_ALPHA = 0x66;

    private NightFilter() {}

    /** The slider's 0 to 100 as a window brightness; 100 hands control back to the system. */
    static float windowBrightness(int percent) {
        if (percent >= 100) return -1f;
        return Math.max(MIN_BRIGHTNESS, Math.min(1f, percent / 100f));
    }

    /** An amber layer over the page cuts blue light; none when the filter is off. */
    static int tint(boolean warm, int strengthPercent) {
        if (!warm) return 0;
        int alpha = Math.round(MAX_WARM_ALPHA * Math.max(0, Math.min(100, strengthPercent)) / 100f);
        return (alpha << 24) | 0xFF8C1A;
    }
}
