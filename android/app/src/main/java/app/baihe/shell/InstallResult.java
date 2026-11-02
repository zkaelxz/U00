package app.baihe.shell;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageInstaller;

/** Shows Android's own confirmation for an update the installer session is waiting to start. */
public class InstallResult extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        int status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (status != PackageInstaller.STATUS_PENDING_USER_ACTION) return;
        Intent confirm = intent.getParcelableExtra(Intent.EXTRA_INTENT);
        if (confirm == null) return;
        confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(confirm);
    }
}
