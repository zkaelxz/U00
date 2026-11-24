package app.baihe.shell;

import android.app.Activity;
import android.content.Intent;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.TextView;

/** The Studio | Grab strip shared by both screens, so switching brings the other screen forward as it was left. */
final class ShellTabs {
    private ShellTabs() {}

    static View strip(Activity from, GrabUi.Palette palette, boolean grabSelected) {
        LinearLayout row = new LinearLayout(from);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setBackgroundColor(palette.surface);
        row.addView(tab(from, palette, "Studio", !grabSelected, MainActivity.class),
                new LinearLayout.LayoutParams(0, GrabUi.dp(from, 40), 1f));
        row.addView(tab(from, palette, "Grab", grabSelected, GrabActivity.class),
                new LinearLayout.LayoutParams(0, GrabUi.dp(from, 40), 1f));
        return row;
    }

    /** Brings the other screen forward without restarting it: a running grab and the open page stay as they were. */
    static void show(Activity from, Class<? extends Activity> screen) {
        from.startActivity(new Intent(from, screen).addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
        from.overridePendingTransition(0, 0);
    }

    private static View tab(Activity from, GrabUi.Palette palette, String label, boolean selected,
                            Class<? extends Activity> screen) {
        TextView view = new TextView(from);
        view.setText(label);
        view.setGravity(Gravity.CENTER);
        view.setTextSize(14);
        view.setTextColor(selected ? palette.accent : palette.muted);
        view.setTypeface(null, selected ? android.graphics.Typeface.BOLD : android.graphics.Typeface.NORMAL);
        view.setContentDescription(selected ? label + ", current screen" : "Open " + label);
        if (!selected) view.setOnClickListener(v -> show(from, screen));
        return view;
    }
}
