package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class SaveDownloadTest {
    @Test
    public void aNameKeepsNoFolderPartOrControlCharacters() {
        assertEquals("_.._evil.srt", SaveDownload.cleanName("/../evil.srt"));
        assertEquals("a_b_c.txt", SaveDownload.cleanName("a\\b\nc.txt"));
        assertEquals("hidden.epub", SaveDownload.cleanName("..hidden.epub"));
        assertEquals("baihe-download", SaveDownload.cleanName(null));
        assertEquals("baihe-download", SaveDownload.cleanName("  "));
    }

    @Test
    public void aLongNameKeepsItsExtension() {
        StringBuilder long_ = new StringBuilder();
        for (int i = 0; i < 300; i++) long_.append('x');
        String clean = SaveDownload.cleanName(long_ + ".epub");
        assertEquals(120, clean.length());
        assertTrue(clean.endsWith(".epub"));
    }
}
