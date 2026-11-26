package app.baihe.shell;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Bitmap;
import android.graphics.Matrix;
import android.graphics.Rect;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.view.PixelCopy;
import android.view.View;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;

/**
 * The one-tap "Report a problem" on the phone app's own screens. The screen is copied first, on
 * the phone only, and shown as a preview with a checkbox: it is sent only if the reader leaves it ticked.
 */
final class ProblemReportDialog {
    /** Long side of the preview and the sent picture, so a report stays well under the PC's 4 MB limit. */
    private static final int MAX_SIDE = 1280;

    private ProblemReportDialog() {}

    /**
     * `area` is the page view: only its rectangle is copied, because the address bar above it shows
     * the full address (query and all), which the report deliberately leaves out.
     */
    static void show(Activity activity, String screen, String pageUrl, View area) {
        int[] at = new int[2];
        area.getLocationInWindow(at);
        Rect source = new Rect(at[0], at[1], at[0] + area.getWidth(), at[1] + area.getHeight());
        if (source.isEmpty()) {
            ask(activity, screen, pageUrl, null);
            return;
        }
        Bitmap full = Bitmap.createBitmap(source.width(), source.height(), Bitmap.Config.ARGB_8888);
        try {
            PixelCopy.request(activity.getWindow(), source, full, result -> {
                Bitmap shot = result == PixelCopy.SUCCESS ? shrink(full) : null;
                ask(activity, screen, pageUrl, shot);
            }, new Handler(Looper.getMainLooper()));
        } catch (RuntimeException e) {
            // A window that cannot be copied (hidden, secure) still gets a report, without a picture.
            ask(activity, screen, pageUrl, null);
        }
    }

    private static Bitmap shrink(Bitmap full) {
        int longSide = Math.max(full.getWidth(), full.getHeight());
        if (longSide <= MAX_SIDE) return full;
        float scale = (float) MAX_SIDE / longSide;
        Matrix matrix = new Matrix();
        matrix.postScale(scale, scale);
        return Bitmap.createBitmap(full, 0, 0, full.getWidth(), full.getHeight(), matrix, true);
    }

    private static void ask(Activity activity, String screen, String pageUrl, Bitmap shot) {
        if (activity.isFinishing() || activity.isDestroyed()) return;
        float dp = activity.getResources().getDisplayMetrics().density;
        int pad = (int) (16 * dp);
        LinearLayout form = new LinearLayout(activity);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(pad, pad / 2, pad, 0);
        EditText note = new EditText(activity);
        note.setHint("What went wrong?");
        note.setMinLines(2);
        note.setFilters(new android.text.InputFilter[] {
                new android.text.InputFilter.LengthFilter(ProblemReport.MAX_NOTE_CHARS)});
        form.addView(note);
        CheckBox include = new CheckBox(activity);
        if (shot != null) {
            ImageView preview = new ImageView(activity);
            preview.setImageBitmap(shot);
            preview.setAdjustViewBounds(true);
            preview.setMaxHeight((int) (220 * dp));
            form.addView(preview);
            include.setText("Include a screenshot of the page");
            include.setChecked(true);
            form.addView(include);
        }
        TextView what = new TextView(activity);
        what.setText("Also sent: this screen's name, the page address (no query), your app build and the last crash the phone kept. It goes to your Baihe, not anywhere else.");
        what.setTextSize(12);
        form.addView(what);
        new AlertDialog.Builder(activity)
                .setTitle("Report a problem")
                .setView(form)
                .setPositiveButton("Send", (d, w) -> send(activity, screen, pageUrl,
                        note.getText().toString(), shot != null && include.isChecked() ? shot : null))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private static void send(Activity activity, String screen, String pageUrl, String note, Bitmap shot) {
        android.content.SharedPreferences prefs = activity.getSharedPreferences(GrabActivity.PREFS, Activity.MODE_PRIVATE);
        String server = prefs.getString(GrabActivity.KEY_SERVER, "");
        String token = prefs.getString(GrabActivity.KEY_TOKEN, "");
        if (server.isEmpty() || token.isEmpty()) {
            Toast.makeText(activity, "Set up Grab first: enter your server address and a phone token.", Toast.LENGTH_LONG).show();
            return;
        }
        String phone = ProblemReport.phoneInfo(BuildConfig.VERSION_CODE, Build.VERSION.SDK_INT, Build.MODEL);
        String crash = CrashLog.recent(activity);
        new Thread(() -> {
            String message;
            try {
                byte[] jpeg = null;
                if (shot != null) {
                    ByteArrayOutputStream out = new ByteArrayOutputStream();
                    shot.compress(Bitmap.CompressFormat.JPEG, 80, out);
                    jpeg = out.toByteArray();
                }
                new BaiheClient(server, token).sendReport(ProblemReport.body(note, screen, pageUrl, phone, crash, jpeg));
                message = "Report sent.";
            } catch (BaiheClient.BridgeException e) {
                message = e.getMessage();
            } catch (Exception e) {
                message = "Could not send the report.";
            }
            String shown = message;
            activity.runOnUiThread(() -> Toast.makeText(activity, shown, Toast.LENGTH_LONG).show());
        }, "problem-report").start();
    }
}
