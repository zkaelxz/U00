package app.baihe.shell;

import android.app.Activity;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;

/**
 * Shows a novel chapter that Grab's Quick read translated on the PC. The text is kept only in memory
 * and is gone when this screen closes, so nothing reaches the library and nothing stays on the phone.
 */
public class NovelReadActivity extends Activity {
    private TextView body;
    private TextView status;
    private Button cancel;
    private ScrollView scroll;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        // Reading has long stretches with no touch, and the screen would otherwise dim mid-paragraph.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        GrabUi.Palette palette = GrabUi.Palette.saved(this);
        getWindow().setStatusBarColor(palette.bg);
        getWindow().setNavigationBarColor(palette.surface);
        if (!palette.dark) {
            getWindow().getDecorView().setSystemUiVisibility(android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                    | android.view.View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        }
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(palette.bg);
        root.setFitsSystemWindows(true);

        scroll = new ScrollView(this);
        body = new TextView(this);
        body.setTextColor(palette.text);
        body.setTextSize(18);
        body.setLineSpacing(0, 1.35f);
        body.setTextIsSelectable(true);
        int pad = GrabUi.dp(this, 16);
        body.setPadding(pad, pad, pad, pad);
        scroll.addView(body);
        root.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout footer = new LinearLayout(this);
        footer.setOrientation(LinearLayout.VERTICAL);
        footer.setBackgroundColor(palette.surface);
        footer.setPadding(pad, GrabUi.dp(this, 8), pad, GrabUi.dp(this, 8));
        status = new TextView(this);
        status.setTextColor(palette.muted);
        status.setTextSize(14);
        footer.addView(status);
        cancel = new Button(this);
        cancel.setText("Cancel");
        cancel.setOnClickListener(v -> NovelReadSession.cancel());
        footer.addView(cancel, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(footer, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);

        refresh();
        // Parts arrive while the reader is open, so the text grows as the grab translates them.
        NovelReadSession.listen(() -> runOnUiThread(this::refresh));
    }

    private void refresh() {
        List<String> parts = NovelReadSession.snapshot();
        body.setText(String.join("\n\n", parts));
        boolean running = !NovelReadSession.isComplete() && !NovelReadSession.isStopped();
        String summary = NovelReadSession.summary();
        if (running) status.setText("Translating more (" + parts.size() + " of " + NovelReadSession.total() + " parts so far)...");
        else status.setText(summary);
        cancel.setVisibility(running ? android.view.View.VISIBLE : android.view.View.GONE);
        status.setVisibility(status.getText().length() == 0 ? android.view.View.GONE : android.view.View.VISIBLE);
    }

    /** The volume keys scroll, so the phone can be read one-handed; most of a screen, to keep a line of overlap. */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode != KeyEvent.KEYCODE_VOLUME_DOWN && keyCode != KeyEvent.KEYCODE_VOLUME_UP) {
            return super.onKeyDown(keyCode, event);
        }
        int step = (int) (scroll.getHeight() * 0.85f);
        scroll.smoothScrollBy(0, keyCode == KeyEvent.KEYCODE_VOLUME_DOWN ? step : -step);
        return true;
    }

    /** Swallowed with its key-down, or the system would still change the volume and show its slider. */
    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        return keyCode == KeyEvent.KEYCODE_VOLUME_DOWN || keyCode == KeyEvent.KEYCODE_VOLUME_UP
                || super.onKeyUp(keyCode, event);
    }

    @Override
    protected void onDestroy() {
        if (isFinishing()) NovelReadSession.abandon();
        super.onDestroy();
    }
}
