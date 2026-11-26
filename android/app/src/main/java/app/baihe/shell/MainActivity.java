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
    private static final String ADDRESS = "address";
    private static final String SIGN_IN_VERIFIER = "sign_in_verifier";
    private static final String SIGN_IN_STARTED = "sign_in_started";
    /** A page of Baihe (a hash route) a tapped alert opens. */
    static final String EXTRA_ROUTE = "route";
    private static final int ALERT_PERMISSION_REQUEST = 41;

    private WebView web;
    /** When the server was last asked for a newer build; 0 before the first ask. */
    private static final long UPDATE_RECHECK_MS = 60_000;
    /** While the app stays open, a build the PC fetches later is still noticed. */
    private static final long UPDATE_POLL_MS = 5 * 60_000;
    private long updateCheckedAt;
    private boolean updateOffered;
    private View customView;
    /** The Studio | Grab strip, hidden while the app is a small floating window. */
    private View strip;
    /** A Live session's picture is on screen, so leaving the app should float it (picture-in-picture). */
    private boolean liveWatching;
    private WebChromeClient.CustomViewCallback customViewCallback;

    @Override
    protected void onCreate(Bundle state) {
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
        text.setText("Baihe hit a problem. Copy this and send it so it can be fixed:\n\n" + trace);
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
            public void onPageFinished(WebView view, String url) {
                // A fresh load has not re-announced a Live picture yet, and the old page's cleanup never ran.
                setLiveWatching(false);
                String address = savedAddress();
                if (address != null && ServerAddress.sameOrigin(address, url)) checkForUpdate(address);
            }

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
        // address loads in this WebView, so no other site can reach it.
        web.addJavascriptInterface(new ShellSettings.Bridge(this, new ShellSettings.Host() {
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
                runOnUiThread(() -> enterPip());
            }

            @Override
            public void checkForUpdates() {
                runOnUiThread(() -> checkForUpdateNow());
            }
        }), "BaiheShell");
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
        GrabUi.Palette palette = GrabUi.Palette.of(this);
        getWindow().setStatusBarColor(palette.surface);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setFitsSystemWindows(true);
        root.setBackgroundColor(palette.surface);
        strip = ShellTabs.strip(this, palette, false);
        root.addView(strip, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        ViewGroup parent = (ViewGroup) web.getParent();
        if (parent != null) parent.removeView(web);
        root.addView(web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        return root;
    }

    private boolean pipAllowed() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && ShellSettings.isOn(this, ShellSettings.PIP);
    }

    private void setLiveWatching(boolean watching) {
        liveWatching = watching;
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

    private void enterPip() {
        if (!pipAllowed() || isInPictureInPictureMode()) return;
        try {
            enterPictureInPictureMode(pipParams(false));
        } catch (RuntimeException ignored) {
            // Refused (picture-in-picture is off for this app in Android's settings): nothing to float.
        }
    }

    @Override
    protected void onUserLeaveHint() {
        super.onUserLeaveHint();
        if (liveWatching && Build.VERSION.SDK_INT < Build.VERSION_CODES.S) enterPip();
    }

    /** The page hides everything but the picture and its captions while the window is this small. */
    @Override
    public void onPictureInPictureModeChanged(boolean inPip, android.content.res.Configuration config) {
        super.onPictureInPictureModeChanged(inPip, config);
        if (strip != null) strip.setVisibility(inPip ? View.GONE : View.VISIBLE);
        if (web == null) return;
        web.evaluateJavascript("document.documentElement.classList.toggle('shell-pip'," + inPip + ")", null);
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

    /**
     * Once per launch, after Baihe has loaded (so the session cookie exists): asks the server
     * whether it holds a newer build. A server that offers none, or a signed-out one, is silent.
     */
    private void checkForUpdate(String address) {
        // Reopening the app resumes the same process and WebView, so no page load fires again;
        // onResume asks again, at most once a minute, so a build fetched meanwhile is offered.
        long now = System.currentTimeMillis();
        if (updateOffered || AppUpdate.pending != null || now - updateCheckedAt < UPDATE_RECHECK_MS) return;
        updateCheckedAt = now;
        String cookie = CookieManager.getInstance().getCookie(address);
        new Thread(() -> {
            long offered = AppUpdate.check(address, cookie);
            // A failed ask (no network, a restarting PC) is tried again at the next chance, not a minute later.
            if (offered == AppUpdate.CHECK_FAILED) updateCheckedAt = 0;
            if (offered <= 0 || !AppUpdate.isNewer(installedVersion(), offered)) return;
            if (AppUpdate.canUpdateSilently(getApplicationContext())) {
                File apk = updateFile();
                if (AppUpdate.download(address, cookie, apk)) AppUpdate.pending = apk;
            } else {
                runOnUiThread(() -> offerUpdate(address, cookie));
            }
        }).start();
    }

    /** The Check for updates button: asks right away, and answers either way instead of staying silent. */
    private void checkForUpdateNow() {
        String address = savedAddress();
        if (address == null) return;
        String cookie = CookieManager.getInstance().getCookie(address);
        toast("Checking for updates…");
        new Thread(() -> {
            long offered = AppUpdate.check(address, cookie, true);
            if (offered == AppUpdate.CHECK_FAILED) {
                runOnUiThread(() -> toast("Could not reach Baihe to check. Try again in a minute."));
            } else if (offered <= 0 || !AppUpdate.isNewer(installedVersion(), offered)) {
                runOnUiThread(() -> toast("This app is up to date."));
            } else if (AppUpdate.pending != null) {
                runOnUiThread(() -> toast("An update is downloaded and installs when you leave the app."));
            } else {
                // Always asks here, even where updates could go in silently: the person pressed the button.
                runOnUiThread(() -> { if (!updateOffered) offerUpdate(address, cookie); });
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

    private void offerUpdate(String address, String cookie) {
        if (isFinishing() || isDestroyed()) return;
        updateOffered = true;
        new AlertDialog.Builder(this)
                .setTitle("Update Available")
                .setPositiveButton("Install update", (d, w) -> new Thread(() -> {
                    File apk = updateFile();
                    if (AppUpdate.download(address, cookie, apk)) AppUpdate.install(getApplicationContext(), apk);
                    else runOnUiThread(() -> android.widget.Toast.makeText(this,
                            "Could not download the update. Try again in a minute.", android.widget.Toast.LENGTH_LONG).show());
                }).start())
                .setNegativeButton(android.R.string.cancel, null)
                .setOnDismissListener(d -> updateOffered = false)
                .show();
    }

    private final Runnable poll = new Runnable() {
        @Override
        public void run() {
            String address = savedAddress();
            String shown = web.getUrl();
            if (address != null && shown != null && ServerAddress.sameOrigin(address, shown)) checkForUpdate(address);
            web.postDelayed(this, UPDATE_POLL_MS);
        }
    };

    @Override
    protected void onResume() {
        super.onResume();
        if (web == null) return;
        web.removeCallbacks(poll);
        poll.run();
    }

    @Override
    protected void onPause() {
        super.onPause();
        if (web != null) web.removeCallbacks(poll);
    }

    private File updateFile() {
        File dir = new File(getCacheDir(), "updates");
        dir.mkdirs();
        return new File(dir, "baihe-shell.apk");
    }

    @Override
    protected void onStart() {
        super.onStart();
        AppUpdate.screenStarted();
        AlertSchedule.enteredApp(this);
    }

    @Override
    protected void onStop() {
        super.onStop();
        AppUpdate.screenStopped(this);
        AlertSchedule.leftApp(this);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, MODE_PRIVATE);
    }
}
