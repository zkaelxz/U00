package app.baihe.shell;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;
import android.os.Build;
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

import android.util.Base64;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
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
    private volatile boolean uploading;

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
                // Leaving the page ends a grab that is still reading it, or Grab would stay
                // disabled. A grab already uploading finishes on its own.
                if (grabbing && !uploading) finishGrab("");
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
        else handleShare(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleShare(intent);
    }

    /** What another app shared: a link opens in the browser, images go straight to Baihe. */
    private void handleShare(Intent intent) {
        if (intent == null) return;
        String action = intent.getAction();
        String type = intent.getType();
        if (!Intent.ACTION_SEND.equals(action) && !Intent.ACTION_SEND_MULTIPLE.equals(action)) return;
        if (SharedImages.isImageType(type)) {
            shareImages(intent);
        } else if (type != null && type.startsWith("text/")) {
            String link = ShareLink.firstLink(intent.getStringExtra(Intent.EXTRA_TEXT));
            String url = link == null ? null : ServerAddress.browsable(link);
            if (url == null) say("No web link found in what was shared.");
            else web.loadUrl(url);
        }
    }

    @SuppressWarnings("deprecation")
    private List<Uri> sharedUris(Intent intent) {
        List<Uri> uris = new ArrayList<>();
        if (Intent.ACTION_SEND_MULTIPLE.equals(intent.getAction())) {
            ArrayList<Uri> many = Build.VERSION.SDK_INT >= 33
                    ? intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri.class)
                    : intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM);
            if (many != null) uris.addAll(many);
        } else {
            Uri one = Build.VERSION.SDK_INT >= 33
                    ? intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri.class)
                    : intent.getParcelableExtra(Intent.EXTRA_STREAM);
            if (one != null) uris.add(one);
        }
        List<Uri> readable = new ArrayList<>();
        for (Uri uri : uris) {
            if (uri != null && SharedImages.isReadableScheme(uri.getScheme())) readable.add(uri);
        }
        return readable;
    }

    private void shareImages(Intent intent) {
        if (grabbing) {
            say("Wait for the current grab to finish.");
            return;
        }
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (server.isEmpty() || token.isEmpty()) {
            say("Set the server and token in Settings, then share again.");
            showSettings();
            return;
        }
        List<Uri> all = sharedUris(intent);
        if (all.isEmpty()) {
            say("Could not read the shared images.");
            return;
        }
        boolean cut = all.size() > SharedImages.MAX_IMAGES;
        List<Uri> uris = SharedImages.limit(all);
        String subject = intent.getStringExtra(Intent.EXTRA_SUBJECT);
        EditText name = new EditText(this);
        name.setHint("Title name");
        name.setSingleLine(true);
        if (subject != null) name.setText(subject.length() > 200 ? subject.substring(0, 200) : subject);
        new AlertDialog.Builder(this)
                .setTitle("Send to which title?")
                .setView(name)
                .setPositiveButton("Send", (d, w) -> {
                    String title = name.getText().toString().trim();
                    if (title.isEmpty()) {
                        say("Enter a title name.");
                        return;
                    }
                    sendShared(uris, title, cut, server, token);
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void sendShared(List<Uri> uris, String title, boolean cut, String server, String token) {
        grabbing = true;
        uploading = true;
        runOnUiThread(() -> grab.setEnabled(false));
        say("Reading shared images");
        sender.execute(() -> {
            List<PageImage> images = new ArrayList<>();
            long used = 0;
            int unreadable = 0;
            boolean truncated = cut;
            for (Uri uri : uris) {
                PageImage image = readShared(uri, images.size());
                if (image == null) {
                    unreadable++;
                } else if (!SharedImages.fitsTotal(used, image.base64.length())) {
                    truncated = true;
                    break;
                } else {
                    used += image.base64.length();
                    images.add(image);
                }
            }
            if (images.isEmpty()) {
                finishGrab("Could not read the shared images.");
                return;
            }
            upload(new BaiheClient(server, token), "", java.util.Collections.singletonList(title),
                    images, unreadable, truncated);
        });
    }

    /** One shared image as base64, or null when it is not an image or is too large to send. */
    private PageImage readShared(Uri uri, int index) {
        try {
            String type = getContentResolver().getType(uri);
            if (!SharedImages.isImageType(type)) return null;
            try (InputStream in = getContentResolver().openInputStream(uri)) {
                if (in == null) return null;
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buffer = new byte[8192];
                int n;
                while ((n = in.read(buffer)) > 0) {
                    if (out.size() + n > Batches.MAX_IMAGE_BYTES) return null;
                    out.write(buffer, 0, n);
                }
                String base64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP);
                return new PageImage("share-" + index, "", type, base64);
            }
        } catch (Exception e) {
            return null;
        }
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
                    if (origin == null) {
                        say("The server address must start with https://.");
                        return;
                    }
                    String candidate = ServerAddress.tokenToCheck(token.getText().toString(),
                            prefs().getString(KEY_TOKEN, ""), origin, prefs().getString(KEY_SERVER, ""));
                    if (candidate.isEmpty()) {
                        say("Enter the token.");
                        return;
                    }
                    verifyAndSave(origin, candidate);
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    /**
     * Nothing is saved until the server answers /health with this token, and the host it
     * answered from is shown, so a lookalike address is visible before the token is trusted.
     */
    private void verifyAndSave(String origin, String token) {
        sender.execute(() -> {
            try {
                new BaiheClient(origin, token).health();
                prefs().edit().putString(KEY_SERVER, origin).putString(KEY_TOKEN, token).apply();
                say("Connected to " + ServerAddress.hostOf(origin));
            } catch (BaiheClient.BridgeException e) {
                say(e.getMessage());
            }
        });
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
        pageUrl = web.getUrl() == null ? "" : web.getUrl();
        runningGrab = nonce;
        web.postDelayed(() -> {
            if (grabbing && !uploading && nonce.equals(runningGrab)) finishGrab("The page did not finish.");
        }, GRAB_TIMEOUT_MS);
        try (InputStream in = getAssets().open("grab.js")) {
            // InputStream.readAllBytes needs API 33; the app runs on API 26.
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            for (int n; (n = in.read(chunk)) > 0; ) out.write(chunk, 0, n);
            String script = new String(out.toByteArray(), StandardCharsets.UTF_8).replace("__NONCE__", nonce);
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
        uploading = false;
        bridge.cancel();
        runOnUiThread(() -> {
            grab.setEnabled(true);
            status.setText(message);
        });
    }

    private static final long GRAB_TIMEOUT_MS = 5 * 60 * 1000;
    private volatile String runningGrab = "";
    // From the web view, not the page: a page can claim any address.
    private volatile String pageUrl = "";
    private List<String> pageTitles = new ArrayList<>();

    @Override
    public void onBegin(List<String> titles, int count) {
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
        uploading = true;
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
