package app.baihe.shell;

import java.io.UnsupportedEncodingException;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;

/**
 * Signing the shell in through Chrome: Google refuses sign-in in a web view, so the
 * shell keeps a random verifier, Chrome signs in and hands back a one-time code on
 * the baihe-shell:// link, and the shell swaps code plus verifier for its own session.
 * A code caught by another app on that link is useless without the verifier.
 */
final class AppSignIn {
    private AppSignIn() {}

    static final String LINK_SCHEME = "baihe-shell";
    static final String LINK_HOST = "signed-in";
    /** Long enough for a Google sign-in in Chrome; the server's code itself lives 60 seconds. */
    static final long PENDING_MS = 15 * 60 * 1000;
    private static final String LOGIN_PATH = "/api/auth/login";

    /** 32 random bytes as unpadded base64url: 43 characters, inside RFC 7636's limits. */
    static String verifier(byte[] random) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(random);
    }

    static String challenge(String verifier) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(verifier.getBytes(StandardCharsets.US_ASCII));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** True when the web app is starting a Google sign-in on the shell's own Baihe. */
    static boolean isLoginStart(String address, String url) {
        if (!ServerAddress.sameOrigin(address, url)) return false;
        try {
            return LOGIN_PATH.equals(new URI(url).getRawPath());
        } catch (Exception e) {
            return false;
        }
    }

    /** What Chrome opens: Baihe's confirm page for this verifier. */
    static String startUrl(String address, String verifier) {
        return address + "/api/auth/app/start?challenge=" + challenge(verifier);
    }

    /** The code from a baihe-shell://signed-in link, or null for any other link. */
    static String codeFrom(String link) {
        try {
            URI uri = new URI(link == null ? "" : link);
            if (!LINK_SCHEME.equalsIgnoreCase(uri.getScheme()) || !LINK_HOST.equals(uri.getHost())) {
                return null;
            }
            String query = uri.getRawQuery();
            if (query == null) return null;
            for (String pair : query.split("&")) {
                if (pair.startsWith("code=")) {
                    String code = URLDecoder.decode(pair.substring(5), "UTF-8");
                    return code.isEmpty() || code.length() > 200 ? null : code;
                }
            }
            return null;
        } catch (Exception e) {
            return null;
        }
    }

    /** The verifier saved at `startedAt`, if it is still waiting at `now`. */
    static String pendingVerifier(String saved, long startedAt, long now) {
        if (saved == null || saved.isEmpty()) return null;
        return now >= startedAt && now - startedAt < PENDING_MS ? saved : null;
    }

    /** The form the shell posts to /api/auth/app/redeem; the verifier never goes in a URL. */
    static byte[] redeemBody(String code, String verifier) {
        try {
            return ("code=" + URLEncoder.encode(code, "UTF-8")
                    + "&verifier=" + URLEncoder.encode(verifier, "UTF-8"))
                    .getBytes(StandardCharsets.US_ASCII);
        } catch (UnsupportedEncodingException e) {
            throw new IllegalStateException(e);
        }
    }

    static String redeemUrl(String address) {
        return address + "/api/auth/app/redeem";
    }
}
