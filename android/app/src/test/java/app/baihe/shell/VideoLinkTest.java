package app.baihe.shell;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class VideoLinkTest {
    @Test
    public void keepsTheQueryWhereTheVideoIdLives() {
        assertEquals("https://www.youtube.com/watch?v=abc123",
                ServerAddress.videoLink("https://www.youtube.com/watch?v=abc123#t=5"));
    }

    @Test
    public void dropsALogin() {
        assertEquals("https://b23.tv/x?p=2", ServerAddress.videoLink("https://me:pw@b23.tv/x?p=2"));
    }

    @Test
    public void refusesPrivateAndPlainHttpAddresses() {
        assertEquals("", ServerAddress.videoLink("http://youtube.com/watch?v=a"));
        assertEquals("", ServerAddress.videoLink("https://192.168.1.5/v?id=1"));
        assertEquals("", ServerAddress.videoLink("https://nas.local/v?id=1"));
        assertEquals("", ServerAddress.videoLink(null));
    }
}
