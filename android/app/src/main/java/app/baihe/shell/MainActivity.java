package app.baihe.shell;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.InputType;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;

/** Shows Baihe in a full-screen WebView and routes shared links to its Sources page. */
public class MainActivity extends Activity {
    private static final String PREFS = "baihe";
    private static final String ADDRESS = "address";

    private WebView web;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        // Stay inside the app for Baihe pages; sign-in and other sites still load in this view.
        web.setWebViewClient(new WebViewClient());
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
        String address = prefs().getString(ADDRESS, null);
        if (address == null) {
            askAddress(intent);
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
                    String a = input.getText().toString().trim();
                    if (!a.startsWith("http://") && !a.startsWith("https://")) {
                        askAddress(intent);
                        return;
                    }
                    prefs().edit().putString(ADDRESS, a).apply();
                    open(intent);
                })
                .show();
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }
}
