package app.baihe.shell;

import android.app.job.JobParameters;
import android.app.job.JobService;

/** The 15-minute alert check (see {@link AlertSchedule}). */
public class AlertJob extends JobService {
    @Override
    public boolean onStartJob(JobParameters params) {
        new Thread(() -> {
            try {
                AlertPoller.poll(getApplicationContext());
            } finally {
                jobFinished(params, false);
            }
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return false;
    }
}
