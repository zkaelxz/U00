package app.baihe.shell;

/** How the English lettering on a redrawn Quick read page should look: its smallest size, an outline, and the typeface. */
final class TextStyle {
    static final String[] SIZES = {"Fit the bubble (default)", "A little larger", "Larger", "Largest"};
    static final String[] FONTS = {"Normal", "Comic lettering"};
    // The PC treats this as the smallest size it will shrink the text to, so a bubble that cannot hold it gets tight.
    private static final int[] MIN_FONT = {0, 16, 22, 30};

    final int size;
    final boolean outline;
    final int font;

    TextStyle(int size, boolean outline, int font) {
        this.size = Math.max(0, Math.min(size, MIN_FONT.length - 1));
        this.outline = outline;
        this.font = Math.max(0, Math.min(font, FONTS.length - 1));
    }

    boolean isDefault() {
        return size == 0 && !outline && font == 0;
    }

    int minFont() {
        return MIN_FONT[size];
    }

    /** The name the PC knows the typeface by. */
    String fontName() {
        return font == 1 ? "bangers" : "default";
    }
}
