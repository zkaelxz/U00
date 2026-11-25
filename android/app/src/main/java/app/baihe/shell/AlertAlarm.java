package app.baihe.shell;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** The quick alert check after the app was left (see {@link AlertSchedule}). */
public class AlertAlarm extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        Context app = context.getApplicationContext();
        PendingResult result = goAsync();
        new Thread(() -> {
            try {
                AlertPoller.poll(app);
            } finally {
                AlertSchedule.armIfRecent(app);
                result.finish();
            }
        }).start();
    }
}
