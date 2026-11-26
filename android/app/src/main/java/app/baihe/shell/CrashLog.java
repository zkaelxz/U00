package app.baihe.shell;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.PrintWriter;
import java.io.StringWriter;

/** Keeps the last crash's stack trace on the phone so the next launch can show it; the app has no other crash record. */
final class CrashLog {
    private static final String KEY = "last_crash";
    private static final String CAUGHT_KEY = "last_caught";
    private static final String CAUGHT_SHOWN = "caught_shown";
    /** A trace is a few KB; the cap only guards the preferences file against a runaway cause chain. */
    private static final int MAX_CHARS = 8000;

    private CrashLog() {}

    static void install(Context context) {
        Context app = context.getApplicationContext();
        Thread.UncaughtExceptionHandler previous = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try {
                // commit(), not apply(): the process is about to die and apply() writes later.
                prefs(app).edit().putString(KEY, describe(thread.getName(), error)).commit();
            } catch (RuntimeException ignored) {
                // Nothing useful to do while already crashing.
            }
            if (previous != null) previous.uncaughtException(thread, error);
        });
    }

    /** The saved trace, removed so it is shown once; null when the last run did not crash. */
    static String takePending(Context context) {
        SharedPreferences prefs = prefs(context);
        String trace = prefs.getString(KEY, null);
        if (trace != null) prefs.edit().remove(KEY).commit();
        return trace;
    }

    /**
     * A failure the app survived on purpose (so it does not close) but that someone needs to learn
     * the reason for. Kept once: the first reason is the useful one and later ones repeat it.
     */
    static void noteCaught(Context context, String what, Throwable error) {
        android.util.Log.w("Baihe", what, error);
        try {
            SharedPreferences prefs = prefs(context);
            if (prefs.contains(CAUGHT_KEY) || prefs.getBoolean(CAUGHT_SHOWN, false)) return;
            prefs.edit().putString(CAUGHT_KEY, what + "\n" + describe(Thread.currentThread().getName(), error)).commit();
        } catch (RuntimeException ignored) {
            // Reporting must never be the thing that fails.
        }
    }

    /** The saved survived-failure note, shown once ever; null when there is none. */
    static String takeCaught(Context context) {
        SharedPreferences prefs = prefs(context);
        String note = prefs.getString(CAUGHT_KEY, null);
        if (note != null) prefs.edit().remove(CAUGHT_KEY).putBoolean(CAUGHT_SHOWN, true).commit();
        return note;
    }

    static String describe(String threadName, Throwable error) {
        StringWriter text = new StringWriter();
        error.printStackTrace(new PrintWriter(text));
        String trace = "Build " + BuildConfig.VERSION_CODE + ", Android " + android.os.Build.VERSION.SDK_INT
                + ", thread " + threadName + "\n" + text;
        return trace.length() > MAX_CHARS ? trace.substring(0, MAX_CHARS) : trace;
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences("baihe_crash", Context.MODE_PRIVATE);
    }
}
