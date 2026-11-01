package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.nio.charset.StandardCharsets;

public class AppSignInTest {
    private static final String ADDRESS = "https://baihe.example.com";

    @Test
    public void challengeMatchesTheServersS256() {
        // The value services/oidc_service.app_challenge_for gives for this verifier.
        assertEquals("6AcuZnCVD39AAxIxgxUgpguBR4nEkmQRzg9Nzq499As",
                AppSignIn.challenge("dBjftJeZ4CVP-mJ92K9qkd0ER1z7y9zf6vU8_OoM7OU"));
        assertEquals(43, AppSignIn.verifier(new byte[32]).length());
    }

    @Test
    public void onlyTheLoginOnThisBaiheStartsTheHandOff() {
        assertTrue(AppSignIn.isLoginStart(ADDRESS, ADDRESS + "/api/auth/login?return_to=%2F"));
        assertFalse(AppSignIn.isLoginStart(ADDRESS, ADDRESS + "/api/auth/logout"));
        assertFalse(AppSignIn.isLoginStart(ADDRESS, ADDRESS + "/library"));
        assertFalse(AppSignIn.isLoginStart(ADDRESS, "https://evil.example/api/auth/login"));
        assertFalse(AppSignIn.isLoginStart(ADDRESS, "http://baihe.example.com/api/auth/login"));
    }

    @Test
    public void startUrlCarriesOnlyTheChallenge() {
        String verifier = AppSignIn.verifier(new byte[32]);
        String url = AppSignIn.startUrl(ADDRESS, verifier);
        assertEquals(ADDRESS + "/api/auth/app/start?challenge=" + AppSignIn.challenge(verifier), url);
        assertFalse(url.contains(verifier));
    }

    @Test
    public void codeComesOnlyFromTheSignedInLink() {
        assertEquals("abc-_9", AppSignIn.codeFrom("baihe-shell://signed-in?code=abc-_9"));
        assertEquals("a b", AppSignIn.codeFrom("baihe-shell://signed-in?x=1&code=a%20b"));
        assertNull(AppSignIn.codeFrom("baihe-shell://other?code=abc"));
        assertNull(AppSignIn.codeFrom("https://signed-in/?code=abc"));
        assertNull(AppSignIn.codeFrom("baihe-shell://signed-in?code="));
        assertNull(AppSignIn.codeFrom("baihe-shell://signed-in"));
        assertNull(AppSignIn.codeFrom(null));
    }

    @Test
    public void pendingVerifierExpires() {
        assertEquals("v", AppSignIn.pendingVerifier("v", 1000, 1000 + AppSignIn.PENDING_MS - 1));
        assertNull(AppSignIn.pendingVerifier("v", 1000, 1000 + AppSignIn.PENDING_MS));
        assertNull(AppSignIn.pendingVerifier("v", 1000, 999));
        assertNull(AppSignIn.pendingVerifier(null, 1000, 1000));
    }

    @Test
    public void redeemBodyIsAFormWithBothFields() {
        assertEquals("code=a%2Bb&verifier=v_-1",
                new String(AppSignIn.redeemBody("a+b", "v_-1"), StandardCharsets.US_ASCII));
        assertEquals(ADDRESS + "/api/auth/app/redeem", AppSignIn.redeemUrl(ADDRESS));
    }
}
