package app.baihe.shell;

import android.app.Application;

/** Installs the crash recorder before any screen, service or receiver of the app starts. */
public class BaiheApp extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        CrashLog.install(this);
    }
}
