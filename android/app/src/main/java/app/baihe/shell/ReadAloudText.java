package app.baihe.shell;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Cuts the novel reader's English text into the sentences it reads aloud, each with where it sits in that text. */
final class ReadAloudText {
    private ReadAloudText() {}

    /** Under TextToSpeech's 4000-character limit per utterance, which some engines enforce by refusing the whole utterance. */
    static final int MAX_CHARS = 3900;

    /** A period after one of these is part of a name ("Mr. Lin"), not the end of a sentence. */
    private static final Set<String> TITLES = new HashSet<>(Arrays.asList(
            "mr", "mrs", "ms", "dr", "prof", "st", "mt", "jr", "sr", "capt", "lt", "col", "gen", "sgt", "rev",
            "hon", "vs", "etc", "e.g", "i.e"));

    private static final String ENDS = ".!?…。！？";
    private static final String CLOSERS = "\"'”’)]»」』";

    static final class Sentence {
        final int start, end;

        Sentence(int start, int end) {
            this.start = start;
            this.end = end;
        }

        @Override
        public boolean equals(Object o) {
            return o instanceof Sentence && ((Sentence) o).start == start && ((Sentence) o).end == end;
        }

        @Override
        public int hashCode() {
            return start * 31 + end;
        }

        @Override
        public String toString() {
            return "[" + start + ", " + end + ")";
        }
    }

    /**
     * The sentences of {@code text} from offset {@code from} on, in order, with surrounding spaces left out.
     * None crosses a line break, so a paragraph always starts a new sentence, and none is longer than
     * {@code max} characters.
     */
    static List<Sentence> sentences(String text, int from, int max) {
        List<Sentence> out = new ArrayList<>();
        int n = text.length();
        int i = Math.max(0, from);
        while (true) {
            while (i < n && Character.isWhitespace(text.charAt(i))) i++;
            if (i >= n) return out;
            int start = i;
            int end = -1;
            while (i < n) {
                char c = text.charAt(i);
                if (c == '\n' || c == '\r') {
                    end = i;
                    break;
                }
                if (ENDS.indexOf(c) >= 0) {
                    int k = i + 1;
                    while (k < n && ENDS.indexOf(text.charAt(k)) >= 0) k++;
                    while (k < n && CLOSERS.indexOf(text.charAt(k)) >= 0) k++;
                    if (endsSentence(text, start, i, k)) {
                        end = k;
                        i = k;
                        break;
                    }
                    i = k;
                    continue;
                }
                i++;
            }
            if (end < 0) end = n;
            while (end > start && Character.isWhitespace(text.charAt(end - 1))) end--;
            // A run-on longer than one utterance is read in pieces, cut where the voice would pause anyway.
            while (end - start > max) {
                int cut = cutPoint(text, start, max);
                out.add(new Sentence(start, trimEnd(text, start, cut)));
                start = cut;
                while (start < end && Character.isWhitespace(text.charAt(start))) start++;
            }
            if (end > start) out.add(new Sentence(start, end));
            i = Math.max(i, end);
        }
    }

    /**
     * Whether the terminator at {@code at} (with the closing quotes after it, up to {@code after}) ends a sentence:
     * not inside a word ("3.5", "U.S"), not after a title or an initial, and not when a lowercase word follows
     * ("Wait... no", "\"Stop!\" she said").
     */
    private static boolean endsSentence(String text, int start, int at, int after) {
        int n = text.length();
        if (after >= n) return true;
        char next = text.charAt(after);
        char c = text.charAt(at);
        if (c == '。' || c == '！' || c == '？') return true;
        if (!Character.isWhitespace(next)) return false;
        int k = after;
        while (k < n && (text.charAt(k) == ' ' || text.charAt(k) == '\t')) k++;
        if (k >= n || text.charAt(k) == '\n' || text.charAt(k) == '\r') return true;
        char following = text.charAt(k);
        if (Character.isLowerCase(following)) return false;
        // Only a lone period can belong to an abbreviation; "...", "?!" and a period before a quote never do.
        if (c == '.' && after == at + 1) {
            String word = wordBefore(text, start, at);
            if (TITLES.contains(word.toLowerCase(Locale.ROOT))) return false;
            if (word.length() == 1 && Character.isUpperCase(word.charAt(0))) return false;
        }
        return true;
    }

    /** The word just before {@code at}, keeping inner periods so "e.g" stays one word. */
    private static String wordBefore(String text, int start, int at) {
        int k = at;
        while (k > start && (Character.isLetter(text.charAt(k - 1)) || text.charAt(k - 1) == '.')) k--;
        return text.substring(k, at);
    }

    /** Where to cut a piece of at most {@code max} characters starting at {@code start}. */
    private static int cutPoint(String text, int start, int max) {
        int limit = start + max;
        for (int k = limit; k > start + max / 2; k--) {
            char c = text.charAt(k - 1);
            if ((c == ',' || c == ';' || c == ':' || c == '—') && Character.isWhitespace(text.charAt(k))) return k;
        }
        for (int k = limit; k > start + max / 2; k--) {
            if (Character.isWhitespace(text.charAt(k))) return k;
        }
        // Never split a surrogate pair, which a voice would read as noise.
        if (Character.isHighSurrogate(text.charAt(limit - 1))) return limit - 1;
        return limit;
    }

    private static int trimEnd(String text, int start, int end) {
        while (end > start && Character.isWhitespace(text.charAt(end - 1))) end--;
        return end;
    }
}
