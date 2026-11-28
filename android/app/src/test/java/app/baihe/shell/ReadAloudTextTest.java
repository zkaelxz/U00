package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.ArrayList;
import java.util.List;

public class ReadAloudTextTest {
    private static List<String> said(String text) {
        return said(text, 0, ReadAloudText.MAX_CHARS);
    }

    private static List<String> said(String text, int from, int max) {
        List<String> out = new ArrayList<>();
        for (ReadAloudText.Sentence s : ReadAloudText.sentences(text, from, max)) {
            out.add(text.substring(s.start, s.end));
        }
        return out;
    }

    @Test
    public void sentencesEndAtPeriodsQuestionAndExclamationMarks() {
        assertEquals(List.of("He opened the door.", "Was anyone there?", "No!", "Only wind"),
                said("He opened the door. Was anyone there? No! Only wind"));
    }

    @Test
    public void offsetsPointIntoTheTextShownWithSpacesLeftOut() {
        String text = "  First one.   Second one.  ";
        List<ReadAloudText.Sentence> got = ReadAloudText.sentences(text, 0, 100);
        assertEquals(2, got.size());
        assertEquals(2, got.get(0).start);
        assertEquals(12, got.get(0).end);
        assertEquals(15, got.get(1).start);
        assertEquals(26, got.get(1).end);
    }

    @Test
    public void closingQuotesStayWithTheirSentence() {
        assertEquals(List.of("\"Run!\"", "She ran.", "“Why?”", "‘Because.’"),
                said("\"Run!\" She ran. “Why?” ‘Because.’"));
    }

    @Test
    public void aLowercaseWordAfterTheMarkKeepsTheSentenceGoing() {
        assertEquals(List.of("\"Stop!\" she said, and left."), said("\"Stop!\" she said, and left."));
        assertEquals(List.of("Well... maybe not.", "Then...", "Nothing."), said("Well... maybe not. Then... Nothing."));
        assertEquals(List.of("Wait…", "He turned."), said("Wait… He turned."));
    }

    @Test
    public void titlesInitialsAndNumbersDoNotEndASentence() {
        assertEquals(List.of("Mr. Lin met Dr. Example at 3.5 km.", "J. R. Example waved."),
                said("Mr. Lin met Dr. Example at 3.5 km. J. R. Example waved."));
        assertEquals(List.of("Bring tools, e.g. A hammer."), said("Bring tools, e.g. A hammer."));
    }

    @Test
    public void aLineBreakAlwaysStartsANewSentence() {
        String text = "A title with no stop\n\nThe first line of the chapter. A second\none.\r\nLast";
        assertEquals(List.of("A title with no stop", "The first line of the chapter.", "A second", "one.", "Last"),
                said(text));
    }

    @Test
    public void untranslatedChineseMarksStillCut() {
        assertEquals(List.of("你好。", "再见！"), said("你好。再见！"));
    }

    @Test
    public void readingFromAnOffsetPicksUpTextAddedLater() {
        String before = "Part one ends here.";
        String after = before + "\n\nPart two begins. And goes on.";
        List<ReadAloudText.Sentence> first = ReadAloudText.sentences(before, 0, 100);
        int end = first.get(first.size() - 1).end;
        assertEquals(List.of("Part two begins.", "And goes on."), said(after, end, 100));
        // The sentences already read keep their places when a part is added after them.
        assertEquals(first, ReadAloudText.sentences(after, 0, 100).subList(0, first.size()));
    }

    @Test
    public void aRunOnSentenceIsCutIntoPiecesNoLongerThanTheLimit() {
        StringBuilder text = new StringBuilder();
        for (int i = 0; i < 60; i++) text.append("and then another clause, ");
        text.append("done.");
        String s = text.toString();
        List<ReadAloudText.Sentence> got = ReadAloudText.sentences(s, 0, 200);
        assertTrue(got.size() > 1);
        int at = 0;
        for (ReadAloudText.Sentence piece : got) {
            assertTrue(piece.end - piece.start <= 200);
            assertTrue(piece.start >= at);
            assertTrue(s.substring(piece.start, piece.end).trim().equals(s.substring(piece.start, piece.end)));
            at = piece.end;
        }
        assertTrue(s.substring(got.get(0).start, got.get(0).end).endsWith(","));
        assertEquals(s.length(), got.get(got.size() - 1).end);
    }

    @Test
    public void aRunWithNoSpacesIsStillCutAtTheLimit() {
        String s = "x".repeat(450);
        List<ReadAloudText.Sentence> got = ReadAloudText.sentences(s, 0, 200);
        assertEquals(3, got.size());
        assertEquals(200, got.get(0).end);
        assertEquals(450, got.get(2).end);
    }

    @Test
    public void noTextGivesNoSentences() {
        assertEquals(0, ReadAloudText.sentences("", 0, 100).size());
        assertEquals(0, ReadAloudText.sentences(" \n\n ", 0, 100).size());
        assertEquals(0, ReadAloudText.sentences("Done.", 5, 100).size());
    }
}
