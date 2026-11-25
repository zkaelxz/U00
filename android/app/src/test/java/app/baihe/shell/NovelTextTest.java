package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.List;

public class NovelTextTest {
    @Test
    public void languageIsToldFromTheScript() {
        assertEquals("zh", NovelText.languageOf("他走进房间，看见桌上放着一封信。他犹豫了很久，最后还是打开了它，信上只有一句话。"));
        assertEquals("ja", NovelText.languageOf("彼は部屋に入って、机の上に手紙があるのを見つけた。しばらくためらってから、それを開いた。"));
        assertEquals("ko", NovelText.languageOf("그는 방에 들어가서 책상 위에 편지가 놓여 있는 것을 보았다. 한참 망설이다가 결국 열어 보았다."));
        assertEquals("", NovelText.languageOf("He walked into the room and saw a letter lying on the desk, and hesitated for a long time."));
        assertEquals("", NovelText.languageOf("短い"));
    }

    @Test
    public void aPagesLanguageComesFromItsTextThenItsLangAttribute() {
        // An English menu around a raw comic's own title and chapter list still names the comic.
        assertEquals("ja", NovelText.pageLanguage("en", "Home Latest Bookmarks\nサイコアイズ 第1話 突然の追放宣告"));
        assertEquals("ko", NovelText.pageLanguage("en", "Home Webtoon\n육아부터 하는 연애 1화 다음 화"));
        assertEquals("zh", NovelText.pageLanguage("", "首页 漫画 第十二话 上一话 下一话"));
        assertEquals("ko", NovelText.pageLanguage("ko-KR", "Chapter 3 Next Prev"));
        assertEquals("", NovelText.pageLanguage("en", "Chapter 3 Next Prev"));
        assertEquals("", NovelText.pageLanguage(null, null));
    }

    @Test
    public void partsKeepParagraphsTogetherInOrderAndStayShort() {
        String text = "一二三四五\n\n六七八九十\n十一十二十三\n  \n十四";
        List<String> parts = NovelText.split(text, 12);
        assertEquals("一二三四五\n六七八九十", parts.get(0));
        assertEquals("十一十二十三\n十四", parts.get(1));
        assertEquals(2, parts.size());
    }

    @Test
    public void aLongParagraphIsCutAfterASentenceEnd() {
        String sentence = "あいうえお。";
        String paragraph = sentence.repeat(10);
        List<String> parts = NovelText.split(paragraph, 20);
        assertTrue(parts.size() >= 3);
        for (String part : parts) assertTrue(part.length() <= 20);
        assertEquals(paragraph, String.join("", parts));
        assertTrue(parts.get(0).endsWith("。"));
    }

    @Test
    public void noTextGivesNoParts() {
        assertEquals(0, NovelText.split("\n \n", 100).size());
    }
}
