package app.baihe.shell;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;

/**
 * When alerts are checked. A 15-minute job runs whenever alerts are on. For two hours after the app is
 * left, a 3-minute alarm checks too, so "channel went live" is not a quarter of an hour late while
 * someone is plausibly waiting for it, without a service that holds the phone awake all day.
 */
final class AlertSchedule {
    static final int JOB_ID = 4101;
    private static final long JOB_EVERY_MS = 15 * 60_000;
    private static final long ALARM_EVERY_MS = 3 * 60_000;
    static final long RECENT_WINDOW_MS = 2 * 60 * 60_000;
    private static final String UNTIL = "alerts_fast_until";

    private AlertSchedule() {}

    /** Starts or stops the periodic job to match the switch. Safe to call often. */
    static void ensure(Context context) {
        JobScheduler jobs = context.getSystemService(JobScheduler.class);
        if (jobs == null) return;
        if (!ShellSettings.isOn(context, ShellSettings.ALERTS)) {
            jobs.cancel(JOB_ID);
            cancelAlarm(context);
            return;
        }
        if (jobs.getPendingJob(JOB_ID) != null) return;
        jobs.schedule(new JobInfo.Builder(JOB_ID, new ComponentName(context, AlertJob.class))
                .setPeriodic(JOB_EVERY_MS)
                .setPersisted(false)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .build());
    }

    /** The app was just left: keep checking quickly for a while. */
    static void leftApp(Context context) {
        if (!ShellSettings.isOn(context, ShellSettings.ALERTS)) return;
        ShellSettings.prefs(context).edit().putLong(UNTIL, System.currentTimeMillis() + RECENT_WINDOW_MS).apply();
        try {
            arm(context);
        } catch (RuntimeException e) {
            // A phone that refuses the alarm only loses the faster checks; leaving the app must not crash it.
        }
    }

    /** The app is in front again: the page itself shows what is new. */
    static void enteredApp(Context context) {
        try {
            cancelAlarm(context);
        } catch (RuntimeException e) {
            // Same as leftApp: the app must open even when the alarm service refuses.
        }
    }

    /** Called after each alarm check; sets the next one while the window lasts. */
    static void armIfRecent(Context context) {
        if (System.currentTimeMillis() < ShellSettings.prefs(context).getLong(UNTIL, 0)) arm(context);
    }

    private static PendingIntent alarmIntent(Context context) {
        return PendingIntent.getBroadcast(context, JOB_ID, new Intent(context, AlertAlarm.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static void arm(Context context) {
        AlarmManager alarms = context.getSystemService(AlarmManager.class);
        if (alarms == null) return;
        // Inexact on purpose: exact alarms need a permission and a few minutes of slack is fine here.
        alarms.setAndAllowWhileIdle(AlarmManager.RTC, System.currentTimeMillis() + ALARM_EVERY_MS, alarmIntent(context));
    }

    private static void cancelAlarm(Context context) {
        AlarmManager alarms = context.getSystemService(AlarmManager.class);
        if (alarms != null) alarms.cancel(alarmIntent(context));
    }
}
