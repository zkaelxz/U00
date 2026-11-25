package app.baihe.shell;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class AdHostsTest {
    @Test
    public void blocksAnAdNetworkAndItsSubdomains() {
        assertTrue(AdHosts.blocks("doubleclick.net"));
        assertTrue(AdHosts.blocks("ad.DoubleClick.net"));
    }

    @Test
    public void leavesOtherSitesAndLookalikesAlone() {
        assertFalse(AdHosts.blocks("rawkuma.com"));
        assertFalse(AdHosts.blocks("notdoubleclick.net"));
        assertFalse(AdHosts.blocks("doubleclick.net.example.com"));
        assertFalse(AdHosts.blocks(null));
    }

    @Test
    public void ownAdditionsBlockTheHostAndItsSubdomains() {
        AdHosts.setExtra("https://www.Ads.Example.com/banner.js, tracker.example.org\nnotahost");
        try {
            assertTrue(AdHosts.blocks("ads.example.com"));
            assertTrue(AdHosts.blocks("x.tracker.example.org"));
            assertFalse(AdHosts.blocks("example.com"));
            assertFalse(AdHosts.blocks("notahost"));
        } finally {
            AdHosts.setExtra("");
        }
    }
}
