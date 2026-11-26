package app.baihe.shell;

import android.content.Context;
import android.content.SharedPreferences;
import android.webkit.JavascriptInterface;

/** The phone app's own switches; Baihe's Settings page reads and sets them through {@link Bridge}. */
final class ShellSettings {
    static final String ALERTS = "alerts_on";
    static final String CLIPBOARD = "clipboard_offer_on";
    static final String PIP = "pip_on";

    private ShellSettings() {}

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("baihe", Context.MODE_PRIVATE);
    }

    /** Both switches start on: a person who never opens Settings still gets what the app is for. */
    static boolean isOn(Context context, String name) {
        return prefs(context).getBoolean(name, true);
    }

    static boolean known(String name) {
        return ALERTS.equals(name) || CLIPBOARD.equals(name) || PIP.equals(name);
    }

    /** What the app does when a Baihe page asks. */
    interface Host {
        void alertsSwitchedOn();

        /** A Live session's picture is on screen (true) or gone (false). */
        void liveWatching(boolean watching);

        /** Shrinks the app to a small floating window now. */
        void popOut();

        /** Baihe is showing a dark (true) or light (false) look, whatever the phone's own setting. */
        void themeChanged(boolean dark);

        /** Looks for a newer app build now and says what it found. */
        void checkForUpdates();

        /** Hides (true) or shows the Studio | Grab strip, for a reader that wants the whole screen. */
        void setBarsHidden(boolean hidden);
    }

    /** What Baihe's own pages can call as `BaiheShell`; only Baihe's address ever loads in the WebView. */
    static final class Bridge {
        private final Context context;
        private final Host host;

        Bridge(Context context, Host host) {
            this.context = context;
            this.host = host;
        }

        @JavascriptInterface
        public boolean get(String name) {
            return known(name) && isOn(context, name);
        }

        private volatile boolean liveWatching;

        @JavascriptInterface
        public void setLiveWatching(boolean watching) {
            liveWatching = watching;
            host.liveWatching(watching);
        }

        @JavascriptInterface
        public void setDark(boolean dark) {
            host.themeChanged(dark);
        }

        /** The build line and the kept crash text as JSON, for Baihe's own "Report a problem" dialog; "{}" if it cannot be built. */
        @JavascriptInterface
        public String diagnostics() {
            try {
                return new org.json.JSONObject()
                        .put("phone", ProblemReport.phoneInfo(BuildConfig.VERSION_CODE,
                                android.os.Build.VERSION.SDK_INT, android.os.Build.MODEL))
                        .put("crash", CrashLog.recent(context)).toString();
            } catch (org.json.JSONException | RuntimeException e) {
                return "{}";
            }
        }

        @JavascriptInterface
        public void checkForUpdates() {
            host.checkForUpdates();
        }

        @JavascriptInterface
        public void setBarsHidden(boolean hidden) {
            host.setBarsHidden(hidden);
        }

        @JavascriptInterface
        public void popOut() {
            host.popOut();
        }

        @JavascriptInterface
        public void set(String name, boolean on) {
            if (!known(name)) return;
            prefs(context).edit().putBoolean(name, on).apply();
            if (ALERTS.equals(name) && on) host.alertsSwitchedOn();
            if (PIP.equals(name)) host.liveWatching(liveWatching);
            if (ALERTS.equals(name)) AlertSchedule.ensure(context);
        }
    }
}
