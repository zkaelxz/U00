package app.baihe.shell;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.webkit.CookieManager;
import java.util.List;

/** Asks Baihe for new alerts while the app is closed and shows them as Android notifications. */
final class AlertPoller {
    static final String CHANNEL = "alerts";
    private static final String MARK_ID = "alerts_mark_id";
    private static final String MARK_AT = "alerts_mark_at";

    private AlertPoller() {}

    /** One check. Quiet when alerts are off, the app is signed out or the PC cannot be reached. */
    static void poll(Context context) {
        SharedPreferences prefs = ShellSettings.prefs(context);
        if (!ShellSettings.isOn(context, ShellSettings.ALERTS)) return;
        String address = ServerAddress.shellAddress(prefs.getString("address", null));
        if (address == null) return;
        String cookie = CookieManager.getInstance().getCookie(address);
        if (cookie == null) return;
        String body = AppUpdate.fetchText(address + "/api/notifications", cookie);
        if (body == null) return;
        List<AlertFeed.Item> items = AlertFeed.parse(body);
        if (!prefs.contains(MARK_ID)) {
            // The first check only learns where the list stands: older entries are not news.
            remember(prefs, items);
            return;
        }
        List<AlertFeed.Item> fresh = AlertFeed.fresh(items, prefs.getLong(MARK_ID, 0),
                Double.longBitsToDouble(prefs.getLong(MARK_AT, 0)));
        remember(prefs, items);
        if (fresh.isEmpty()) return;
        ensureChannel(context);
        if (fresh.size() > AlertFeed.MAX_SEPARATE) {
            show(context, 1, fresh.size() + " new updates in Baihe", null);
            return;
        }
        for (AlertFeed.Item item : fresh) show(context, (int) (item.id % 100000) + 100, item.text, item.route);
    }

    private static void remember(SharedPreferences prefs, List<AlertFeed.Item> items) {
        if (items.isEmpty() && prefs.contains(MARK_ID)) return;
        prefs.edit().putLong(MARK_ID, AlertFeed.newestId(items))
                .putLong(MARK_AT, Double.doubleToLongBits(AlertFeed.newestAt(items))).apply();
    }

    private static void ensureChannel(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL, "Baihe alerts",
                NotificationManager.IMPORTANCE_DEFAULT));
    }

    private static void show(Context context, int id, String text, String route) {
        Intent open = new Intent(context, MainActivity.class).putExtra(MainActivity.EXTRA_ROUTE, route);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent tap = PendingIntent.getActivity(context, id, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        android.app.Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new android.app.Notification.Builder(context, CHANNEL)
                : new android.app.Notification.Builder(context);
        android.app.Notification note = builder
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("Baihe Studio")
                .setContentText(text)
                .setStyle(new android.app.Notification.BigTextStyle().bigText(text))
                .setContentIntent(tap)
                .setAutoCancel(true)
                .build();
        try {
            context.getSystemService(NotificationManager.class).notify(id, note);
        } catch (SecurityException ignored) {
            // Notifications were switched off in Android's settings.
        }
    }
}
