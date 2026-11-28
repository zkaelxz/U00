package app.baihe.shell;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;
import android.os.Build;
import android.widget.Toast;

/** Shows Android's own confirmation for an update the installer session is waiting to start, and says when an update failed. */
public class InstallResult extends BroadcastReceiver {
    private static final int NOTE_ID = 7001;

    @Override
    public void onReceive(Context context, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        // The person pressed Cancel in Android's installer; that is an answer, not a failure.
        if (status == PackageInstaller.STATUS_SUCCESS || status == PackageInstaller.STATUS_FAILURE_ABORTED) {
            clear(context);
            return;
        }
        if (status != PackageInstaller.STATUS_PENDING_USER_ACTION) {
            // Install update reuses a saved file of the offered build, so one Android refused must go.
            if (!keepsSavedApk(status)) AppUpdate.savedApk(context).delete();
            tell(context, failureText(status));
            return;
        }
        Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
        if (confirm == null) {
            tell(context, failureText(PackageInstaller.STATUS_FAILURE));
            return;
        }
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

    /** What the person reads when Android's installer refused the update. */
    static String failureText(int status) {
        switch (status) {
            case PackageInstaller.STATUS_FAILURE_STORAGE:
                return "The update did not install: the phone is short of free space. Free some space, then press Check for updates again.";
            case PackageInstaller.STATUS_FAILURE_CONFLICT:
            case PackageInstaller.STATUS_FAILURE_INCOMPATIBLE:
                return "The update did not install: it is signed with a different key from the Baihe app on this phone, and Android refuses that. Uninstall the old Baihe app first, then install this build from the PC.";
            case PackageInstaller.STATUS_FAILURE_INVALID:
                return "The update did not install: Android found the downloaded file damaged. Press Check for updates to download it again.";
            case PackageInstaller.STATUS_FAILURE_BLOCKED:
                return "The update did not install: Android or a security app on the phone blocked it.";
            default:
                return "The update did not install. Open Baihe and try Check for updates again.";
        }
    }

    /** A key conflict is the installed app's fault, not the file's: the same file installs once the old app is gone. */
    static boolean keepsSavedApk(int status) {
        return status == PackageInstaller.STATUS_FAILURE_CONFLICT || status == PackageInstaller.STATUS_FAILURE_INCOMPATIBLE;
    }

    /** A failure also shows as a toast, because a notification is silent when notifications are switched off. */
    static void tell(Context context, String text) {
        notify(context, text, null);
        try {
            Toast.makeText(context, text, Toast.LENGTH_LONG).show();
        } catch (RuntimeException ignored) {
            // The notification is the other way the person hears of it.
        }
    }

    /** Removes the update notification once it has no use: the install ended or the app is in front. */
    static void clear(Context context) {
        context.getSystemService(NotificationManager.class).cancel(NOTE_ID);
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
