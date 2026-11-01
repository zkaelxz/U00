package app.baihe.shell;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.webkit.WebResourceRequest;
import android.os.Bundle;
import android.text.InputType;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

import java.security.SecureRandom;

/** Shows Baihe in a full-screen WebView and routes shared links to its Sources page. */
public class MainActivity extends Activity {
    private static final String PREFS = "baihe";
    private static final String ADDRESS = "address";
    private static final String SIGN_IN_VERIFIER = "sign_in_verifier";
    private static final String SIGN_IN_STARTED = "sign_in_started";

    private WebView web;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        // Only Baihe's own origin loads here. Google blocks sign-in inside an embedded WebView and
        // credentials must not be typed into a view this app controls, so every other
        // address (shared links, sign-in redirects) opens in the system browser.
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String url = request.getUrl().toString();
                String address = savedAddress();
                if (address != null && AppSignIn.isLoginStart(address, url)) {
                    startSignIn(address);
                    return true;
                }
                if (address != null && ServerAddress.sameOrigin(address, url)) return false;
                String scheme = request.getUrl().getScheme();
                if ("http".equals(scheme) || "https".equals(scheme)) {
                    try {
                        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                    } catch (RuntimeException e) {
                        // No browser installed: nothing safe to do but stay on the current page.
                    }
                }
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient());
        setContentView(web);
        open(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        open(intent);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    private void open(Intent intent) {
        String address = savedAddress();
        if (address == null) {
            askAddress(intent);
            return;
        }
        if (Intent.ACTION_VIEW.equals(intent.getAction()) && intent.getData() != null) {
            finishSignIn(address, intent.getDataString());
            return;
        }
        String link = Intent.ACTION_SEND.equals(intent.getAction())
                ? ShareLink.firstLink(intent.getStringExtra(Intent.EXTRA_TEXT))
                : null;
        web.loadUrl(link == null ? address : ShareLink.pageFor(address, link));
    }

    private void askAddress(Intent intent) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://");
        new AlertDialog.Builder(this)
                .setTitle("Baihe address")
                .setView(input)
                .setCancelable(false)
                .setPositiveButton("OK", (d, w) -> {
                    String a = ServerAddress.shellAddress(input.getText().toString());
                    if (a == null) {
                        askAddress(intent);
                        return;
                    }
                    prefs().edit().putString(ADDRESS, a).apply();
                    open(intent);
                })
                .show();
    }

    /**
     * The saved address, checked again: one saved by an older build may predate the
     * http-only-on-private-hosts rule, so an address that no longer passes is forgotten
     * and asked for again.
     */
    private String savedAddress() {
        String saved = prefs().getString(ADDRESS, null);
        if (saved == null) return null;
        String checked = ServerAddress.shellAddress(saved);
        if (checked == null) {
            prefs().edit().remove(ADDRESS).apply();
        } else if (!checked.equals(saved)) {
            prefs().edit().putString(ADDRESS, checked).apply();
        }
        return checked;
    }

    private void startSignIn(String address) {
        byte[] random = new byte[32];
        new SecureRandom().nextBytes(random);
        String verifier = AppSignIn.verifier(random);
        prefs().edit()
                .putString(SIGN_IN_VERIFIER, verifier)
                .putLong(SIGN_IN_STARTED, System.currentTimeMillis())
                .apply();
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(AppSignIn.startUrl(address, verifier))));
        } catch (RuntimeException e) {
            // No browser installed: sign-in can't happen, so stay on the current page.
        }
    }

    /**
     * Swaps the code on Chrome's link back for this app's own session. The verifier is
     * spent whatever happens, and a link that arrives when no sign-in is waiting just
     * opens Baihe, so a link from anywhere else can't sign the app in.
     */
    private void finishSignIn(String address, String link) {
        SharedPreferences p = prefs();
        String verifier = AppSignIn.pendingVerifier(
                p.getString(SIGN_IN_VERIFIER, null), p.getLong(SIGN_IN_STARTED, 0),
                System.currentTimeMillis());
        p.edit().remove(SIGN_IN_VERIFIER).remove(SIGN_IN_STARTED).apply();
        String code = AppSignIn.codeFrom(link);
        if (verifier == null || code == null) {
            web.loadUrl(address);
            return;
        }
        web.postUrl(AppSignIn.redeemUrl(address), AppSignIn.redeemBody(code, verifier));
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }
}
