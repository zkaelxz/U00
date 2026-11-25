package app.baihe.shell;

/** Which lettering box of a Quick read page a tap landed on, and what language its source text is in. */
final class BubbleHit {
    private BubbleHit() {}

    /**
     * The box {x, y, w, h} (pixels of the picture the PC read) holding the tap, given as fractions of
     * the shown page. The redrawn picture is the same page scaled evenly, so fractions carry over.
     * With boxes inside boxes the smallest wins; -1 when the tap is on no lettering.
     */
    static int at(int[][] boxes, int pageWidth, int pageHeight, float fx, float fy) {
        if (pageWidth <= 0 || pageHeight <= 0 || fx < 0 || fy < 0 || fx > 1 || fy > 1) return -1;
        float x = fx * pageWidth;
        float y = fy * pageHeight;
        int best = -1;
        long bestArea = Long.MAX_VALUE;
        for (int i = 0; i < boxes.length; i++) {
            int[] b = boxes[i];
            if (b[2] <= 0 || b[3] <= 0 || x < b[0] || x > b[0] + b[2] || y < b[1] || y > b[1] + b[3]) continue;
            long area = (long) b[2] * b[3];
            if (area < bestArea) {
                best = i;
                bestArea = area;
            }
        }
        return best;
    }

    /** zh, ja or ko by the script of a short text (a bubble is too short for the novel detector); "" for none of them. */
    static String languageOf(String text) {
        boolean han = false;
        for (int i = 0; text != null && i < text.length(); ) {
            int c = text.codePointAt(i);
            i += Character.charCount(c);
            if (c >= 0x3040 && c <= 0x30FF) return "ja";
            if ((c >= 0xAC00 && c <= 0xD7AF) || (c >= 0x1100 && c <= 0x11FF)) return "ko";
            if ((c >= 0x4E00 && c <= 0x9FFF) || (c >= 0x3400 && c <= 0x4DBF)) han = true;
        }
        return han ? "zh" : "";
    }
}
