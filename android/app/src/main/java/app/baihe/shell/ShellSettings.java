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
