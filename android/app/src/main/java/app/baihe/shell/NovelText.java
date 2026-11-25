package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** Prepares a novel chapter's text for translation: which language it is and how it is cut into parts. */
final class NovelText {
    private NovelText() {}

    /** Parts stay well under the PC's limit per request so the first part reads quickly. */
    static final int PART_CHARS = 3000;

    /**
     * "zh", "ja" or "ko" for text that is mostly Chinese, Japanese or Korean, and "" for anything else
     * (already English, or too little text to tell). Kana means Japanese and Hangul means Korean,
     * since Japanese text is also full of Chinese characters.
     */
    static String languageOf(String text) {
        int han = 0, kana = 0, hangul = 0, letters = 0;
        for (int i = 0; i < text.length(); ) {
            int c = text.codePointAt(i);
            i += Character.charCount(c);
            if (!Character.isLetter(c)) continue;
            letters++;
            if (c >= 0x3040 && c <= 0x30FF) kana++;
            else if ((c >= 0xAC00 && c <= 0xD7AF) || (c >= 0x1100 && c <= 0x11FF)) hangul++;
            else if ((c >= 0x4E00 && c <= 0x9FFF) || (c >= 0x3400 && c <= 0x4DBF)) han++;
        }
        if (letters < 20 || (han + kana + hangul) * 5 < letters) return "";
        if (hangul > han && hangul > kana) return "ko";
        if (kana > 0 && kana * 20 >= han + kana) return "ja";
        return "zh";
    }

    /** The text cut at paragraph breaks into parts of at most {@code max} characters, in order. */
    static List<String> split(String text, int max) {
        List<String> parts = new ArrayList<>();
        StringBuilder part = new StringBuilder();
        for (String paragraph : text.split("\n")) {
            String line = paragraph.trim();
            if (line.isEmpty()) continue;
            while (line.length() > max) {
                int cut = cutPoint(line, max);
                flush(parts, part);
                parts.add(line.substring(0, cut));
                line = line.substring(cut).trim();
            }
            if (part.length() > 0 && part.length() + 1 + line.length() > max) flush(parts, part);
            if (part.length() > 0) part.append('\n');
            part.append(line);
        }
        flush(parts, part);
        return parts;
    }

    private static void flush(List<String> parts, StringBuilder part) {
        if (part.length() > 0) parts.add(part.toString());
        part.setLength(0);
    }

    /** After the last sentence end in the first {@code max} characters, or at {@code max} when there is none. */
    private static int cutPoint(String line, int max) {
        for (int i = max; i > max / 2; i--) {
            char c = line.charAt(i - 1);
            if (c == '。' || c == '！' || c == '？' || c == '.' || c == '!' || c == '?' || c == '」' || c == '”') return i;
        }
        return max;
    }
}
