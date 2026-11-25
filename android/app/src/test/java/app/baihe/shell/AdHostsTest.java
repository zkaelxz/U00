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
}
