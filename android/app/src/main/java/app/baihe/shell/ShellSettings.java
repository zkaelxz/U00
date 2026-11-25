package app.baihe.shell;

import android.content.Context;
import android.content.SharedPreferences;
import android.webkit.JavascriptInterface;

/** The phone app's own switches; Baihe's Settings page reads and sets them through {@link Bridge}. */
final class ShellSettings {
    static final String ALERTS = "alerts_on";
    static final String CLIPBOARD = "clipboard_offer_on";

    private ShellSettings() {}

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("baihe", Context.MODE_PRIVATE);
    }

    /** Both switches start on: a person who never opens Settings still gets what the app is for. */
    static boolean isOn(Context context, String name) {
        return prefs(context).getBoolean(name, true);
    }

    static boolean known(String name) {
        return ALERTS.equals(name) || CLIPBOARD.equals(name);
    }

    /** What Baihe's own pages can call as `BaiheShell`; only Baihe's address ever loads in the WebView. */
    static final class Bridge {
        private final Context context;
        private final Runnable onAlertsOn;

        Bridge(Context context, Runnable onAlertsOn) {
            this.context = context;
            this.onAlertsOn = onAlertsOn;
        }

        @JavascriptInterface
        public boolean get(String name) {
            return known(name) && isOn(context, name);
        }

        @JavascriptInterface
        public void set(String name, boolean on) {
            if (!known(name)) return;
            prefs(context).edit().putBoolean(name, on).apply();
            if (ALERTS.equals(name) && on) onAlertsOn.run();
            if (ALERTS.equals(name)) AlertSchedule.ensure(context);
        }
    }
}
