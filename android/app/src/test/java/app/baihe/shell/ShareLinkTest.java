package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;

import org.junit.Test;

public class ShareLinkTest {
    @Test
    public void quickReadLinkGivesTheEncodedPage() {
        assertEquals("https://site.example/c/1?a=b",
                ShareLink.quickReadLink("baihegrab://quickread?url=https%3A%2F%2Fsite.example%2Fc%2F1%3Fa%3Db"));
    }

    @Test
    public void quickReadLinkRefusesAnythingButAWebPage() {
        assertNull(ShareLink.quickReadLink("baihegrab://open"));
        assertNull(ShareLink.quickReadLink("baihegrab://quickread?url=file%3A%2F%2F%2Fdata%2Fx"));
        assertNull(ShareLink.quickReadLink("baihegrab://quickread?url=%ZZ"));
        assertNull(ShareLink.quickReadLink(null));
    }
}
