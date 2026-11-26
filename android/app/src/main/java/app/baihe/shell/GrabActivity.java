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
    static final String PREFS = "grab";
    static final String KEY_SERVER = "server";
    static final String KEY_TOKEN = "token";
    static final String KEY_READ_AHEAD = "read_ahead";

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
    // A Quick read of a chapter split in parts ("(1/2)") follows each part into the same reader.
    private volatile boolean followingParts;
    private volatile boolean partsContinuing;
    private volatile boolean uploading;
    // Optional "Translate this page" for reading a raw site; a grab always switches it off first.
    private boolean translateOn;
    private boolean translateRestoring;

    @SuppressLint({"SetJavaScriptEnabled", "AddJavascriptInterface"})
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        bridge = new GrabBridge(this);
        // Pages left by a Quick read that was cut off (the app was killed) are not kept.
        QuickReadActivity.clear(this);
        // Android 13 hides the "reading in the background" notice until this is allowed; the grab
        // keeps running either way.
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[] {"android.permission.POST_NOTIFICATIONS"}, 1);
        }

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

        root.addView(ShellTabs.strip(this, palette, true), new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
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
        // Pinch to zoom out (or in) on the site; without the built-in controls the page cannot be pinched at all.
        settings.setBuiltInZoomControls(true);
        settings.setDisplayZoomControls(false);
        // With multiple windows off, a pop-up replaces the page being read; on, onCreateWindow decides.
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        AdHosts.setExtra(prefs().getString(KEY_EXTRA_ADS, ""));
        // Sign-in on YouTube, Twitch and similar sites sets cookies from a second site, which a WebView drops by default.
        android.webkit.CookieManager.getInstance().setAcceptThirdPartyCookies(web, true);
        web.setWebChromeClient(new android.webkit.WebChromeClient() {
            @Override
            public boolean onCreateWindow(WebView view, boolean dialog, boolean userGesture, android.os.Message resultMsg) {
                // A tap on a real link may open a new window; anything a script opens by itself is a pop-up.
                if (!userGesture || !(resultMsg.obj instanceof WebView.WebViewTransport)) return false;
                WebView catcher = new WebView(GrabActivity.this);
                catcher.setWebViewClient(new WebViewClient() {
                    private boolean handled;

                    @Override
                    public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest request) {
                        take(v, request.getUrl());
                        return true;
                    }

                    @Override
                    public void onPageStarted(WebView v, String url, Bitmap favicon) {
                        take(v, Uri.parse(url));
                    }

                    private void take(WebView v, Uri uri) {
                        if (handled) return;
                        handled = true;
                        boolean web = "https".equals(uri.getScheme()) || "http".equals(uri.getScheme());
                        if (web && !AdHosts.blocks(uri.getHost())) GrabActivity.this.web.loadUrl(uri.toString());
                        v.stopLoading();
                        v.post(v::destroy);
                    }
                });
                ((WebView.WebViewTransport) resultMsg.obj).setWebView(catcher);
                resultMsg.sendToTarget();
                return true;
            }
        });
        web.addJavascriptInterface(bridge, "Baihe");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                String scheme = request.getUrl().getScheme();
                if (!"https".equals(scheme) && !"http".equals(scheme)) return true;
                // A redirect to an ad network is cut off, so the page stays where it is.
                return request.isForMainFrame() && AdHosts.blocks(request.getUrl().getHost());
            }

            @Override
            public android.webkit.WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                if (!AdHosts.blocks(request.getUrl().getHost())) return null;
                return new android.webkit.WebResourceResponse("text/plain", "utf-8",
                        new java.io.ByteArrayInputStream(new byte[0]));
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                rememberPlace(url, view.getTitle());
                noteVisit(url, view.getTitle());
                if (translateOn && !translateRestoring) web.evaluateJavascript(PageTranslate.ON, null);
                if (quickAfterLoad) {
                    quickAfterLoad = false;
                    web.postDelayed(() -> {
                        if (!grabbing) startGrab(true);
                    }, 2500);
                    return;
                }
                // The next chapter has loaded; the delay lets its reader start drawing pages.
                if (!loadingNext) return;
                loadingNext = false;
                web.postDelayed(() -> {
                    if (chaptersLeft > 0 && !grabbing) {
                        startGrab(followingParts);
                        // A grab that could not start would leave the open reader waiting for pages.
                        if (!grabbing && followingParts) cancelGrab();
                    }
                }, 2500);
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                // A site whose phone reader cannot be walked is read through its desktop page; the
                // switch costs one reload, and leaving the site restores the phone's own agent.
                String desktop = DesktopSite.rewrite(url);
                if (desktop != null && (!desktopAgent || !desktop.equals(url))) {
                    desktopAgent = true;
                    view.getSettings().setUserAgentString(DesktopSite.AGENT);
                    view.stopLoading();
                    view.loadUrl(desktop);
                    return;
                }
                if (desktop == null && desktopAgent) {
                    desktopAgent = false;
                    view.getSettings().setUserAgentString(null);
                    view.stopLoading();
                    view.loadUrl(url);
                    return;
                }
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
        grab = new GrabUi.BarButton(this, palette, R.drawable.ic_grab, "Save", false);
        grab.setOnClickListener(v -> startGrab());
        chapters = new GrabUi.BarButton(this, palette, R.drawable.ic_chapters, "Save chapters", false);
        chapters.setOnClickListener(v -> toggleChapters());
        quick = new GrabUi.BarButton(this, palette, R.drawable.ic_quick, "Read now", true);
        quick.setOnClickListener(v -> {
            if (QuickReadSession.isDetached()) openReader();
            else if ((grabbing && runId != null) || (followingParts && chaptersLeft > 0)) cancelGrab();
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
            handleCommand(getIntent());
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleShare(intent);
        handleCommand(intent);
    }

    /** What the history and bookmark pages ask for: go back to a page, read it again, start the queue, or bookmark the open page. */
    static final String EXTRA_COMMAND = "grab_command";
    static final String EXTRA_URL = "grab_url";

    private void handleCommand(Intent intent) {
        String command = intent == null ? null : intent.getStringExtra(EXTRA_COMMAND);
        if (command == null) return;
        String url = ServerAddress.browsable(intent.getStringExtra(EXTRA_URL));
        intent.removeExtra(EXTRA_COMMAND);
        if ("start".equals(command)) {
            startQueue();
        } else if ("bookmark".equals(command)) {
            // The Bookmarks screen's button only adds: there, Remove is on each card.
            String open = ServerAddress.browsable(web.getUrl());
            if (open != null && GrabBookmarks.has(GrabBookmarks.parse(prefs().getString(KEY_BOOKMARKS, "")), open)) {
                say("This page is already bookmarked.");
            } else {
                toggleBookmark();
            }
        } else if (url != null && ("open".equals(command) || "read".equals(command))) {
            if ("read".equals(command)) {
                if (grabbing || loadingNext || chaptersLeft > 0) {
                    say("Wait for the current grab to finish.");
                    return;
                }
                // The PC remembers a page it already translated, so this costs no tokens while it does.
                quickAfterLoad = true;
            }
            web.loadUrl(url);
        }
    }

    static final String KEY_EXTRA_ADS = "extra_ad_hosts";

    private static final int REQUEST_GALLERY = 41;

    private void pickGallery() {
        Intent pick = new Intent(Intent.ACTION_GET_CONTENT).setType("image/*")
                .addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(Intent.createChooser(pick, "Pictures to translate"), REQUEST_GALLERY);
    }

    /** Picked pictures take the same road as ones shared from the gallery, so the title prompt and limits are shared. */
    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != REQUEST_GALLERY || result != RESULT_OK || data == null) return;
        ArrayList<Uri> picked = new ArrayList<>();
        if (data.getClipData() != null) {
            for (int i = 0; i < data.getClipData().getItemCount(); i++) picked.add(data.getClipData().getItemAt(i).getUri());
        } else if (data.getData() != null) {
            picked.add(data.getData());
        }
        if (picked.isEmpty()) return;
        Intent shared = new Intent(Intent.ACTION_SEND_MULTIPLE).setType("image/*")
                .putParcelableArrayListExtra(Intent.EXTRA_STREAM, picked);
        shareImages(shared);
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
            else askOpenOrQueue(url);
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
                    sendShared(uris, title, cut, server, token, false);
                })
                // A screenshot of a page to read now: translated and shown, nothing saved, no title needed.
                .setNeutralButton("Read now", (d, w) -> sendShared(uris, "", cut, server, token, true))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void sendShared(List<Uri> uris, String title, boolean cut, String server, String token, boolean read) {
        grabbing = true;
        uploading = true;
        quickRead = read;
        if (read) {
            runName = "Shared pictures";
            runUrl = "";
            cancelRequested = false;
        }
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
            BaiheClient client = new BaiheClient(server, token);
            if (read) {
                client.typesetMaxWidth = getResources().getDisplayMetrics().widthPixels;
                client.textStyle = textStyle(prefs());
                read(client, "", images, unreadable, truncated);
                return;
            }
            upload(client, "", java.util.Collections.singletonList(title), null, images, unreadable, truncated);
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

    /** Sections put what a reader needs on this page first; Open Baihe and Settings sit last. */
    private void showMore(android.view.View anchor) {
        List<GrabBookmarks.Mark> marks = GrabBookmarks.parse(prefs().getString(KEY_BOOKMARKS, ""));
        String here = ServerAddress.browsable(web.getUrl());
        int savedCount = SavedChapters.list(SavedChapters.root(this)).size();
        int waiting = GrabStore.queue(prefs()).size();
        GrabMenu menu = new GrabMenu(this, palette).section("This page");
        menu.item(translateOn ? "Show original page" : "Translate this page", () -> setTranslate(!translateOn));
        if (QuickReadSession.isDetached()) menu.item("Stop translating", this::cancelGrab);
        else if (QuickReadSession.hasPages()) menu.item("Open reader (" + QuickReadSession.snapshot().size() + " pages)", this::openReader);
        menu.item(here != null && GrabBookmarks.has(marks, here) ? "Remove bookmark" : "Bookmark this page", this::toggleBookmark);
        menu.item("Add to queue", this::askAddToQueue);
        menu.item("Pictures from my gallery", this::pickGallery);
        menu.section("This series");
        menu.item("Follow this series", this::followSeries);
        menu.item("Grab whole series", this::askWholeSeries);
        menu.section("My lists");
        menu.item("Pick up where I left off", this::pickUp);
        menu.item(marks.isEmpty() ? "Bookmarks" : "Bookmarks (" + marks.size() + ")", () -> startActivity(new Intent(this, GrabBookmarksActivity.class)));
        menu.item("Recent pages", () -> startActivity(new Intent(this, GrabBookmarksActivity.class)
                .putExtra(GrabBookmarksActivity.EXTRA_RECENT, true)));
        menu.item(savedCount == 0 ? "Saved chapters" : "Saved chapters (" + savedCount + ")", this::showSaved);
        menu.item(waiting == 0 ? "Queue and history" : "Queue and history (" + waiting + " waiting)",
                () -> startActivity(new Intent(this, GrabHistoryActivity.class)));
        menu.section("Video");
        menu.item("Send video to Baihe", this::sendVideo);
        menu.item("Start Live here", this::askStartLive);
        menu.section("App");
        menu.item("Settings", this::showSettings);
        menu.item("Report a problem", () -> ProblemReportDialog.show(this, "Grab", web.getUrl(), web));
        menu.item("Open Baihe", this::openBaihe);
        menu.show();
    }

    private String lastRemembered = "";

    /** Tells the PC which page of a public site is open, so this account's other phones can go on from it. */
    private void rememberPlace(String url, String title) {
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        // No query: it can hold a login token, and this goes to the PC and into its backups.
        String link = ServerAddress.publicSourceUrl(url);
        if (server.isEmpty() || token.isEmpty() || link.isEmpty() || link.equals(lastRemembered)
                || GrabBookmarks.isSite(link) || ServerAddress.sameOrigin(server, link)) return;
        lastRemembered = link;
        sender.execute(() -> {
            try {
                new BaiheClient(server, token).rememberPage(link, title);
            } catch (BaiheClient.BridgeException e) {
                // Only a convenience: a missed save is not worth interrupting the reader.
            }
        });
    }

    /** Lists the page this account last read on each site, from any of its phones; tap one to open it. */
    private void pickUp() {
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (server.isEmpty() || token.isEmpty()) {
            say("Set up Grab first: enter your server address and a phone token.");
            showSettings();
            return;
        }
        say("Asking Baihe where you left off...");
        sender.execute(() -> {
            try {
                List<String[]> pages = new BaiheClient(server, token).lastPages();
                runOnUiThread(() -> showPlaces(pages));
            } catch (BaiheClient.BridgeException e) {
                say(e.getMessage(), GrabUi.Kind.BAD);
            }
        });
    }

    private void showPlaces(List<String[]> pages) {
        if (pages.isEmpty()) {
            say("Nothing yet. Pages you read in Grab show up here, on every phone you sign in on.");
            return;
        }
        String[] names = new String[pages.size()];
        for (int i = 0; i < names.length; i++) {
            String[] page = pages.get(i);
            names[i] = page[0] + (page[2].isEmpty() ? "" : ": " + page[2]);
        }
        new AlertDialog.Builder(this)
                .setTitle("Where you left off")
                .setItems(names, (d, which) -> {
                    String link = ServerAddress.browsable(pages.get(which)[1]);
                    if (link != null) web.loadUrl(link);
                })
                .setNegativeButton("Close", null)
                .show();
    }

    /** Chapters saved for offline reading: tap to read, hold to delete. */
    private void showSaved() {
        List<SavedChapters.Saved> saved = SavedChapters.list(SavedChapters.root(this));
        if (saved.isEmpty()) {
            say("No saved chapters. In Quick read, hold a page and choose Save to phone.");
            return;
        }
        String[] names = new String[saved.size()];
        for (int i = 0; i < names.length; i++) names[i] = saved.get(i).title + " (" + saved.get(i).pages + " pages)";
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Saved chapters (hold one to delete it)")
                .setItems(names, (d, which) -> startActivity(new Intent(this, QuickReadActivity.class)
                        .putExtra(QuickReadActivity.EXTRA_SAVED, saved.get(which).dir.getName())))
                .setNegativeButton("Close", null)
                .create();
        dialog.getListView().setOnItemLongClickListener((parent, view, which, id) -> {
            SavedChapters.delete(saved.get(which).dir);
            dialog.dismiss();
            say("Deleted from this phone.");
            return true;
        });
        dialog.show();
    }

    static TextStyle textStyle(android.content.SharedPreferences p) {
        return new TextStyle(p.getInt("text_size", 0), p.getBoolean("text_outline", false), p.getInt("text_font", 0));
    }

    static final String KEY_RECENT = "recent_pages";
    private static final int MAX_RECENT = 30;

    /** Keeps the pages opened here on the phone only, so a page can be found again; nothing is sent anywhere. */
    private void noteVisit(String url, String title) {
        String link = ServerAddress.publicSourceUrl(url);
        if (link.isEmpty()) return;
        List<GrabBookmarks.Mark> recent = GrabBookmarks.parse(prefs().getString(KEY_RECENT, ""));
        recent = GrabBookmarks.with(recent, title, link, MAX_RECENT);
        prefs().edit().putString(KEY_RECENT, GrabBookmarks.serialize(recent)).apply();
    }

    static final String KEY_BOOKMARKS = "bookmarks";

    private void toggleBookmark() {
        String link = ServerAddress.browsable(web.getUrl());
        if (link == null) {
            say("Open a page first.");
            return;
        }
        List<GrabBookmarks.Mark> marks = GrabBookmarks.parse(prefs().getString(KEY_BOOKMARKS, ""));
        boolean had = GrabBookmarks.has(marks, link);
        marks = had ? GrabBookmarks.without(marks, link) : GrabBookmarks.with(marks, web.getTitle(), link);
        prefs().edit().putString(KEY_BOOKMARKS, GrabBookmarks.serialize(marks)).apply();
        say(had ? "Bookmark removed." : "Bookmarked.", had ? GrabUi.Kind.INFO : GrabUi.Kind.OK);
    }

    /** Has the PC watch this site's series for new chapters; the phone then pings when one arrives. */
    private void followSeries() {
        String link = ServerAddress.publicSourceUrl(web.getUrl());
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (link == null || !link.startsWith("http")) {
            say("Open a series or chapter page first.");
            return;
        }
        if (server.isEmpty() || token.isEmpty()) {
            say("Set up Grab first: enter your server address and a phone token.");
            showSettings();
            return;
        }
        say("Asking Baihe to follow this series...");
        sender.execute(() -> {
            try {
                String title = new BaiheClient(server, token).follow(link);
                say("Following " + (title.isEmpty() ? "this series" : title)
                        + ". Your phone will tell you when a new chapter comes out.", GrabUi.Kind.OK);
            } catch (BaiheClient.BridgeException e) {
                say(e.getMessage(), GrabUi.Kind.BAD);
            }
        });
    }

    /**
     * Sends this page's link to Quick video translate on the PC, which downloads and subtitles it.
     * Only the link goes: the phone's logins stay here, so a video that needs one will not download.
     */
    private void sendVideo() {
        String link = ServerAddress.videoLink(web.getUrl());
        if (link.isEmpty()) {
            say("Open a video page on a public website first.");
            return;
        }
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (server.isEmpty() || token.isEmpty()) {
            say("Set up Grab first: enter your server address and a phone token.");
            showSettings();
            return;
        }
        say("Sending the video link to Baihe...");
        sender.execute(() -> {
            try {
                new BaiheClient(server, token).sendVideo(link);
                say("Sent. Baihe is downloading and subtitling it; follow it in Baihe's Jobs.", GrabUi.Kind.OK);
            } catch (BaiheClient.BridgeException e) {
                say(e.getMessage(), GrabUi.Kind.BAD);
            }
        });
    }

    /** Asks which language the stream speaks, then starts Live captions on it from the PC. */
    private void askStartLive() {
        String link = ServerAddress.videoLink(web.getUrl());
        if (link.isEmpty()) {
            say("Open a live stream page on a public website first.");
            return;
        }
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        if (server.isEmpty() || token.isEmpty()) {
            say("Set up Grab first: enter your server address and a phone token.");
            showSettings();
            return;
        }
        String[] codes = {"zh", "ja", "ko"};
        new AlertDialog.Builder(this)
                .setTitle("Start Live: what language is spoken?")
                .setItems(new String[] {"Chinese", "Japanese", "Korean"}, (d, which) -> {
                    say("Starting Live...");
                    sender.execute(() -> {
                        try {
                            new BaiheClient(server, token).startLive(link, codes[which]);
                            say("Live started on the PC. Watch it in Baihe's Live page.", GrabUi.Kind.OK);
                        } catch (BaiheClient.BridgeException e) {
                            say(e.getMessage(), GrabUi.Kind.BAD);
                        }
                    });
                })
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void setTranslate(boolean on) {
        translateOn = on;
        web.evaluateJavascript(on ? PageTranslate.ON : PageTranslate.OFF, null);
        if (on) say("Translating this page to English. Save and Read now use the original page.");
    }

    /** While a grab runs the main button is its Cancel; Quick read comes back when it ends. */
    private void showCancel(boolean running) {
        runOnUiThread(() -> {
            quick.setDanger(running);
            if (running) quick.setLabel("Cancel", palette.onAccent, R.drawable.ic_stop);
            else {
                quick.setLabel("Read now", palette.onAccent, R.drawable.ic_quick);
                quick.setBusy(false);
            }
        });
    }

    /** Stops this grab on the phone and tells the server to drop the pages it has not started. */
    private void cancelGrab() {
        boolean betweenParts = !grabbing && followingParts && chaptersLeft > 0;
        if (!grabbing && !betweenParts) return;
        cancelRequested = true;
        queueRunning = false;
        chaptersLeft = 0;
        loadingNext = false;
        followingParts = false;
        if (betweenParts) {
            // The next part is only loading: nothing is running on the server to stop.
            partsContinuing = false;
            QuickReadSession.onCancel(null);
            QuickReadSession.finish();
            showCancel(false);
            say("Cancelled.");
            return;
        }
        String id = runId;
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        BaiheClient running = activeClient;
        if (running != null || (id != null && uploading)) {
            new Thread(() -> {
                if (running != null) running.abort();
                if (id == null || !uploading) return;
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

    private void recordDone(String message) {
        if (message == null || message.isEmpty()) return;
        GrabStore.record(prefs(), new GrabQueue.Done(System.currentTimeMillis(),
                runName.isEmpty() ? "Page" : runName, message, runUrl, quickRead));
    }

    /** The next waiting chapter while the queue is running, or null (which also ends the run). */
    private GrabQueue.Item takeQueued() {
        if (!queueRunning) return null;
        GrabQueue.Item item = GrabStore.take(prefs());
        if (item == null) queueRunning = false;
        return item;
    }

    /** Opens a waiting chapter; the page-finished hook starts the grab, as it does for the next chapter of a series. */
    private void launchQueued(GrabQueue.Item item) {
        runName = item.name;
        runUrl = item.url;
        chaptersLeft = item.chapters;
        loadingNext = true;
        GrabKeepAlive.start(getApplicationContext());
        showChapterState();
        int more = GrabStore.queue(prefs()).size();
        say("Next in the queue: " + item.name + (more > 0 ? " (" + more + " more after it)" : ""), GrabUi.Kind.BUSY);
        web.loadUrl(item.url);
        // A page that never finishes loading must not hold the rest of the queue.
        web.postDelayed(() -> {
            if (loadingNext && !grabbing && item.name.equals(runName)) {
                loadingNext = false;
                finishGrab("The page did not load.");
            }
        }, QUEUE_PAGE_LOAD_MS);
    }

    private void askAddToQueue() {
        String url = web.getUrl();
        if (url == null || ServerAddress.browsable(url) == null) {
            say(OPEN_FIRST);
            return;
        }
        askChapterCount("Add to the queue", "This chapter and the ones after it, saved to your library. "
                + "How many chapters in all (up to " + MAX_CHAPTERS + ")?", "1", "Add", n -> addToQueue(url, n));
    }

    /** Reads the chapter list on the open series page and, after a warning, queues every chapter not yet done. */
    private void askWholeSeries() {
        String url = web.getUrl();
        if (url == null || ServerAddress.browsable(url) == null) {
            say(OPEN_FIRST);
            return;
        }
        web.evaluateJavascript(SeriesRun.LINKS_JS, raw -> {
            // evaluateJavascript hands back the script's string as a JSON string literal.
            String json = "[]";
            try {
                json = new org.json.JSONTokener(raw == null ? "\"[]\"" : raw).nextValue().toString();
            } catch (org.json.JSONException ignored) {
                // Nothing readable lists no chapters.
            }
            List<GrabQueue.Item> links = SeriesRun.parseLinks(json, url);
            if (links.size() < SeriesRun.MIN_LIST) {
                say("Open the series page, the one that lists its chapters, first.");
                return;
            }
            SeriesRun.Plan plan = SeriesRun.plan(links, GrabStore.history(prefs()), GrabStore.queue(prefs()));
            int room = GrabQueue.MAX_QUEUED - GrabStore.queue(prefs()).size();
            if (plan.fresh.isEmpty() || room <= 0) {
                say(plan.fresh.isEmpty() ? "Every chapter listed here is already done or waiting." : "The queue is full.");
                return;
            }
            new AlertDialog.Builder(this)
                    .setTitle("Grab the whole series?")
                    .setMessage(SeriesRun.warning(plan, room))
                    .setPositiveButton("Add " + Math.min(plan.fresh.size(), room) + " chapters", (d, w) -> addSeries(plan, room))
                    .setNegativeButton("Cancel", null)
                    .show();
        });
    }

    private void addSeries(SeriesRun.Plan plan, int room) {
        boolean idle = !grabbing && !loadingNext && chaptersLeft == 0;
        int added = 0;
        for (GrabQueue.Item item : plan.fresh) {
            if (added >= room || !GrabStore.add(prefs(), item)) break;
            added++;
        }
        if (idle && added > 0) startQueue();
        else say("Added " + added + " chapters to the queue (" + GrabStore.queue(prefs()).size() + " waiting).");
    }

    /** A link from another app can wait for its turn, whatever site it is on, instead of replacing the page being grabbed. */
    private void askOpenOrQueue(String url) {
        if (!grabbing && !loadingNext && chaptersLeft == 0) {
            web.loadUrl(url);
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("A grab is running")
                .setMessage("Add " + GrabQueue.siteOf(url) + " to the queue, or open it now (that cuts the grab short)?")
                .setPositiveButton("Add to queue", (d, w) -> addToQueue(url, 1, GrabQueue.nameFor(null, url)))
                .setNegativeButton("Open now", (d, w) -> web.loadUrl(url))
                .setNeutralButton("Cancel", null)
                .show();
    }

    private void addToQueue(String url, int chapters) {
        addToQueue(url, chapters, GrabQueue.nameFor(web.getTitle(), url));
    }

    private void addToQueue(String url, int chapters, String name) {
        GrabQueue.Item item = new GrabQueue.Item(url, chapters, name);
        boolean idle = !grabbing && !loadingNext && chaptersLeft == 0;
        if (!GrabStore.add(prefs(), item)) {
            say(GrabStore.queue(prefs()).size() >= GrabQueue.MAX_QUEUED
                    ? "The queue is full." : "That chapter is already in the queue.");
            return;
        }
        if (idle) {
            startQueue();
        } else {
            // finishGrab only takes a waiting page while the queue is running, so adding during a grab starts it.
            queueRunning = true;
            say("Added to the queue (" + GrabStore.queue(prefs()).size() + " waiting). It runs when this grab is done.");
        }
    }

    /** Starts working through the queue from the first waiting chapter; does nothing while a grab is running. */
    private void startQueue() {
        if (grabbing || loadingNext) {
            say("Wait for the current grab to finish.");
            return;
        }
        queueRunning = true;
        GrabQueue.Item first = takeQueued();
        if (first == null) {
            say("The queue is empty.");
            return;
        }
        launchQueued(first);
    }

    private void showChapterState() {
        if (chaptersLeft > 0 && !followingParts) chapters.setLabel(
                GrabQueue.untilEnd(chaptersLeft) ? "Stop (to the end)" : "Stop (" + chaptersLeft + " left)", palette.bad, R.drawable.ic_stop);
        else {
            chapters.setLabel("Save chapters", palette.muted, R.drawable.ic_chapters);
            chapters.resetTint();
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onStart() {
        super.onStart();
        AppUpdate.screenStarted();
        // The reader is destroyed after this screen is shown again, so it reports being left.
        QuickReadSession.onDetach(() -> runOnUiThread(this::showReaderLeft));
        showReaderLeft();
    }

    /** A reader left mid-grab: the grab shows it is still working, and Quick read opens the reader again. */
    private void showReaderLeft() {
        if (!QuickReadSession.isDetached()) return;
        quick.setDanger(false);
        quick.setLabel("Open reader", palette.onAccent, R.drawable.ic_quick);
        say(backgroundLine(QuickReadSession.snapshot().size()), GrabUi.Kind.BUSY);
    }

    private static String backgroundLine(int ready) {
        return "Still translating in the background: " + ready + " pages ready. More > Stop translating cancels it.";
    }

    private void openReader() {
        startActivity(new Intent(this, QuickReadActivity.class));
    }

    @Override
    protected void onStop() {
        super.onStop();
        // Android writes cookies to disk on its own schedule; a login made just before the app is killed would be lost.
        android.webkit.CookieManager.getInstance().flush();
        AppUpdate.screenStopped(this);
    }

    @Override
    protected void onDestroy() {
        QuickReadSession.onDetach(null);
        GrabKeepAlive.stop(getApplicationContext());
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
        boolean busy = text.startsWith("Reading ") || text.startsWith("Redrawing ") || text.startsWith("Sending ")
                || text.startsWith("Still translating");
        say(text, busy ? GrabUi.Kind.BUSY : GrabUi.Kind.INFO);
    }

    private void say(String text, GrabUi.Kind kind) {
        runOnUiThread(() -> chip.show(text, kind));
    }

    private void openBaihe() {
        ShellTabs.show(this, MainActivity.class);
    }

    private void showSettings() {
        startActivity(new Intent(this, GrabSettingsActivity.class));
    }

    private void startGrab() {
        startGrab(false);
    }

    private void startGrab(boolean readOnly) {
        if (translateRestoring) return;
        if (grabbing) {
            say("Wait for the current grab to finish.");
            return;
        }
        // Following chapters saves each one, so a Quick read never starts inside that run.
        if (readOnly && chaptersLeft > 0 && !followingParts) {
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
        if (translateOn) {
            // The widget rewrites the page's text; the grab must read the site's own page.
            translateOn = false;
            translateRestoring = true;
            web.evaluateJavascript(PageTranslate.OFF, null);
            web.postDelayed(() -> {
                translateRestoring = false;
                startGrab(readOnly);
            }, 800);
            return;
        }
        grabbing = true;
        GrabKeepAlive.start(getApplicationContext());
        if (!loadingNext && !queueRunning) {
            runName = GrabQueue.nameFor(web.getTitle(), current);
            runUrl = current;
        }
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
            queueRunning = false;
            runOnUiThread(this::showChapterState);
            say("Stopped after this chapter.");
            return;
        }
        if (grabbing) {
            say("Wait for the current grab to finish.");
            return;
        }
        askChapterCount("Grab this chapter and the next ones", "How many chapters in all (up to " + MAX_CHAPTERS + ")?",
                "3", "Start", n -> {
                    chaptersLeft = n;
                    showChapterState();
                    startGrab();
                });
    }

    /** A run to the last chapter can be hundreds of chapters, so the size and cost are shown before it starts. */
    private void confirmUntilEnd(java.util.function.IntConsumer onChosen) {
        new AlertDialog.Builder(this)
                .setTitle("Keep going to the last chapter?")
                .setMessage("This can run up to " + GrabQueue.UNTIL_END + " chapters, one after another, while this phone "
                        + "stays on and Grab stays open (roughly 2 to 5 minutes each). It stops by itself when the site has "
                        + "no next chapter or the next one was grabbed before. With a billed engine every page of every "
                        + "chapter is billed until your PC's monthly cap stops it. Stop ends it at any time.")
                .setPositiveButton("Keep going", (d, w) -> onChosen.accept(GrabQueue.UNTIL_END))
                .setNegativeButton("Cancel", null)
                .show();
    }

    /** A chapter count, or the last chapter of the series, handed to onChosen; the same question wherever chapters are followed. */
    private void askChapterCount(String title, String message, String defaultCount, String confirm,
                                  java.util.function.IntConsumer onChosen) {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        form.setPadding(pad, 0, pad, 0);
        EditText count = new EditText(this);
        count.setInputType(InputType.TYPE_CLASS_NUMBER);
        count.setText(defaultCount);
        android.widget.CheckBox toEnd = new android.widget.CheckBox(this);
        toEnd.setText("Keep going until the last chapter");
        toEnd.setOnCheckedChangeListener((box, on) -> count.setEnabled(!on));
        form.addView(count);
        form.addView(toEnd);
        new AlertDialog.Builder(this)
                .setTitle(title)
                .setMessage(message)
                .setView(form)
                .setPositiveButton(confirm, (d, w) -> {
                    if (toEnd.isChecked()) {
                        confirmUntilEnd(onChosen);
                        return;
                    }
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
                    onChosen.accept(n);
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
        strips.clear();
        String next = null;
        if (chaptersLeft > 0) {
            boolean toEnd = GrabQueue.untilEnd(chaptersLeft);
            chaptersLeft--;
            if (sent && chaptersLeft > 0) {
                next = ServerAddress.nextChapterUrl(pageUrl, pageNext);
                if (next == null) {
                    message = message + (toEnd ? ". That was the last chapter." : ". No next chapter link was found.");
                } else if (toEnd && SeriesRun.alreadyDone(next, GrabStore.history(prefs()))) {
                    // An earlier run already sent everything from here on; going further would only repeat it.
                    next = null;
                    message = message + ". The next chapter was grabbed before, so it stopped here.";
                }
            }
            if (next == null) chaptersLeft = 0;
        }
        if (chaptersLeft == 0) {
            if (partsContinuing) {
                // The chain ended with the reader still waiting for a next part that will not come.
                QuickReadSession.onCancel(null);
                QuickReadSession.finish();
            }
            followingParts = false;
            partsContinuing = false;
        }
        GrabQueue.Item queued = null;
        if (next == null && chaptersLeft == 0) {
            recordDone(message);
            queued = takeQueued();
        }
        if (next == null && queued == null) GrabKeepAlive.stop(getApplicationContext());
        String shown = message;
        String go = next;
        GrabQueue.Item following = queued;
        runOnUiThread(() -> {
            grab.setBusy(false);
            if (!(go != null && followingParts)) showCancel(false);
            showChapterState();
            chip.show(shown, GrabUi.kindOf(shown, sent));
            if (go != null) {
                loadingNext = true;
                web.loadUrl(go);
            } else if (following != null) {
                launchQueued(following);
            }
        });
    }

    // Chapters waiting to be grabbed one after another; queueRunning is true while the phone is working through them.
    private volatile String runUrl = "";
    private volatile boolean quickAfterLoad;
    private volatile boolean queueRunning;
    private volatile String runName = "";
    private static final long QUEUE_PAGE_LOAD_MS = 2 * 60 * 1000;
    private static final int MAX_CHAPTERS = GrabQueue.MAX_CHAPTERS;
    private static final String OPEN_FIRST = "Open a comic chapter first: enter its site address above and tap Go.";
    private static final int QUICK_READ_BATCH = 2;
    // Batches in flight at once: the PC reads two pages at a time, so one stream leaves it waiting on uploads.
    private static final int QUICK_READ_STREAMS = 3;
    private volatile String pageNext = "";
    private volatile String pageLanguage = "";
    private JSONArray pageChapters = new JSONArray();

    private static final long GRAB_TIMEOUT_MS = 9 * 60 * 1000;
    private volatile String runningGrab = "";
    // Names this grab's batches on the server so Cancel can stop the pages it has not started.
    private volatile String runId;
    private volatile BaiheClient activeClient;
    private volatile long grabStartedAt;
    private volatile boolean cancelRequested;
    // From the web view, not the page: a page can claim any address.
    private volatile String pageUrl = "";
    private volatile String pageAgent = "";
    private List<String> pageTitles = new ArrayList<>();

    // A site that keeps its comic canvases from being read gets its pages from what the web view
    // shows on screen: strips are cropped here, joined here, and filed as the page. Nothing leaves
    // the app except to the running grab, by its secret callback name.
    private final PageStrips<Bitmap> strips = new PageStrips<>();
    private boolean desktopAgent;

    @Override
    public void onSnap(String id, String key, double left, double top, double width, double height, double viewWidth) {
        final String run = runningGrab;
        runOnUiThread(() -> {
            if (web == null || run.isEmpty() || !grabbing || web.getWidth() <= 0 || web.getHeight() <= 0) {
                answerSnap(run, id, false);
                return;
            }
            int[] at = new int[2];
            web.getLocationInWindow(at);
            Bitmap shot = Bitmap.createBitmap(web.getWidth(), web.getHeight(), Bitmap.Config.ARGB_8888);
            android.graphics.Rect area = new android.graphics.Rect(at[0], at[1], at[0] + web.getWidth(), at[1] + web.getHeight());
            try {
                android.view.PixelCopy.request(getWindow(), area, shot, result -> {
                    boolean ok = false;
                    if (result == android.view.PixelCopy.SUCCESS) {
                        double scale = shot.getWidth() / viewWidth;
                        int x = Math.max(0, (int) Math.round(left * scale));
                        int y = Math.max(0, (int) Math.round(top * scale));
                        int w = Math.min(shot.getWidth() - x, (int) Math.round(width * scale));
                        int h = Math.min(shot.getHeight() - y, (int) Math.round(height * scale));
                        if (w > 0 && h > 0) {
                            // A grab that ended while the picture was taken must not leave a strip behind.
                            if (grabbing && run.equals(runningGrab)) {
                                strips.add(key, Bitmap.createBitmap(shot, x, y, w, h));
                                ok = true;
                            }
                        }
                    }
                    shot.recycle();
                    answerSnap(run, id, ok);
                }, new android.os.Handler(android.os.Looper.getMainLooper()));
            } catch (RuntimeException e) {
                shot.recycle();
                answerSnap(run, id, false);
            }
        });
    }

    @Override
    public void onStitch(String id, String key) {
        final String run = runningGrab;
        List<Bitmap> taken = strips.take(key);
        sender.execute(() -> {
            boolean ok = false;
            if (!taken.isEmpty() && grabbing && run.equals(runningGrab)) {
                int width = 0;
                int height = 0;
                for (Bitmap strip : taken) {
                    width = Math.max(width, strip.getWidth());
                    height += strip.getHeight();
                }
                Bitmap page = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888);
                android.graphics.Canvas canvas = new android.graphics.Canvas(page);
                int top = 0;
                for (Bitmap strip : taken) {
                    canvas.drawBitmap(strip, 0, top, null);
                    top += strip.getHeight();
                }
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                page.compress(Bitmap.CompressFormat.JPEG, 90, out);
                page.recycle();
                bridge.image(run, key, "", "image/jpeg", Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP));
                ok = true;
            }
            for (Bitmap strip : taken) strip.recycle();
            final boolean filed = ok;
            runOnUiThread(() -> answerSnap(run, id, filed));
        });
    }

    private void answerSnap(String run, String id, boolean ok) {
        if (web == null || run.isEmpty() || !run.equals(runningGrab)) return;
        String callback = "window['__baiheSnap" + run + "']";
        web.evaluateJavascript(callback + "&&" + callback + "('" + id + "'," + ok + ")", null);
    }

    @Override
    public void onLanguage(String language) {
        pageLanguage = language;
    }

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
        String language = pageLanguage;
        GrabBridge.PageMeta meta = bridge.meta();
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        uploading = true;
        boolean readOnly = quickRead;
        if (readOnly && chaptersLeft == 0) {
            int[] part = ServerAddress.partOf(titles);
            if (part != null && part[0] < part[1] && ServerAddress.nextChapterUrl(url, pageNext) != null) {
                chaptersLeft = part[1] - part[0] + 1;
                followingParts = true;
                partsContinuing = false;
            }
        }
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
                // A Quick read asks the PC to download first; the phone downloads only what it could not.
                if (image.needsDownload() && !(readOnly && PcFetch.mark(java.util.Collections.singletonList(image)) != null)) {
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
            activeClient = client;
            client.typesetMaxWidth = getResources().getDisplayMetrics().widthPixels;
            client.textStyle = textStyle(prefs());
            client.stats = new UploadStats();
            client.fetchUserAgent = agent;
            client.sourceLanguage = language;
            client.pageMeta = meta;
            if (readOnly) read(client, url, ready, bad, cut);
            else upload(client, url, titles, chapterLinks, ready, bad, cut);
        });
    }

    /** A page with no comic pictures but a chapter's prose: Quick read translates it into the text reader, Grab saves it. */
    @Override
    public void onText(String text) {
        String language = NovelText.languageOf(text);
        if (language.isEmpty()) {
            finishGrab("The page's text is not Chinese, Japanese or Korean, so there is nothing to translate.");
            return;
        }
        String server = prefs().getString(KEY_SERVER, "");
        String token = prefs().getString(KEY_TOKEN, "");
        uploading = true;
        BaiheClient client = new BaiheClient(server, token);
        client.pageMeta = bridge.meta();
        // Cancel cuts the request in flight; without this it waited out a translation the PC was still running.
        activeClient = client;
        if (!quickRead) {
            String url = pageUrl;
            List<String> titles = pageTitles;
            JSONArray chapterLinks = pageChapters;
            sender.execute(() -> saveNovel(client, url, titles, chapterLinks, text));
            return;
        }
        sender.execute(() -> readNovel(client, text, language));
    }

    /** Grab on a text chapter: its raw text is saved into a novel title, where the PC's novel tools translate it. */
    private void saveNovel(BaiheClient client, String url, List<String> titles, JSONArray chapterLinks, String text) {
        try {
            say("Saving the chapter");
            JSONObject title = client.resolveTitle(url, titles, chapterLinks, "novel");
            // The page's own heading (last of the candidates) names the chapter; the first is the browser tab title.
            String heading = client.pageMeta.chapterHeading(titles.isEmpty() ? "" : titles.get(titles.size() - 1));
            JSONObject answer = client.saveNovelChapter(title.getLong("drama_id"), url, heading, text);
            String where = title.optString("title", "Baihe");
            boolean added = answer.optBoolean("saved", false);
            finishGrab((added ? "Saved the chapter to " : "Already saved in ") + where, true);
        } catch (BaiheClient.BridgeException e) {
            finishGrab(cancelRequested ? "Cancelled." : e.getMessage());
        } catch (Exception e) {
            finishGrab("Could not save the chapter.");
        }
    }

    private void readNovel(BaiheClient client, String text, String language) {
        List<String> parts = NovelText.split(text, NovelText.PART_CHARS);
        NovelReadSession.begin(parts.size());
        NovelReadSession.onCancel(this::cancelGrab);
        runOnUiThread(() -> startActivity(new Intent(this, NovelReadActivity.class)));
        // Two parts at a time, shown in order: the next part is usually ready by the time the last is read.
        ExecutorService pool = Executors.newFixedThreadPool(2);
        List<java.util.concurrent.Future<String>> results = new ArrayList<>();
        for (String part : parts) results.add(pool.submit(() -> client.translateText(part, language, "en")));
        int done = 0;
        String stoppedBy = "";
        try {
            for (int i = 0; i < results.size(); i++) {
                if (cancelRequested || NovelReadSession.isStopped()) break;
                try {
                    NovelReadSession.add(results.get(i).get());
                    done++;
                } catch (java.util.concurrent.ExecutionException e) {
                    // An aborted request is the person's Cancel, not a part that failed.
                    if (cancelRequested || NovelReadSession.isStopped()) break;
                    Throwable cause = e.getCause();
                    String why = cause instanceof BaiheClient.BridgeException ? cause.getMessage() : "the part could not be translated";
                    NovelReadSession.add("(Part " + (i + 1) + " was not translated: " + why + ")");
                    // A token the server refused, or a spent limit, would refuse every later part too.
                    if (cause instanceof BaiheClient.BridgeException) {
                        int status = ((BaiheClient.BridgeException) cause).status;
                        if (status == 401 || status == 403 || status == 429) {
                            stoppedBy = why;
                            break;
                        }
                    }
                }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            pool.shutdownNow();
        }
        boolean cancelled = cancelRequested || NovelReadSession.isStopped();
        String closing = (cancelled ? "Cancelled. " : stoppedBy.isEmpty() ? "" : stoppedBy + " ")
                + "Translated " + done + " of " + parts.size() + " parts"
                + (!cancelled && stoppedBy.isEmpty() && done < parts.size() ? "; the ones that failed are marked." : ".");
        NovelReadSession.finish(closing);
        finishGrab(closing, !cancelled && stoppedBy.isEmpty() && done == parts.size());
    }

    /**
     * Quick read: send the pages without saving. Two batches are in flight at once so the PC is
     * not idle while the next one uploads, but pages are shown in page order: a later batch's
     * pages wait until the earlier ones are done. The reader opens on the first finished page and
     * the rest are added as the server streams them.
     */
    private void read(BaiheClient client, String url, List<PageImage> images, int unreadable, boolean truncated) {
        boolean continuing = partsContinuing;
        boolean moreParts = followingParts && chaptersLeft > 1;
        if (!continuing) {
            QuickReadSession.begin();
            QuickReadSession.setTitle(runName);
        }
        QuickReadSession.onCancel(this::cancelGrab);
        long readStarted = System.currentTimeMillis();
        java.util.concurrent.ExecutorService pool = Executors.newFixedThreadPool(QUICK_READ_STREAMS);
        try {
            // A typeset answer is large, so the batch stays small however many the server allows.
            JSONObject health = client.health();
            int max = Math.min(QUICK_READ_BATCH, health.optInt("max_images_per_request", 12));
            if (!continuing) QuickReadActivity.clear(this);
            // Clear non-pages stay home: on a slow link each one costs real time to upload.
            List<PageImage> pages = PageFilter.pagesOnly(images);
            List<List<PageImage>> batches = Batches.plan(pages, max, 1);
            int n = batches.size();
            Object lock = new Object();
            // A page's report text, found again by the very array that shows it.
            java.util.Map<byte[], String> metaOf = new java.util.IdentityHashMap<>();
            // The picture as the phone sent it, kept beside the redraw so a tap can show the original art.
            java.util.Map<byte[], byte[]> originalOf = new java.util.IdentityHashMap<>();
            List<List<byte[]>> held = new ArrayList<>();
            boolean[] finished = new boolean[n];
            for (int i = 0; i < n; i++) held.add(new ArrayList<>());
            int[] frontier = {0};
            int[] shown = {continuing ? QuickReadSession.snapshot().size() : 0};
            int shownBefore = shown[0];
            long[] firstPageAt = {0};
            boolean[] opened = {continuing};
            String[] firstError = {""};
            boolean[] refused = {false};
            // Cleared when the PC turns out not to download pictures (an older Baihe): bytes go up instead.
            // Older Baihe versions omit the flag, so they never get addresses to fetch.
            boolean[] pcFetch = {health.optBoolean("picture_addresses", false)};

            // Called with `lock` held: writes a page and, the first time, opens the reader.
            java.util.function.Consumer<byte[]> show = jpeg -> {
                String name = String.format(java.util.Locale.ROOT, "page-%03d.jpg", shown[0]);
                try (java.io.FileOutputStream out = new java.io.FileOutputStream(
                        new java.io.File(QuickReadActivity.quickReadDir(this), name))) {
                    out.write(jpeg);
                } catch (IOException e) {
                    return;
                }
                String meta = metaOf.remove(jpeg);
                if (meta != null) QuickReadActivity.writeReportText(this, name, meta);
                byte[] originalArt = originalOf.remove(jpeg);
                if (originalArt != null) QuickReadActivity.writeOriginal(this, name, originalArt);
                shown[0]++;
                QuickReadSession.add(name);
                say(QuickReadSession.isDetached() ? backgroundLine(shown[0])
                        : "Redrawing " + pages.size() + " pictures: " + shown[0] + " ready");
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
                    say(QuickReadSession.isDetached() ? backgroundLine(shown[0])
                            : "Redrawing " + pages.size() + " pictures: " + shown[0] + " ready");
                    try {
                        BaiheClient.PageListener onPage = page -> {
                            JSONObject wrapped;
                            try {
                                wrapped = new JSONObject().put("pages", new JSONArray().put(page));
                            } catch (org.json.JSONException e) {
                                return;
                            }
                            List<byte[]> jpegs = QuickRead.typesetPages(wrapped);
                            String meta = PageReport.meta(page, QuickRead.sourceUrl(batches.get(index), page.optString("key", "")),
                                    url, runName);
                            synchronized (lock) {
                                boolean redrawn = !jpegs.isEmpty();
                                if (jpegs.isEmpty()) {
                                    if (firstError[0].isEmpty()) firstError[0] = QuickRead.firstWarning(wrapped);
                                    byte[] original = QuickRead.original(batches.get(index), page.optString("key", ""));
                                    if (original == null) return;
                                    jpegs = java.util.Collections.singletonList(original);
                                }
                                byte[] originalArt = redrawn ? QuickRead.original(batches.get(index), page.optString("key", "")) : null;
                                for (byte[] jpeg : jpegs) {
                                    metaOf.put(jpeg, meta);
                                    if (originalArt != null) originalOf.put(jpeg, originalArt);
                                    if (index == frontier[0]) show.accept(jpeg);
                                    else held.get(index).add(jpeg);
                                }
                            }
                        };
                        JSONObject done = sendBatch(client, url, batches.get(index), onPage, pcFetch);
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
            int thisPart = shown[0] - shownBefore;
            // A part of a split chapter hands over to the next one with the reader still open.
            boolean carryOn = moreParts && !cancelRequested && !QuickReadSession.isAbandoned() && thisPart > 0;
            partsContinuing = carryOn;
            long ended = System.currentTimeMillis();
            String timing = firstPageAt[0] == 0 ? "" : String.format(java.util.Locale.ROOT,
                    " (found %d s, first page %d s, all %d s)", (readStarted - grabStartedAt) / 1000,
                    (firstPageAt[0] - readStarted) / 1000, (ended - readStarted) / 1000);
            String link = client.stats == null ? "" : client.stats.describe();
            if (!link.isEmpty()) timing = timing.isEmpty() ? " (" + link + ")" : timing.substring(0, timing.length() - 1) + "; " + link + ")";
            String message = cancelRequested && thisPart == 0 ? "Cancelled."
                    : (cancelRequested ? "Cancelled. " : "")
                    + QuickRead.summary(thisPart, pages.size() + unreadable, firstError[0])
                    + (truncated ? ". Some pages were left out." : "") + timing;
            if (!carryOn) QuickReadSession.finish(message);
            if (!carryOn) QuickReadSession.onCancel(null);
            // A refusal (a spent limit, a bad token) would refuse the next chapter too, so none is offered.
            String ahead = carryOn || cancelRequested || refused[0] || thisPart == 0 || queueRunning
                    || !prefs().getBoolean(KEY_READ_AHEAD, true) ? null : ServerAddress.nextChapterUrl(url, pageNext);
            finishGrab(message, carryOn);
            // After finishGrab, so a reader already at the end finds this grab over when it asks.
            if (ahead != null) QuickReadSession.offerNext(() -> runOnUiThread(() -> readAhead(ahead)));
        } catch (BaiheClient.BridgeException e) {
            QuickReadSession.onCancel(null);
            QuickReadSession.finish(e.getMessage());
            finishGrab(e.getMessage());
        } catch (Exception e) {
            QuickReadSession.onCancel(null);
            QuickReadSession.finish("Could not read the pages.");
            finishGrab("Could not read the pages.");
        } finally {
            pool.shutdown();
        }
    }

    /**
     * Fetches the chapter after the one being read, into the open reader. It goes through the
     * same hand-over as the parts of a split chapter, so the server's limits apply as they do
     * to any Quick read.
     */
    private void readAhead(String next) {
        if (grabbing || loadingNext || queueRunning || chaptersLeft > 0 || QuickReadSession.isAbandoned()) return;
        chaptersLeft = 1;
        followingParts = true;
        partsContinuing = true;
        QuickReadSession.onCancel(this::cancelGrab);
        QuickReadSession.resume();
        loadingNext = true;
        int load = ++aheadLoads;
        web.loadUrl(next);
        web.postDelayed(() -> {
            // A page that never finishes loading would leave the reader waiting for good.
            if (loadingNext && !grabbing && load == aheadLoads) cancelGrab();
        }, QUEUE_PAGE_LOAD_MS);
    }

    // Tells one read-ahead page load from a later one when its timeout fires. Main thread only.
    private int aheadLoads;

    /**
     * One request of a Quick read. The PC is asked to download the pictures first; any it could
     * not get (and a whole batch, when the PC is too old to know the field) go up as bytes.
     */
    private JSONObject sendBatch(BaiheClient client, String pageUrl, List<PageImage> batch,
                                 BaiheClient.PageListener onPage, boolean[] pcFetch) throws BaiheClient.BridgeException {
        if (pcFetch[0]) {
            List<PageImage> asked = PcFetch.mark(batch);
            if (asked != null) {
                try {
                    int[] delivered = {0};
                    JSONObject done = client.streamPages(pageUrl, asked, page -> {
                        delivered[0]++;
                        onPage.onPage(page);
                    });
                    // An older Baihe ignores fetch_url and finds an empty picture: nothing comes back and no
                    // "unfetched" list either. That is told apart from a stop by the user.
                    boolean stopped = cancelRequested || QuickReadSession.isAbandoned();
                    if (delivered[0] == 0 && !done.has("unfetched") && !stopped) {
                        pcFetch[0] = false;
                        return sendBytes(client, pageUrl, batch, onPage);
                    }
                    List<PageImage> again = PcFetch.unfetched(batch, done.optJSONArray("unfetched"));
                    if (again.isEmpty()) return done;
                    List<PageImage> bytes = withBytes(again, pageUrl);
                    return bytes.isEmpty() ? done : client.streamPages(pageUrl, ImageShrink.forUpload(bytes), onPage);
                } catch (BaiheClient.BridgeException e) {
                    if (e.status != 400 && e.status != 422) throw e;
                    pcFetch[0] = false;
                }
            }
        }
        return sendBytes(client, pageUrl, batch, onPage);
    }

    private JSONObject sendBytes(BaiheClient client, String pageUrl, List<PageImage> batch,
                                 BaiheClient.PageListener onPage) throws BaiheClient.BridgeException {
        List<PageImage> bytes = withBytes(batch, pageUrl);
        if (bytes.isEmpty()) throw new BaiheClient.BridgeException(0, "The site would not let Baihe read those pictures.");
        return client.streamPages(pageUrl, ImageShrink.forUpload(bytes), onPage);
    }

    /** The pictures with their bytes, downloading on the phone those that have only an address; unreadable ones are left out. */
    private List<PageImage> withBytes(List<PageImage> batch, String pageUrl) {
        List<PageImage> out = new ArrayList<>(batch.size());
        for (PageImage image : batch) {
            PageImage got = image.base64.isEmpty()
                    ? ImageFetch.download(image, pageUrl, android.webkit.CookieManager.getInstance()::getCookie, pageAgent)
                    : image;
            if (got != null) out.add(got);
        }
        return out;
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
