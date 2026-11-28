package app.baihe.shell;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Build;
import android.webkit.CookieManager;
import android.content.SharedPreferences;
import android.net.Uri;
import android.webkit.WebResourceRequest;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.security.SecureRandom;

/** Shows Baihe in a full-screen WebView and routes shared links to its Sources page. */
public class MainActivity extends Activity {
    private static final String PREFS = "baihe";
    private static final String PAGE_LOOK = "page_look";
    private static final String ADDRESS = "address";
    private static final String SIGN_IN_VERIFIER = "sign_in_verifier";
    private static final String SIGN_IN_STARTED = "sign_in_started";
    /** A page of Baihe (a hash route) a tapped alert opens. */
    static final String EXTRA_ROUTE = "route";
    private static final int ALERT_PERMISSION_REQUEST = 41;

    private WebView web;
    private ShellSettings.Bridge bridge;
    /** Covers the WebView when the page could not load, so Android's raw error page is never shown. */
    private View failureView;
    private TextView failureMessage;
    private Button failureRetry;
    private Button failureChange;
    private AlertDialog addressDialog;
    private boolean loadFailed;
    private int autoRetries;
    /** When the server last answered an update check, and whether the latest check got no answer (kept across launches). */
    private static final String UPDATE_ANSWERED_AT = "update_answered_at";
    private static final String UPDATE_CHECK_FAILED = "update_check_failed";
    /** The person was sent to Android's "Install unknown apps" setting; the update continues when they come back. */
    private static final String UPDATE_WAITS_FOR_ALLOW = "update_waits_for_allow";
    /** When this process last asked for a newer build; 0 before the first ask. */
    private long updateTriedAt;
    private boolean updateDownloading;
    private long declinedVersion;
    private boolean updateOffered;
    private View customView;
    /** The Studio | Grab strip, hidden while the app is a small floating window. */
    private View strip;
    /** The comic reader asked for the whole screen; kept so a rebuilt strip stays hidden. */
    private boolean barsHidden;
    /** A Live session's picture is on screen, so leaving the app should float it (picture-in-picture). */
    private boolean liveWatching;
    private WebChromeClient.CustomViewCallback customViewCallback;

    @Override
    protected void onCreate(Bundle state) {
        setTheme(GrabUi.Palette.saved(this).themeRes());
        super.onCreate(state);
        String crash = CrashLog.takePending(this);
        if (crash == null) crash = CrashLog.takeCaught(this);
        if (crash != null) {
            showCrash(crash);
            return;
        }
        startUp();
    }

    /**
     * After a crash the trace is shown before anything else runs, so a start-up crash that repeats
     * can still be read, copied and sent instead of the app just closing again.
     */
    private void showCrash(String trace) {
        TextView text = new TextView(this);
        text.setText("Baihe hit a problem. Copy this and paste it into Report a problem "
                + "(Grab > More), or send it to whoever runs your Baihe, so it can be fixed:\n\n" + trace);
        text.setTextIsSelectable(true);
        text.setPadding(32, 32, 32, 32);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(text);
        Button again = new Button(this);
        again.setText("Open Baihe");
        again.setOnClickListener(v -> startUp());
        Button copy = new Button(this);
        copy.setText("Copy");
        copy.setOnClickListener(v -> ((android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE))
                .setPrimaryClip(android.content.ClipData.newPlainText("Baihe crash", trace)));
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setFitsSystemWindows(true);
        root.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        root.addView(copy);
        root.addView(again);
        setContentView(root);
    }

    private void startUp() {
        clearInstalledUpdate();
        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        // The shell has no menu of its own: Baihe's Settings and Diagnostics read this build and its
        // date from here and show them beside Baihe's version.
        s.setUserAgentString(s.getUserAgentString() + " BaiheAndroid/" + BuildConfig.VERSION_CODE
                + " (" + BuildConfig.BUILD_DATE + ")");
        // Only Baihe's own origin loads here. Google blocks sign-in inside an embedded WebView and
        // credentials must not be typed into a view this app controls, so Google addresses
        // and sign-in redirects open in the system browser; other sites open in Baihe Grab.
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                // A page that reloads while its reader hid the strip never runs the cleanup that shows it again.
                barsHidden = false;
                showStrip();
                loadFailed = false;
                // The page that was playing is gone, and its cleanup never ran.
                MediaPlayback.report(MainActivity.this, null);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, android.webkit.WebResourceError error) {
                if (!LoadFailure.coversPage(request.isForMainFrame(), error.getErrorCode())) return;
                loadFailed = true;
                showLoadFailure(request.getUrl().toString());
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                if (loadFailed) return;
                autoRetries = 0;
                if (failureView != null) failureView.setVisibility(View.GONE);
                syncSepiaFromPage();
                // A fresh load has not re-announced a Live picture yet, and the old page's cleanup never ran.
                setLiveWatching(false);
                String address = savedAddress();
                if (address != null && ServerAddress.sameOrigin(address, url)) {
                    // evaluateJavascript runs in the top page only, so no iframe ever sees the key.
                    view.evaluateJavascript("window.__baiheSaveKey=" + org.json.JSONObject.quote(bridge.newSaveKey())
                            + ";window.dispatchEvent(new Event('baihekey'))", null);
                    checkForUpdate(address);
                }
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                // Live's Twitch and YouTube players are iframes on other sites; only a page the user
                // navigates to should leave Baihe, or the player's own loads would open Grab.
                if (!request.isForMainFrame()) return false;
                String url = request.getUrl().toString();
                String address = savedAddress();
                if (address != null && AppSignIn.isLoginStart(address, url)) {
                    startSignIn(address);
                    return true;
                }
                if (address != null && ServerAddress.sameOrigin(address, url)) return false;
                String scheme = request.getUrl().getScheme();
                if ("baihegrab".equals(scheme)) {
                    String quickRead = ShareLink.quickReadLink(url);
                    if (quickRead != null) {
                        startActivity(new Intent(MainActivity.this, GrabActivity.class)
                                .putExtra(GrabActivity.EXTRA_COMMAND, "read")
                                .putExtra(GrabActivity.EXTRA_URL, quickRead));
                    } else {
                        ShellTabs.show(MainActivity.this, GrabActivity.class);
                    }
                    return true;
                }
                if ("http".equals(scheme) || "https".equals(scheme)) {
                    try {
                        if (ServerAddress.opensInGrab(url)) {
                            // Grab's browser can grab the pages once the site is confirmed.
                            startActivity(new Intent(MainActivity.this, GrabActivity.class)
                                    .setAction(Intent.ACTION_SEND).setType("text/plain")
                                    .putExtra(Intent.EXTRA_TEXT, url));
                            return true;
                        }
                        startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
                    } catch (RuntimeException e) {
                        // No browser installed: nothing safe to do but stay on the current page.
                    }
                }
                return true;
            }
        });
        // A WebView drops downloads unless told what to do with them; Baihe's "Save to phone" needs this.
        web.setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) ->
                SaveDownload.enqueue(this, savedAddress(), url, userAgent, contentDisposition, mimeType));
        // Without these two callbacks WebView silently refuses requestFullscreen() and the
        // player's full-screen button, which Chrome handles itself.
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onShowCustomView(View view, CustomViewCallback callback) {
                if (customView != null) {
                    callback.onCustomViewHidden();
                    return;
                }
                customView = view;
                customViewCallback = callback;
                setContentView(view);
                hideSystemBars(view);
                // Turned sideways for the video and back on exit, whatever the page asks for, so the
                // picture fills the screen instead of sitting small above the captions.
                setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);
            }

            @Override
            public void onHideCustomView() {
                exitCustomView();
            }
        });
        // Baihe's own pages read and set the phone app's switches (Settings > Phone app). Only Baihe's
        // address loads as the page, but the bridge also reaches its iframes, so saveFile needs a key.
        bridge = new ShellSettings.Bridge(this, new ShellSettings.Host() {
            @Override
            public void alertsSwitchedOn() {
                askForAlertPermission();
            }

            @Override
            public void liveWatching(boolean watching) {
                runOnUiThread(() -> setLiveWatching(watching));
            }

            @Override
            public void popOut() {
                // Pressed on purpose, so the "when I leave the app" switch does not apply.
                runOnUiThread(() -> enterPip(true));
            }

            @Override
            public void themeChanged(String look) {
                runOnUiThread(() -> {
                    followPageTheme(look);
                    // An older Baihe page only says dark or light, so sepia is read from the page itself.
                    if (web != null) web.postDelayed(MainActivity.this::syncSepiaFromPage, 300);
                });
            }

            @Override
            public void checkForUpdates() {
                runOnUiThread(() -> checkForUpdateNow());
            }

            @Override
            public void changeAddress() {
                runOnUiThread(() -> askAddress(new Intent(), savedAddress(), null));
            }

            @Override
            public void setBarsHidden(boolean hidden) {
                runOnUiThread(() -> {
                    barsHidden = hidden;
                    showStrip();
                });
            }
            @Override
            public void mediaState(String json) {
                MediaState state = MediaState.parse(json);
                if (state != null) runOnUiThread(() -> MediaPlayback.report(MainActivity.this, state));
            }
        });
        web.addJavascriptInterface(bridge, "BaiheShell");
        MediaPlayback.attach(mediaPage);
        setContentView(page());
        open(getIntent());
        try {
            AlertSchedule.ensure(this);
        } catch (RuntimeException e) {
            // Some phones refuse the job; alerts then stay off but the app must still open.
            CrashLog.noteCaught(this, "Alert job could not be scheduled", e);
        }
        if (Build.VERSION.SDK_INT >= 33 && !prefs().getBoolean("alerts_asked", false)) {
            prefs().edit().putBoolean("alerts_asked", true).apply();
            askForAlertPermission();
        }
    }

    /** Android 13+ shows notifications only after the person allows them; asked again whenever they switch alerts on. */
    private void askForAlertPermission() {
        if (Build.VERSION.SDK_INT < 33) return;
        if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return;
        runOnUiThread(() -> requestPermissions(new String[] {android.Manifest.permission.POST_NOTIFICATIONS}, ALERT_PERMISSION_REQUEST));
    }

    /** The Studio | Grab strip above Baihe; rebuilt after a full-screen video because setContentView drops it. */
    private View page() {
        GrabUi.Palette palette = palette();
        getWindow().setStatusBarColor(palette.surface);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setFitsSystemWindows(true);
        root.setBackgroundColor(palette.surface);
        strip = ShellTabs.strip(this, palette, false);
        // An address that answers with some other site never reaches the failure screen's button.
        ((ViewGroup) strip).getChildAt(0).setOnLongClickListener(v -> {
            askAddress(new Intent(), savedAddress(), null);
            return true;
        });
        root.addView(strip, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        showStrip();
        ViewGroup parent = (ViewGroup) web.getParent();
        if (parent != null) parent.removeView(web);
        // A WebView paints black until its first page does; the app's own colour hides that flash.
        web.setBackgroundColor(palette.surface);
        android.widget.FrameLayout body = new android.widget.FrameLayout(this);
        body.addView(web, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        failureView = failureScreen(palette);
        body.addView(failureView, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(body, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        return root;
    }

    private View failureScreen(GrabUi.Palette palette) {
        TextView message = new TextView(this);
        failureMessage = message;
        message.setTextColor(palette.text);
        message.setTextSize(16);
        message.setGravity(android.view.Gravity.CENTER);
        Button retry = new Button(this);
        failureRetry = retry;
        retry.setText("Retry");
        retry.setOnClickListener(v -> {
            autoRetries = 0;
            retryLoad();
        });
        // A typo or a moved PC would otherwise leave clearing the app's storage as the only way out.
        Button change = new Button(this);
        failureChange = change;
        change.setText("Change address");
        change.setOnClickListener(v -> askAddress(new Intent(), savedAddress(), null));
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(android.view.Gravity.CENTER);
        box.setPadding(48, 48, 48, 48);
        box.setBackgroundColor(palette.surface);
        box.addView(message);
        box.addView(retry);
        box.addView(change);
        // Opaque and tappable so the WebView's own error page behind it can't be seen or touched.
        box.setClickable(true);
        box.setVisibility(View.GONE);
        return box;
    }

    /** Covers the error page at once, then tries again quietly a couple of times before leaving it to the person. */
    private void showLoadFailure(String url) {
        if (failureView == null) return;
        failureView.setVisibility(View.VISIBLE);
        long delay = LoadFailure.retryDelayMs(autoRetries);
        // While quiet retries remain it reads as a wait, not a failure: the PC may just be busy.
        String host = Uri.parse(String.valueOf(savedAddress())).getHost();
        failureMessage.setText(delay < 0
                ? "Can't reach Baihe" + (host == null ? "" : " at " + host)
                        + " right now.\nYour PC may be busy or restarting, or its address may have changed."
                : "Connecting to Baihe…\nThe PC is taking longer than usual.");
        failureRetry.setVisibility(delay < 0 ? View.VISIBLE : View.GONE);
        // Shown during the quiet retries too: a mistyped address would otherwise wait them all out first.
        failureChange.setVisibility(View.VISIBLE);
        if (delay < 0) return;
        autoRetries++;
        web.postDelayed(() -> {
            if (loadFailed && !isFinishing() && !isDestroyed()) retryLoad();
        }, delay);
    }

    private void retryLoad() {
        String address = savedAddress();
        if (address == null || web == null) return;
        loadFailed = false;
        // The failed address may be an alert route or a shared link; the web view still holds it.
        String failed = web.getUrl();
        web.loadUrl(failed == null || failed.startsWith("data:") || !ServerAddress.sameOrigin(address, failed)
                ? address : failed);
    }

    /** The page's own theme once it has said, so a light Baihe on a dark phone (or the reverse) gets a matching bar. */
    private GrabUi.Palette palette() {
        return GrabUi.Palette.saved(this);
    }

    /**
     * The server may still serve a Baihe page that predates setLook and reports only dark or light;
     * the page's own theme attribute tells the bar when that look is sepia.
     */
    private void syncSepiaFromPage() {
        if (web == null) return;
        web.evaluateJavascript("document.documentElement.getAttribute('data-theme')", value -> {
            if ("\"sepia\"".equals(value)) followPageTheme("sepia");
        });
    }

    private void followPageTheme(String look) {
        if (!java.util.Arrays.asList("light", "dark", "sepia", "oled").contains(look)) return;
        SharedPreferences p = prefs();
        if (look.equals(p.getString(PAGE_LOOK, null))) return;
        p.edit().putString(PAGE_LOOK, look).apply();
        // Dialogs opened from now on take the new look's colours; one already on screen keeps its own.
        getTheme().applyStyle(GrabUi.Palette.saved(this).themeRes(), true);
        // While a video is full screen the strip is not on screen; page() reads the saved value on the way back.
        if (customView != null || web == null) return;
        setContentView(page());
    }

    /** The strip shows unless the app is a small floating window or the reader asked for the whole screen. */
    private void showStrip() {
        if (strip == null) return;
        boolean floating = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && isInPictureInPictureMode();
        strip.setVisibility(floating || barsHidden ? View.GONE : View.VISIBLE);
    }

    private boolean pipAllowed() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && ShellSettings.isOn(this, ShellSettings.PIP);
    }

    private void setLiveWatching(boolean watching) {
        liveWatching = watching;
        // A player that has just appeared can leave the page wider than the screen until the next layout pass.
        if (watching && web != null) web.postDelayed(this::refreshAfterResize, 600);
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return;
        // From Android 12 the system floats the app by itself on the home gesture; older ones use onUserLeaveHint.
        try {
            setPictureInPictureParams(pipParams(watching && pipAllowed()));
        } catch (RuntimeException ignored) {
            // The device has no picture-in-picture: the app just leaves as before.
        }
    }

    @android.annotation.TargetApi(Build.VERSION_CODES.O)
    private android.app.PictureInPictureParams pipParams(boolean autoEnter) {
        android.app.PictureInPictureParams.Builder builder = new android.app.PictureInPictureParams.Builder()
                .setAspectRatio(new android.util.Rational(16, 9));
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) builder.setAutoEnterEnabled(autoEnter);
        return builder.build();
    }

    private void enterPip(boolean asked) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O || isInPictureInPictureMode()) return;
        if (!asked && !pipAllowed()) return;
        try {
            enterPictureInPictureMode(pipParams(false));
        } catch (RuntimeException ignored) {
            // Refused (picture-in-picture is off for this app in Android's settings): nothing to float.
        }
    }

    @Override
    protected void onUserLeaveHint() {
        super.onUserLeaveHint();
        if (liveWatching && Build.VERSION.SDK_INT < Build.VERSION_CODES.S) enterPip(false);
    }

    /** The page hides everything but the picture and its captions while the window is this small. */
    @Override
    public void onPictureInPictureModeChanged(boolean inPip, android.content.res.Configuration config) {
        super.onPictureInPictureModeChanged(inPip, config);
        if (strip != null) strip.setVisibility(inPip || barsHidden ? View.GONE : View.VISIBLE);
        if (web == null) return;
        web.evaluateJavascript("document.documentElement.classList.toggle('shell-pip'," + inPip + ")", null);
        // The window animates between sizes, so the WebView can be left laid out for the small one (page zoomed
        // in after returning, black picture): ask for the layout and the page's viewport again once it settles.
        refreshAfterResize();
        web.postDelayed(this::refreshAfterResize, 350);
    }

    private void refreshAfterResize() {
        if (web == null) return;
        web.requestLayout();
        web.invalidate();
        // The viewport is only re-applied when the page really is zoomed or wider than the screen, so a
        // normal return to the app never moves the person's scroll position.
        web.evaluateJavascript("(function(){var v=window.visualViewport,"
                + "bad=(v&&Math.abs(v.scale-1)>0.01)||document.documentElement.scrollWidth>window.innerWidth+1;"
                + "if(!bad)return;var m=document.querySelector('meta[name=viewport]');"
                + "if(m){var c=m.content;m.content='';m.content=c;}"
                + "window.dispatchEvent(new Event('resize'));})()", null);
    }

    /** The clipboard can only be read while this window has focus (Android 10+). */
    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (!hasFocus || web == null || customView != null || savedAddress() == null) return;
        String link = ClipboardOffer.pending(this);
        if (link != null) offerClipboardLink(link);
    }

    private void offerClipboardLink(String link) {
        String address = savedAddress();
        if (address == null || isFinishing() || isDestroyed()) return;
        boolean video = LinkKind.of(link) == LinkKind.Kind.VIDEO;
        new AlertDialog.Builder(this)
                .setTitle(video ? "Subtitle this?" : "Read this?")
                .setMessage(LinkKind.host(link))
                .setPositiveButton(video ? "Subtitle" : "Quick read", (d, w) -> {
                    if (video) {
                        web.loadUrl(ShareLink.pageFor(address, link));
                    } else {
                        startActivity(new Intent(this, GrabActivity.class)
                                .putExtra(GrabActivity.EXTRA_COMMAND, "read")
                                .putExtra(GrabActivity.EXTRA_URL, link));
                    }
                })
                .setNegativeButton("Not now", null)
                .show();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        // A tap on the playback controls only brings the app back; opening the address again would stop the sound.
        if (MediaPlayback.SHOW_PLAYER.equals(intent.getAction())) return;
        if (web != null) open(intent);
    }

    @Override
    public void onBackPressed() {
        if (customView != null) exitCustomView();
        else if (web != null && web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @SuppressWarnings("deprecation")
    private void hideSystemBars(View view) {
        // The flag form works on API 26; WindowInsetsController needs 30.
        view.setSystemUiVisibility(View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE);
    }

    private void exitCustomView() {
        if (customView == null) return;
        View view = customView;
        WebChromeClient.CustomViewCallback callback = customViewCallback;
        customView = null;
        customViewCallback = null;
        ViewGroup parent = (ViewGroup) view.getParent();
        if (parent != null) parent.removeView(view);
        setContentView(page());
        setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED);
        web.setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
        callback.onCustomViewHidden();
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
        String route = intent.getStringExtra(EXTRA_ROUTE);
        if (AlertFeed.safeRoute(route)) {
            web.loadUrl(address.replaceAll("/+$", "") + "/" + route);
            intent.removeExtra(EXTRA_ROUTE);
            return;
        }
        String link = Intent.ACTION_SEND.equals(intent.getAction())
                ? ShareLink.firstLink(intent.getStringExtra(Intent.EXTRA_TEXT))
                : null;
        web.loadUrl(link == null ? address : ShareLink.pageFor(address, link));
    }

    private void askAddress(Intent intent) {
        askAddress(intent, null, null);
    }

    /** First run has no address to keep, so only a change (current != null) can be cancelled. */
    private void askAddress(Intent intent, String current, String typed) {
        EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        input.setHint("https://");
        input.setSingleLine(true);
        String shown = typed != null ? typed : current;
        if (shown != null) input.setText(shown);
        AlertDialog.Builder dialog = new AlertDialog.Builder(this)
                .setTitle("Baihe address")
                .setView(input)
                .setCancelable(current != null)
                .setPositiveButton("OK", (d, w) -> {
                    String text = input.getText().toString();
                    String a = ServerAddress.shellAddress(text);
                    if (a == null) {
                        askAddress(intent, current, text);
                        return;
                    }
                    if (!a.equals(current)) {
                        prefs().edit().putString(ADDRESS, a).apply();
                        // Another PC's alert list starts from other numbers, so old marks would misread it.
                        AlertPoller.forget(prefs());
                        // Another PC may hold a newer build than the one that last answered.
                        prefs().edit().remove(UPDATE_ANSWERED_AT).remove(UPDATE_CHECK_FAILED).apply();
                        updateTriedAt = 0;
                    }
                    autoRetries = 0;
                    loadFailed = false;
                    if (failureView != null) failureView.setVisibility(View.GONE);
                    open(intent);
                });
        if (typed != null) {
            dialog.setMessage("That isn't a Baihe address. Use https://, for example https://studio.example.com "
                    + "(http:// only for an address on your home network).");
        }
        if (current != null) dialog.setNegativeButton("Cancel", null);
        // A second tap or long-press would otherwise stack another dialog on top.
        if (addressDialog != null && addressDialog.isShowing()) addressDialog.dismiss();
        addressDialog = dialog.show();
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

    /**
     * After Baihe has loaded (so the session cookie exists), on each return to the app and hourly while it
     * stays open: asks the server whether it holds a newer build, when {@link AppUpdate#checkDue} says a
     * check is due. A server that offers none, or a signed-out one, is silent.
     */
    private void checkForUpdate(String address) {
        long now = System.currentTimeMillis();
        SharedPreferences p = prefs();
        if (updateOffered || !AppUpdate.checkDue(now, p.getLong(UPDATE_ANSWERED_AT, 0),
                p.getBoolean(UPDATE_CHECK_FAILED, false), updateTriedAt)) return;
        updateTriedAt = now;
        String cookie = CookieManager.getInstance().getCookie(address);
        new Thread(() -> {
            // The PC only looks at GitHub when asked, so an unforced ask would hear the old build first.
            long offered = AppUpdate.check(address, cookie, true);
            if (!stillAddress(address)) return;
            noteCheck(offered);
            if (offered <= 0 || !AppUpdate.isNewer(installedVersion(), offered) || offered == declinedVersion) return;
            runOnUiThread(() -> offerUpdate(address, cookie, offered));
        }).start();
    }

    /** A check that was asked of a PC the person has since moved away from says nothing about the current one. */
    private boolean stillAddress(String address) {
        return address.equals(prefs().getString(ADDRESS, null));
    }

    private void noteCheck(long offered) {
        SharedPreferences.Editor e = prefs().edit().putBoolean(UPDATE_CHECK_FAILED, offered == AppUpdate.CHECK_FAILED);
        if (offered != AppUpdate.CHECK_FAILED) e.putLong(UPDATE_ANSWERED_AT, System.currentTimeMillis());
        e.apply();
    }

    /** The Check for updates button: asks right away, and answers either way instead of staying silent. */
    private void checkForUpdateNow() {
        String address = savedAddress();
        if (address == null) {
            toast("Set your PC's address first, then check for updates.");
            return;
        }
        String cookie = CookieManager.getInstance().getCookie(address);
        toast("Checking for updates…");
        new Thread(() -> {
            long offered = AppUpdate.check(address, cookie, true);
            if (!stillAddress(address)) return;
            noteCheck(offered);
            if (offered == AppUpdate.CHECK_FAILED) {
                runOnUiThread(() -> toast("Could not reach Baihe to check. Try again in a minute."));
            } else if (offered <= 0 || !AppUpdate.isNewer(installedVersion(), offered)) {
                runOnUiThread(() -> toast("This app is up to date."));
            } else {
                runOnUiThread(() -> { if (!updateOffered) offerUpdate(address, cookie, offered); });
            }
        }).start();
    }

    private void toast(String text) {
        android.widget.Toast.makeText(this, text, android.widget.Toast.LENGTH_LONG).show();
    }

    // getLongVersionCode needs API 28 and this app supports 26; versionCode is an int below 2^31 here.
    @SuppressWarnings("deprecation")
    private long installedVersion() {
        try {
            PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            return info.versionCode;
        } catch (Exception e) {
            // Unknown installed build: report the newest so no update is offered by mistake.
            return Long.MAX_VALUE;
        }
    }

    /** The build of a saved APK as Android reads it, or 0 when it is missing or not an APK. */
    @SuppressWarnings("deprecation")
    private long apkVersion(File apk) {
        if (!apk.isFile()) return 0;
        PackageInfo info = getPackageManager().getPackageArchiveInfo(apk.getPath(), 0);
        return info == null ? 0 : info.versionCode;
    }

    /** Why the saved APK must not be installed, or null when it is a newer build of this app. */
    @SuppressWarnings("deprecation")
    private String apkProblem(File apk) {
        PackageInfo info = apk.isFile() ? getPackageManager().getPackageArchiveInfo(apk.getPath(), 0) : null;
        return AppUpdate.apkProblem(getPackageName(), info == null ? null : info.packageName,
                info == null ? 0 : info.versionCode, installedVersion());
    }

    private void offerUpdate(String address, String cookie, long offered) {
        if (isFinishing() || isDestroyed()) return;
        updateOffered = true;
        new AlertDialog.Builder(this)
                .setTitle("Update Available")
                .setMessage("A newer Baihe app is on your PC. It downloads from there, then Android asks you to install it.")
                .setPositiveButton("Install update", (d, w) -> downloadAndInstall(address, cookie, offered))
                // Asking again at every return to the app would nag; the Check for updates button still offers it.
                .setNegativeButton(android.R.string.cancel, (d, w) -> declinedVersion = offered)
                .setOnDismissListener(d -> updateOffered = false)
                .show();
    }

    /** Install update: downloads (or reuses a saved copy of the same build), checks the file, then installs. */
    private void downloadAndInstall(String address, String cookie, long offered) {
        // A second press would write into the file the first download is still filling.
        if (updateDownloading) {
            toast("The update is already downloading.");
            return;
        }
        updateDownloading = true;
        toast("Downloading the update…");
        new Thread(() -> {
            File apk = AppUpdate.savedApk(this);
            String problem = null;
            if (!AppUpdate.reuseSavedApk(apkVersion(apk), offered)) {
                apk.delete();
                problem = AppUpdate.download(address, cookie, apk);
            }
            if (problem == null) problem = apkProblem(apk);
            if (problem != null) apk.delete();
            String failed = problem;
            runOnUiThread(() -> {
                updateDownloading = false;
                if (failed != null) updateFailed(failed);
                else installOrAskToAllow(apk);
            });
        }).start();
    }

    /**
     * Android refuses an install from an app the person has not allowed under "Install unknown apps", and its
     * own prompt for that can drop the install on the way back, so Baihe sends the person to the setting
     * itself and continues from onResume.
     */
    private void installOrAskToAllow(File apk) {
        if (isFinishing() || isDestroyed()) {
            // The file is kept, and Install update reuses it while the PC still offers this build.
            InstallResult.tell(getApplicationContext(),
                    "Update downloaded. Open Baihe and press Check for updates in Settings > Phone app to install it.");
            return;
        }
        if (getPackageManager().canRequestPackageInstalls()) {
            install(apk);
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle("Allow Baihe to install its update")
                .setMessage("Android asks you once to allow app installs from Baihe. On the next screen turn on "
                        + "\"Allow from this source\", then come back: the update carries on.")
                .setPositiveButton("Open setting", (d, w) -> {
                    prefs().edit().putBoolean(UPDATE_WAITS_FOR_ALLOW, true).apply();
                    try {
                        startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:" + getPackageName())));
                    } catch (RuntimeException e) {
                        prefs().edit().remove(UPDATE_WAITS_FOR_ALLOW).apply();
                        updateFailed("This phone did not open the setting. In Android's Settings, open Apps > Special app "
                                + "access > Install unknown apps > Baihe, turn it on, then press Check for updates again.");
                    }
                })
                .setNegativeButton(android.R.string.cancel, (d, w) -> toast(
                        "The update was not installed. Check for updates in Settings > Phone app offers it again."))
                .show();
    }

    /** Back from the "Install unknown apps" setting: installs the saved update, or says why it can't. */
    private void resumeAfterAllow() {
        if (!prefs().getBoolean(UPDATE_WAITS_FOR_ALLOW, false)) return;
        prefs().edit().remove(UPDATE_WAITS_FOR_ALLOW).apply();
        if (!getPackageManager().canRequestPackageInstalls()) {
            updateFailed("Baihe still isn't allowed to install apps, so the update was not installed. "
                    + "Press Check for updates in Settings > Phone app to try again.");
            return;
        }
        new Thread(() -> {
            File apk = AppUpdate.savedApk(this);
            String problem = apkProblem(apk);
            if (problem != null) {
                apk.delete();
                runOnUiThread(() -> updateFailed(problem));
            } else {
                runOnUiThread(() -> install(apk));
            }
        }).start();
    }

    private void install(File apk) {
        new Thread(() -> {
            String reason = AppUpdate.install(getApplicationContext(), apk);
            if (reason != null) {
                runOnUiThread(() -> updateFailed("Android would not start installing the update (" + reason + "). "
                        + "Press Check for updates in Settings > Phone app to try again."));
            }
        }).start();
    }

    private void updateFailed(String text) {
        toast(text);
        if (isFinishing() || isDestroyed()) return;
        new AlertDialog.Builder(this)
                .setTitle("Update not installed")
                .setMessage(text)
                .setPositiveButton(android.R.string.ok, null)
                .show();
    }

    /** Once this app is on the saved APK's build or newer, the file has served its purpose. */
    private void clearInstalledUpdate() {
        new Thread(() -> {
            File apk = AppUpdate.savedApk(this);
            if (apk.isFile() && AppUpdate.savedApkDone(apkVersion(apk), installedVersion())) apk.delete();
        }).start();
    }

    /** Hourly while the app is in front; each return to the app also asks, from onResume. */
    private final Runnable poll = new Runnable() {
        @Override
        public void run() {
            String address = savedAddress();
            String shown = web.getUrl();
            if (address != null && shown != null && ServerAddress.sameOrigin(address, shown)) checkForUpdate(address);
            web.postDelayed(this, AppUpdate.POLL_EVERY_MS);
        }
    };

    @Override
    protected void onResume() {
        super.onResume();
        if (web == null) return;
        // Coming back to the front is what cleared a stale layout by hand; do it without the person having to.
        refreshAfterResize();
        // A start Android refused while the app was in the background can go through now that it is in front.
        MediaPlayback.retryIfRefused(this);
        resumeAfterAllow();
        web.removeCallbacks(poll);
        poll.run();
    }

    // Only the update poll stops here: pausing the WebView or its timers would stop the Watch page's sound
    // on the lock screen.
    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) web.removeCallbacks(poll);
    }

    @Override
    protected void onDestroy() {
        MediaPlayback.detach(mediaPage);
        // The WebView is not destroyed with the activity, so a player still going would play on with no controls.
        mediaPage.send(MediaState.PAUSE, 0);
        super.onDestroy();
    }

    /** Hands a press on the lock screen, notification or headset to the Watch page. */
    private final MediaPlayback.Page mediaPage = (action, value) -> {
        String script = MediaState.pageScript(action, value);
        if (web != null && script != null) web.evaluateJavascript(script, null);
    };

    @Override
    protected void onStart() {
        super.onStart();
        // An update notice posted while the app was away has served its purpose once the app is in front.
        InstallResult.clear(this);
        AlertSchedule.enteredApp(this);
    }

    @Override
    protected void onStop() {
        super.onStop();
        AlertSchedule.leftApp(this);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }
}
