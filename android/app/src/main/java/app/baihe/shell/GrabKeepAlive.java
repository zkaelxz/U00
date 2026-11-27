package app.baihe.shell;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

/**
 * Keeps the app's process (and the PC connection a grab holds open) alive while the person
 * leaves the app: Android otherwise cuts a backgrounded app's network and may stop it.
 */
public class GrabKeepAlive extends Service {
    private static final String CHANNEL = "grab";
    private static final int NOTIFICATION_ID = 7;
    private static final String READY_CHANNEL = "grab_ready";
    private static final int READY_NOTIFICATION_ID = 8;
    /** Far longer than any grab (a page walk is capped at 6 minutes); only a leak guard. */
    private static final long WAKE_LOCK_MS = 45L * 60 * 1000;
    private PowerManager.WakeLock wake;

    /** Never throws: Android refuses to start a foreground service from the background. */
    static void start(Context context) {
        try {
            Intent intent = new Intent(context, GrabKeepAlive.class);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent);
            else context.startService(intent);
        } catch (RuntimeException ignored) {
            // The grab still runs while the app is open.
        }
    }

    static void stop(Context context) {
        try {
            context.stopService(new Intent(context, GrabKeepAlive.class));
        } catch (RuntimeException ignored) {
            // Already stopped.
        }
    }

    /** Tells the person a Quick read they left is fully ready; a tap opens the reader. Never throws. */
    static void notifyReady(Context context, String name) {
        try {
            NotificationManager manager = (NotificationManager) context.getSystemService(NOTIFICATION_SERVICE);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                manager.createNotificationChannel(new NotificationChannel(
                        READY_CHANNEL, "Baihe chapter ready", NotificationManager.IMPORTANCE_DEFAULT));
            }
            PendingIntent open = PendingIntent.getActivity(context, 1,
                    new Intent(context, GrabActivity.class)
                            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                            .putExtra(GrabActivity.EXTRA_COMMAND, "reader"),
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
            Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                    ? new Notification.Builder(context, READY_CHANNEL) : new Notification.Builder(context);
            manager.notify(READY_NOTIFICATION_ID, builder
                    .setSmallIcon(R.drawable.ic_grab)
                    .setContentTitle("Chapter ready")
                    .setContentText(name == null || name.isEmpty() ? "Tap to open the reader" : name + ": tap to open the reader")
                    .setContentIntent(open)
                    .setAutoCancel(true)
                    .build());
        } catch (RuntimeException ignored) {
            // Notifications may be off; the reader still has the pages.
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager manager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL, "Baihe Grab", NotificationManager.IMPORTANCE_LOW));
        }
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, GrabActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        Notification notification = builder
                .setSmallIcon(R.drawable.ic_grab)
                .setContentTitle("Baihe Grab")
                .setContentText("Reading pages in the background")
                .setContentIntent(open)
                .setOngoing(true)
                .build();
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
        } catch (RuntimeException e) {
            stopSelf();
            return START_NOT_STICKY;
        }
        if (wake == null) {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            wake = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "baihe:grab");
            wake.acquire(WAKE_LOCK_MS);
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        if (wake != null && wake.isHeld()) wake.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
