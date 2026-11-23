package app.baihe.shell;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.drawable.GradientDrawable;
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
import android.widget.ImageView;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
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
    private GrabUi.Chip chip;
    private GrabUi.BarButton grab;
    private GrabBridge bridge;
    private final ExecutorService sender = Executors.newSingleThreadExecutor();
    private final SecureRandom random = new SecureRandom();
    private GrabUi.BarButton chapters;
    private GrabUi.BarButton quick;
    private GrabUi.Palette palette;
    // This grab is a Quick read: the pages come back typeset and nothing is saved.
    private volatile boolean quickRead;
    private volatile boolean grabbing;
    // Chapters still to follow after the current one; 0 when not following.
    private volatile int chaptersLeft;
    private volatile boolean loadingNext;
    private volatile boolean uploading;

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        bridge = new GrabBridge(this);
        // Pages left by a Quick read that was cut off (the app was killed) are not kept.
        QuickReadActivity.clear(this);

        palette = GrabUi.Palette.of(this);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(palette.bg);
        getWindow().setStatusBarColor(palette.surface);
        getWindow().setNavigationBarColor(palette.surface);
        if (!palette.dark) {
            getWindow().getDecorView().setSystemUiVisibility(android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                    | android.view.View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        }

        address = new EditText(this);
        address.setSingleLine(true);
        address.setHint("Enter a comic site address");
        address.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        address.setImeOptions(EditorInfo.IME_ACTION_GO);
        address.setSelectAllOnFocus(true);
        address.setOnEditorActionListener((v, actionId, event) -> {
            go();
            return true;
        });
        address.setTextColor(palette.text);
        address.setHintTextColor(palette.muted);
        address.setTextSize(16);
        address.setBackground(GrabUi.box(this, palette.field, palette.fieldEdge, 8, 1));
        int inset = GrabUi.dp(this, 12);
        address.setPadding(inset, 0, inset, 0);
        ImageView go = new ImageView(this);
        go.setImageResource(R.drawable.ic_go);
        go.setImageTintList(android.content.res.ColorStateList.valueOf(palette.accent));
        go.setScaleType(ImageView.ScaleType.CENTER);
        go.setContentDescription("Go");
        go.setOnClickListener(v -> go());
        LinearLayout addressRow = new LinearLayout(this);
        addressRow.setOrientation(LinearLayout.HORIZONTAL);
        addressRow.setGravity(android.view.Gravity.CENTER_VERTICAL);
        addressRow.setBackgroundColor(palette.surface);
        int gap = GrabUi.dp(this, 8);
        addressRow.setPadding(gap, gap, gap, gap);
        addressRow.addView(address, new LinearLayout.LayoutParams(0, GrabUi.dp(this, 48), 1f));
        addressRow.addView(go, new LinearLayout.LayoutParams(GrabUi.dp(this, 48), GrabUi.dp(this, 48)));
        root.addView(addressRow, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(divider(), new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, GrabUi.dp(this, 1)));

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
            public void onPageFinished(WebView view, String url) {
                // The next chapter has loaded; the delay lets its reader start drawing pages.
                if (!loadingNext) return;
                loadingNext = false;
                web.postDelayed(() -> {
                    if (chaptersLeft > 0 && !grabbing) startGrab();
                }, 2500);
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

        chip = new GrabUi.Chip(this, palette);
        LinearLayout.LayoutParams chipParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        chipParams.setMargins(gap, gap, gap, gap);
        root.addView(chip, chipParams);

        root.addView(divider(), new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, GrabUi.dp(this, 1)));
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(android.view.Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(palette.surface);
        bar.setPadding(gap, gap, gap, gap);
        grab = new GrabUi.BarButton(this, palette, R.drawable.ic_grab, "Grab", false);
        grab.setOnClickListener(v -> startGrab());
        chapters = new GrabUi.BarButton(this, palette, R.drawable.ic_chapters, "Chapters", false);
        chapters.setOnClickListener(v -> toggleChapters());
        quick = new GrabUi.BarButton(this, palette, R.drawable.ic_quick, "Quick read", true);
        quick.setOnClickListener(v -> {
            if (grabbing && runId != null) cancelGrab();
            else startGrab(true);
        });
        ImageView more = new ImageView(this);
        more.setImageResource(R.drawable.ic_more);
        more.setImageTintList(android.content.res.ColorStateList.valueOf(palette.muted));
        more.setScaleType(ImageView.ScaleType.CENTER);
        more.setContentDescription("More");
        more.setOnClickListener(v -> showMore(v));
        int tall = GrabUi.dp(this, 48);
        bar.addView(grab, new LinearLayout.LayoutParams(0, GrabUi.dp(this, 56), 1f));
        bar.addView(chapters, new LinearLayout.LayoutParams(0, GrabUi.dp(this, 56), 1f));
        LinearLayout.LayoutParams quickParams = new LinearLayout.LayoutParams(0, tall, 1.6f);
        quickParams.setMargins(gap, 0, gap, 0);
        bar.addView(quick, quickParams);
        bar.addView(more, new LinearLayout.LayoutParams(tall, tall));
        root.addView(bar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        setContentView(root);
        root.setFitsSystemWindows(true);
        if (state != null) web.restoreState(state);
        else {
            say(OPEN_FIRST);
            handleShare(getIntent());
        }
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
        runOnUiThread(() -> {
            grab.setBusy(true);
            quick.setBusy(true);
        });
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
                    null, images, unreadable, truncated);
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

    private android.view.View divider() {
        android.view.View line = new android.view.View(this);
        line.setBackgroundColor(palette.border);
        return line;
    }

    /** Open Baihe and Settings are rarely needed mid-grab, so they sit behind one button. */
    private void showMore(android.view.View anchor) {
        android.widget.PopupMenu menu = new android.widget.PopupMenu(this, anchor);
        menu.getMenu().add("Open Baihe");
        menu.getMenu().add("Settings");
        menu.setOnMenuItemClickListener(item -> {
            if ("Settings".contentEquals(item.getTitle())) showSettings();
            else openBaihe();
            return true;
        });
        menu.show();
    }

    /** While a grab runs the main button is its Cancel; Quick read comes back when it ends. */
    private void showCancel(boolean running) {
        runOnUiThread(() -> {
            quick.setDanger(running);
            if (running) quick.setLabel("Cancel", palette.onAccent, R.drawable.ic_stop);
            else {
                quick.setLabel("Quick read", palette.onAccent, R.drawable.ic_quick);
                quick.setBusy(false);
            }
        });
    }

    /** Stops this grab on the phone and tells the server to drop the pages it has not started. */
    private void cancelGrab() {
        if (!grabbing) return;
        cancelRequested = true;
        chaptersLeft = 0;
        loadingNext = false;
        String id = runId;
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (id != null && uploading) {
            new Thread(() -> {
                try {
                    new BaiheClient(server, token).cancelRun(id);
                } catch (BaiheClient.BridgeException ignored) {
                    // The phone side has already stopped; the server only finishes its page in flight.
                }
            }, "grab-cancel").start();
        }
        QuickReadSession.finish();
        if (uploading) say("Cancelling...", GrabUi.Kind.BUSY);
        else finishGrab("Cancelled.");
    }

    private void showChapterState() {
        if (chaptersLeft > 0) chapters.setLabel("Stop (" + chaptersLeft + " left)", palette.bad, R.drawable.ic_stop);
        else {
            chapters.setLabel("Chapters", palette.muted, R.drawable.ic_chapters);
            chapters.resetTint();
        }
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
        boolean busy = text.startsWith("Reading ") || text.startsWith("Redrawing ") || text.startsWith("Sending ");
        say(text, busy ? GrabUi.Kind.BUSY : GrabUi.Kind.INFO);
    }

    private void say(String text, GrabUi.Kind kind) {
        runOnUiThread(() -> chip.show(text, kind));
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
        token.setHint("Token (starts with baihe_dt_)");
        token.setSingleLine(true);
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        form.addView(server);
        form.addView(token);
        new AlertDialog.Builder(this)
                .setTitle("Settings")
                .setMessage("Make the token at the PC in Baihe: Settings > Extension tokens per computer.")
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
        startGrab(false);
    }

    private void startGrab(boolean readOnly) {
        if (grabbing) {
            say("Wait for the current grab to finish.");
            return;
        }
        // Following chapters saves each one, so a Quick read never starts inside that run.
        if (readOnly && chaptersLeft > 0) {
            say("Stop following chapters first.");
            return;
        }
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (server.isEmpty() || token.isEmpty()) {
            say("Set up Grab first: enter your server address and a phone token.");
            showSettings();
            return;
        }
        String current = web.getUrl();
        if (current == null || current.isEmpty() || current.startsWith("about:")) {
            say(OPEN_FIRST);
            return;
        }
        grabbing = true;
        quickRead = readOnly;
        grabStartedAt = System.currentTimeMillis();
        runId = "grab-" + newNonce().substring(0, 16);
        cancelRequested = false;
        grab.setBusy(true);
        showCancel(true);
        say("Reading pages");
        String nonce = newNonce();
        bridge.start(nonce);
        pageUrl = web.getUrl() == null ? "" : web.getUrl();
        pageAgent = web.getSettings().getUserAgentString();
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

    /** Pressing Chapters asks how many to follow; pressing it again while following stops. */
    private void toggleChapters() {
        if (chaptersLeft > 0) {
            chaptersLeft = 0;
            loadingNext = false;
            runOnUiThread(this::showChapterState);
            say("Stopped after this chapter.");
            return;
        }
        if (grabbing) {
            say("Wait for the current grab to finish.");
            return;
        }
        EditText count = new EditText(this);
        count.setInputType(InputType.TYPE_CLASS_NUMBER);
        count.setText("3");
        new AlertDialog.Builder(this)
                .setTitle("Grab this chapter and the next ones")
                .setMessage("How many chapters in all (up to " + MAX_CHAPTERS + ")?")
                .setView(count)
                .setPositiveButton("Start", (d, w) -> {
                    int n = 0;
                    try {
                        n = Integer.parseInt(count.getText().toString().trim());
                    } catch (NumberFormatException ignored) {
                        // Falls through to the range message below.
                    }
                    if (n < 1 || n > MAX_CHAPTERS) {
                        say("Enter a number from 1 to " + MAX_CHAPTERS + ".");
                        return;
                    }
                    chaptersLeft = n;
                    showChapterState();
                    startGrab();
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    /** Ends one grab; when following chapters and this one went through, opens the next. */
    private void finishGrab(String message) {
        finishGrab(message, false);
    }

    private void finishGrab(String message, boolean sent) {
        grabbing = false;
        uploading = false;
        bridge.cancel();
        String next = null;
        if (chaptersLeft > 0) {
            chaptersLeft--;
            if (sent && chaptersLeft > 0) {
                next = ServerAddress.nextChapterUrl(pageUrl, pageNext);
                if (next == null) message = message + ". No next chapter link was found.";
            }
            if (next == null) chaptersLeft = 0;
        }
        String shown = message;
        String go = next;
        runOnUiThread(() -> {
            grab.setBusy(false);
            showCancel(false);
            showChapterState();
            chip.show(shown, GrabUi.kindOf(shown, sent));
            if (go != null) {
                loadingNext = true;
                web.loadUrl(go);
            }
        });
    }

    private static final int MAX_CHAPTERS = 20;
    private static final String OPEN_FIRST = "Open a comic chapter first: enter its site address above and tap Go.";
    private static final int QUICK_READ_BATCH = 4;
    // Batches in flight at once: the PC reads two pages at a time, so one stream leaves it waiting on uploads.
    private static final int QUICK_READ_STREAMS = 2;
    private volatile String pageNext = "";
    private JSONArray pageChapters = new JSONArray();

    private static final long GRAB_TIMEOUT_MS = 5 * 60 * 1000;
    private volatile String runningGrab = "";
    // Names this grab's batches on the server so Cancel can stop the pages it has not started.
    private volatile String runId;
    private volatile long grabStartedAt;
    private volatile boolean cancelRequested;
    // From the web view, not the page: a page can claim any address.
    private volatile String pageUrl = "";
    private volatile String pageAgent = "";
    private List<String> pageTitles = new ArrayList<>();

    @Override
    public void onBegin(List<String> titles, int count, JSONArray chapterLinks, String next) {
        pageTitles = titles;
        pageChapters = chapterLinks;
        pageNext = next;
        if (count == 0) say("Reading pages");
    }

    @Override
    public void onDone(List<PageImage> images, int unreadable, boolean truncated) {
        if (images.isEmpty()) {
            finishGrab(unreadable > 0
                    ? "Found " + unreadable + " pictures but the site would not let Baihe read them."
                    : "No page images were found on this page.");
            return;
        }
        String url = pageUrl;
        String agent = pageAgent;
        List<String> titles = pageTitles;
        JSONArray chapterLinks = pageChapters;
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        uploading = true;
        boolean readOnly = quickRead;
        sender.execute(() -> {
            // Pictures the page would not hand over are downloaded here, in page order.
            List<PageImage> ready = new ArrayList<>();
            int bad = unreadable;
            boolean cut = truncated;
            long used = 0;
            long downloadDeadline = System.currentTimeMillis() + 5 * 60_000L;
            for (PageImage image : images) {
                if (cancelRequested) {
                    finishGrab("Cancelled.");
                    return;
                }
                PageImage got = image;
                if (image.needsDownload()) {
                    got = System.currentTimeMillis() > downloadDeadline ? null
                            : ImageFetch.download(image, url, android.webkit.CookieManager.getInstance()::getCookie, agent);
                }
                if (got == null) {
                    bad++;
                } else if (!SharedImages.fitsTotal(used, got.base64.length())) {
                    cut = true;
                    break;
                } else {
                    used += got.base64.length();
                    ready.add(got);
                }
            }
            if (ready.isEmpty()) {
                finishGrab("Found " + bad + " pictures but the site would not let Baihe read them.");
                return;
            }
            BaiheClient client = new BaiheClient(server, token);
            client.runId = runId;
            if (readOnly) read(client, url, ready, bad, cut);
            else upload(client, url, titles, chapterLinks, ready, bad, cut);
        });
    }

    /**
     * Quick read: send the pages without saving. Two batches are in flight at once so the PC is
     * not idle while the next one uploads, but pages are shown in page order: a later batch's
     * pages wait until the earlier ones are done. The reader opens on the first finished page and
     * the rest are added as the server streams them.
     */
    private void read(BaiheClient client, String url, List<PageImage> images, int unreadable, boolean truncated) {
        QuickReadSession.begin();
        QuickReadSession.onCancel(this::cancelGrab);
        long readStarted = System.currentTimeMillis();
        java.util.concurrent.ExecutorService pool = Executors.newFixedThreadPool(QUICK_READ_STREAMS);
        try {
            // A typeset answer is large, so the batch stays small however many the server allows.
            int max = Math.min(QUICK_READ_BATCH, client.health().optInt("max_images_per_request", 12));
            QuickReadActivity.clear(this);
            List<List<PageImage>> batches = Batches.plan(images, max);
            int n = batches.size();
            Object lock = new Object();
            List<List<byte[]>> held = new ArrayList<>();
            boolean[] finished = new boolean[n];
            for (int i = 0; i < n; i++) held.add(new ArrayList<>());
            int[] frontier = {0};
            int[] shown = {0};
            long[] firstPageAt = {0};
            boolean[] opened = {false};
            String[] firstError = {""};
            boolean[] refused = {false};

            // Called with `lock` held: writes a page and, the first time, opens the reader.
            java.util.function.Consumer<byte[]> show = jpeg -> {
                String name = String.format(java.util.Locale.ROOT, "page-%03d.jpg", shown[0]);
                try (java.io.FileOutputStream out = new java.io.FileOutputStream(
                        new java.io.File(QuickReadActivity.quickReadDir(this), name))) {
                    out.write(jpeg);
                } catch (IOException e) {
                    return;
                }
                shown[0]++;
                QuickReadSession.add(name);
                if (!opened[0]) {
                    opened[0] = true;
                    firstPageAt[0] = System.currentTimeMillis();
                    Intent open = new Intent(this, QuickReadActivity.class);
                    runOnUiThread(() -> startActivity(open));
                }
            };

            List<java.util.concurrent.Future<?>> running = new ArrayList<>();
            for (int i = 0; i < n; i++) {
                final int index = i;
                running.add(pool.submit(() -> {
                    if (refused[0] || cancelRequested || QuickReadSession.isAbandoned()) {
                        synchronized (lock) {
                            finished[index] = true;
                            advance(frontier, finished, held, show);
                        }
                        return;
                    }
                    say("Redrawing " + images.size() + " pictures: " + shown[0] + " ready");
                    try {
                        JSONObject done = client.streamPages(url, batches.get(index), page -> {
                            JSONObject wrapped;
                            try {
                                wrapped = new JSONObject().put("pages", new JSONArray().put(page));
                            } catch (org.json.JSONException e) {
                                return;
                            }
                            List<byte[]> jpegs = QuickRead.typesetPages(wrapped);
                            synchronized (lock) {
                                if (jpegs.isEmpty()) {
                                    if (firstError[0].isEmpty()) firstError[0] = QuickRead.firstWarning(wrapped);
                                    return;
                                }
                                for (byte[] jpeg : jpegs) {
                                    if (index == frontier[0]) show.accept(jpeg);
                                    else held.get(index).add(jpeg);
                                }
                            }
                        });
                        synchronized (lock) {
                            JSONArray failed = done.optJSONArray("failed");
                            if (firstError[0].isEmpty() && failed != null && failed.length() > 0) {
                                firstError[0] = failed.optJSONObject(0) == null ? "" : failed.optJSONObject(0).optString("error", "");
                            }
                            JSONArray skipped = done.optJSONArray("skipped");
                            if (firstError[0].isEmpty() && skipped != null && skipped.length() > 0) {
                                firstError[0] = skipped.length() + " pictures were not pages ("
                                        + (skipped.optJSONObject(0) == null ? "" : skipped.optJSONObject(0).optString("reason", "")) + ")";
                            }
                        }
                    } catch (BaiheClient.BridgeException e) {
                        synchronized (lock) {
                            if (firstError[0].isEmpty()) firstError[0] = e.getMessage();
                            // A token the server refused, or a spent limit, would refuse every later batch too.
                            if (e.status == 401 || e.status == 403 || e.status == 429) refused[0] = true;
                        }
                    }
                    synchronized (lock) {
                        finished[index] = true;
                        advance(frontier, finished, held, show);
                    }
                }));
            }
            for (java.util.concurrent.Future<?> f : running) {
                try {
                    f.get();
                } catch (InterruptedException | java.util.concurrent.ExecutionException e) {
                    // A batch that threw is counted as nothing shown; the others carry on.
                }
            }
            QuickReadSession.finish();
            long ended = System.currentTimeMillis();
            String timing = firstPageAt[0] == 0 ? "" : String.format(java.util.Locale.ROOT,
                    " (found %d s, first page %d s, all %d s)", (readStarted - grabStartedAt) / 1000,
                    (firstPageAt[0] - readStarted) / 1000, (ended - readStarted) / 1000);
            String message = cancelRequested && shown[0] == 0 ? "Cancelled."
                    : (cancelRequested ? "Cancelled. " : "")
                    + QuickRead.summary(shown[0], images.size() + unreadable, firstError[0])
                    + (truncated ? ". Some pages were left out." : "") + timing;
            QuickReadSession.onCancel(null);
            finishGrab(message, false);
        } catch (BaiheClient.BridgeException e) {
            QuickReadSession.onCancel(null);
            QuickReadSession.finish();
            finishGrab(e.getMessage());
        } catch (Exception e) {
            QuickReadSession.onCancel(null);
            QuickReadSession.finish();
            finishGrab("Could not read the pages.");
        } finally {
            pool.shutdown();
        }
    }

    /** With the read lock held: shows what earlier batches held back, moving on past each finished batch. */
    private static void advance(int[] frontier, boolean[] finished, List<List<byte[]>> held,
                                java.util.function.Consumer<byte[]> show) {
        while (frontier[0] < finished.length) {
            List<byte[]> waiting = held.get(frontier[0]);
            for (byte[] jpeg : waiting) show.accept(jpeg);
            waiting.clear();
            if (!finished[frontier[0]]) break;
            frontier[0]++;
        }
    }

    private void upload(BaiheClient client, String url, List<String> titles, JSONArray chapterLinks,
                        List<PageImage> images, int unreadable, boolean truncated) {
        try {
            int max = client.health().optInt("max_images_per_request", 12);
            JSONObject title = client.resolveTitle(url, titles, chapterLinks);
            long dramaId = title.getLong("drama_id");
            List<List<PageImage>> batches = Batches.plan(images, max);
            int stored = 0;
            int failed = 0;
            String firstError = "";
            for (int i = 0; i < batches.size() && !cancelRequested; i++) {
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
            StringBuilder out = new StringBuilder((cancelRequested ? "Cancelled. " : "") + "Sent " + stored + " pages to " + title.optString("title", "Baihe"));
            if (failed > 0 || truncated) out.append(". Not sent: ").append(failed + (truncated ? " or more" : ""));
            if (!firstError.isEmpty()) out.append(". ").append(firstError);
            // A chapter where every page was refused must not send the chain on to the next.
            finishGrab(out.toString(), stored > 0);
        } catch (BaiheClient.BridgeException e) {
            finishGrab(e.getMessage());
        } catch (Exception e) {
            finishGrab("Could not send the pages.");
        }
    }
}
