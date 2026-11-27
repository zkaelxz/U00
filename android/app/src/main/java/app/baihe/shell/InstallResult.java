package app.baihe.shell;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.Build;

/** Shows Android's own confirmation for an update the installer session is waiting to start, and says when an update failed. */
public class InstallResult extends BroadcastReceiver {
    private static final int NOTE_ID = 7001;

    @Override
    public void onReceive(Context context, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status == PackageInstaller.STATUS_SUCCESS) return;
        if (status != PackageInstaller.STATUS_PENDING_USER_ACTION) {
            notify(context, "The update did not install. Open Baihe and try Check for updates again.", null);
            return;
        }
        Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
        if (confirm == null) return;
        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        // Android 10 and later refuse to open a screen from the background, so the same
        // confirmation is also offered as a notification the person can tap.
        notify(context, "Tap to finish updating Baihe.", confirm);
        try {
            context.startActivity(confirm);
        } catch (RuntimeException ignored) {
            // Blocked from the background; the notification above still reaches the person.
        }
    }

    private static void notify(Context context, String text, Intent confirm) {
        AlertPoller.ensureChannel(context);
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(context, AlertPoller.CHANNEL)
                : new Notification.Builder(context);
        builder.setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("Baihe Studio")
                .setContentText(text)
                .setAutoCancel(true);
        if (confirm != null) {
            builder.setContentIntent(PendingIntent.getActivity(context, NOTE_ID, confirm,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        }
        try {
            context.getSystemService(NotificationManager.class).notify(NOTE_ID, builder.build());
        } catch (SecurityException ignored) {
            // Notifications were switched off in Android's settings.
        }
    }
}
