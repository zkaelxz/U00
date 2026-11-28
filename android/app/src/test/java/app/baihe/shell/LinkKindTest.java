package app.baihe.shell;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class LinkKindTest {
    @Test
    public void videoSitesAreVideo() {
        assertEquals(LinkKind.Kind.VIDEO, LinkKind.of("https://www.youtube.com/watch?v=abc"));
        assertEquals(LinkKind.Kind.VIDEO, LinkKind.of("https://youtu.be/abc"));
        assertEquals(LinkKind.Kind.VIDEO, LinkKind.of("https://m.bilibili.com/video/BV1"));
    }

    @Test
    public void comicSitesAreComicEvenUnderBilibili() {
        assertEquals(LinkKind.Kind.COMIC, LinkKind.of("https://manga.bilibili.com/mc123/456"));
        assertEquals(LinkKind.Kind.COMIC, LinkKind.of("https://rawkuma.net/manga/x/chapter-1/"));
    }

    @Test
    public void sitesThatChangeTheirNumberAreComicWhateverTheNumber() {
        assertEquals(LinkKind.Kind.COMIC, LinkKind.of("https://marumaru113.com/webtoon/1284"));
        assertEquals(LinkKind.Kind.COMIC, LinkKind.of("https://marumaru108.com/webtoon/1284"));
        assertEquals(LinkKind.Kind.COMIC, LinkKind.of("https://newxtoon5.com/webtoon/1"));
        assertEquals(LinkKind.Kind.COMIC, LinkKind.of("https://wfwf999.com/view?toon=1"));
        assertEquals(LinkKind.Kind.NONE, LinkKind.of("https://notmarumaru113.com/"));
        assertEquals(LinkKind.Kind.NONE, LinkKind.of("https://marumaru113.com.evil.example/"));
    }

    @Test
    public void anythingElseAndLookalikesAreNotOffered() {
        assertEquals(LinkKind.Kind.NONE, LinkKind.of("https://example.com/watch"));
        assertEquals(LinkKind.Kind.NONE, LinkKind.of("https://notyoutube.com/watch"));
        assertEquals(LinkKind.Kind.NONE, LinkKind.of("https://youtube.com.evil.example/"));
        assertEquals(LinkKind.Kind.NONE, LinkKind.of(null));
    }
}
