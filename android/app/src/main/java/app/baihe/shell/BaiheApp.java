package app.baihe.shell;

import android.app.Activity;
import android.app.Application;
import android.os.Bundle;

/** Installs the crash recorder before any screen, service or receiver of the app starts, and notes when the app returns to the front. */
public class BaiheApp extends Application {
    /** Shorter absences are ordinary screen changes, not a stalled connection. */
    private static final long AWAY_MS = 10_000;
    private int started;
    private long leftAt;

    @Override
    public void onCreate() {
        super.onCreate();
        CrashLog.install(this);
        registerActivityLifecycleCallbacks(new ActivityLifecycleCallbacks() {
            @Override public void onActivityStarted(Activity activity) {
                if (started++ > 0 || leftAt == 0) return;
                long since = leftAt;
                if (System.currentTimeMillis() - since >= AWAY_MS) {
                    new Thread(() -> BaiheClient.dropQuietStreams(since), "grab-resume").start();
                }
            }

            @Override public void onActivityStopped(Activity activity) {
                if (--started == 0) leftAt = System.currentTimeMillis();
            }

            @Override public void onActivityCreated(Activity activity, Bundle state) {}
            @Override public void onActivityResumed(Activity activity) {}
            @Override public void onActivityPaused(Activity activity) {}
            @Override public void onActivitySaveInstanceState(Activity activity, Bundle state) {}
            @Override public void onActivityDestroyed(Activity activity) {}
        });
    }
}
