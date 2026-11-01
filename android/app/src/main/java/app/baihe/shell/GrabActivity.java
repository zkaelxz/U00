package app.baihe.shell;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.text.InputType;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Browse a comic site, press Grab, and the chapter's page images go to Baihe through the
 * household server's device-token bridge (/api/ext-bridge), not the signed-in web session:
 * Google sign-in does not work inside an embedded web view, and a token can be revoked.
 */
public class GrabActivity extends Activity implements GrabBridge.Listener {
    private static final String PREFS = "grab";
    private static final String KEY_SERVER = "server";
    private static final String KEY_TOKEN = "token";

    private WebView web;
    private EditText address;
    private TextView status;
    private Button grab;
    private GrabBridge bridge;
    private final ExecutorService sender = Executors.newSingleThreadExecutor();
    private final SecureRandom random = new SecureRandom();
    private volatile boolean grabbing;

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        bridge = new GrabBridge(this);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);

        address = new EditText(this);
        address.setSingleLine(true);
        address.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        address.setImeOptions(EditorInfo.IME_ACTION_GO);
        address.setOnEditorActionListener((v, actionId, event) -> {
            go();
            return true;
        });
        root.addView(address, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setSafeBrowsingEnabled(true);
        web.addJavascriptInterface(bridge, "Baihe");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                return !"https".equals(scheme) && !"http".equals(scheme);
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                address.setText(url);
            }
        });
        root.addView(web, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        status = new TextView(this);
        root.addView(status, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        grab = button("Grab", v -> startGrab());
        buttons.addView(grab, weighted());
        buttons.addView(button("Open Baihe", v -> openBaihe()), weighted());
        buttons.addView(button("Settings", v -> showSettings()), weighted());
        root.addView(buttons);

        setContentView(root);
        root.setFitsSystemWindows(true);
        if (state != null) web.restoreState(state);
    }

    private Button button(String label, android.view.View.OnClickListener click) {
        Button b = new Button(this);
        b.setText(label);
        b.setOnClickListener(click);
        return b;
    }

    private LinearLayout.LayoutParams weighted() {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onDestroy() {
        bridge.cancel();
        sender.shutdownNow();
        web.destroy();
        super.onDestroy();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }

    private void go() {
        String url = ServerAddress.browsable(address.getText().toString());
        if (url != null) web.loadUrl(url);
    }

    private void say(String text) {
        runOnUiThread(() -> status.setText(text));
    }

    private void openBaihe() {
        startActivity(new Intent(this, MainActivity.class));
    }

    private void showSettings() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        form.setPadding(pad, pad, pad, 0);
        EditText server = new EditText(this);
        server.setHint("Server address");
        server.setSingleLine(true);
        server.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        server.setText(prefs().getString(KEY_SERVER, ""));
        EditText token = new EditText(this);
        // The saved token is never shown again; an empty field keeps the one already saved.
        token.setHint("Token");
        token.setSingleLine(true);
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        form.addView(server);
        form.addView(token);
        new AlertDialog.Builder(this)
                .setTitle("Settings")
                .setView(form)
                .setPositiveButton("Save", (d, w) -> {
                    String origin = ServerAddress.normalizeServer(server.getText().toString());
                    String newToken = token.getText().toString().trim();
                    if (origin == null) {
                        say("The server address must start with https://.");
                        return;
                    }
                    SharedPreferences.Editor edit = prefs().edit().putString(KEY_SERVER, origin);
                    if (!newToken.isEmpty()) edit.putString(KEY_TOKEN, newToken);
                    edit.apply();
                    say("");
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void startGrab() {
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (server.isEmpty() || token.isEmpty()) {
            showSettings();
            return;
        }
        if (grabbing) return;
        grabbing = true;
        grab.setEnabled(false);
        say("Reading pages");
        String nonce = newNonce();
        bridge.start(nonce);
        try (InputStream in = getAssets().open("grab.js")) {
            String script = new String(in.readAllBytes(), StandardCharsets.UTF_8).replace("__NONCE__", nonce);
            web.evaluateJavascript(script, null);
        } catch (Exception e) {
            finishGrab("Could not start the grab.");
        }
    }

    private String newNonce() {
        byte[] bytes = new byte[16];
        random.nextBytes(bytes);
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format("%02x", b));
        return out.toString();
    }

    private void finishGrab(String message) {
        grabbing = false;
        runOnUiThread(() -> {
            grab.setEnabled(true);
            status.setText(message);
        });
    }

    private String pageUrl = "";
    private List<String> pageTitles = new ArrayList<>();

    @Override
    public void onBegin(String url, List<String> titles, int count) {
        pageUrl = url;
        pageTitles = titles;
        if (count == 0) say("Reading pages");
    }

    @Override
    public void onDone(List<PageImage> images, int unreadable, boolean truncated) {
        if (images.isEmpty()) {
            finishGrab("No page images were found on this page.");
            return;
        }
        String url = pageUrl;
        List<String> titles = pageTitles;
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        sender.execute(() -> upload(new BaiheClient(server, token), url, titles, images, unreadable, truncated));
    }

    private void upload(BaiheClient client, String url, List<String> titles, List<PageImage> images,
                        int unreadable, boolean truncated) {
        try {
            int max = client.health().optInt("max_images_per_request", 12);
            JSONObject title = client.resolveTitle(url, titles);
            long dramaId = title.getLong("drama_id");
            List<List<PageImage>> batches = Batches.plan(images, max);
            int stored = 0;
            int failed = 0;
            String firstError = "";
            for (int i = 0; i < batches.size(); i++) {
                say("Sending " + (i + 1) + " of " + batches.size());
                try {
                    JSONObject answer = client.sendPages(dramaId, url, batches.get(i));
                    stored += answer.optInt("stored", 0) + answer.optInt("already_stored", 0);
                    failed += answer.optJSONArray("failed") == null ? 0 : answer.optJSONArray("failed").length();
                } catch (BaiheClient.BridgeException e) {
                    failed += batches.get(i).size();
                    if (firstError.isEmpty()) firstError = e.getMessage();
                    // A token the server refused would be refused again by every later batch.
                    if (e.status == 401 || e.status == 403) break;
                }
            }
            failed += unreadable;
            StringBuilder out = new StringBuilder("Sent " + stored + " pages to " + title.optString("title", "Baihe"));
            if (failed > 0 || truncated) out.append(". Not sent: ").append(failed + (truncated ? " or more" : ""));
            if (!firstError.isEmpty()) out.append(". ").append(firstError);
            finishGrab(out.toString());
        } catch (BaiheClient.BridgeException e) {
            finishGrab(e.getMessage());
        } catch (Exception e) {
            finishGrab("Could not send the pages.");
        }
    }
}
