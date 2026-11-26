package app.baihe.shell;

import android.app.Activity;
import android.app.Dialog;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.RippleDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.List;

/** Grab's More menu as a bottom sheet: actions grouped under section headers, most used first. */
final class GrabMenu {
    private final Activity activity;
    private final GrabUi.Palette palette;
    private final List<Object[]> rows = new ArrayList<>();

    GrabMenu(Activity activity, GrabUi.Palette palette) {
        this.activity = activity;
        this.palette = palette;
    }

    GrabMenu section(String title) {
        rows.add(new Object[] {title, null});
        return this;
    }

    GrabMenu item(String label, Runnable action) {
        rows.add(new Object[] {label, action});
        return this;
    }

    void show() {
        Dialog dialog = new Dialog(activity);
        dialog.requestWindowFeature(Window.FEATURE_NO_TITLE);
        LinearLayout column = new LinearLayout(activity);
        column.setOrientation(LinearLayout.VERTICAL);
        int pad = GrabUi.dp(activity, 8);
        column.setPadding(0, pad, 0, pad);
        for (Object[] row : rows) {
            String label = (String) row[0];
            Runnable action = (Runnable) row[1];
            if (action == null) column.addView(header(label));
            else column.addView(entry(label, () -> {
                dialog.dismiss();
                action.run();
            }));
        }
        ScrollView scroll = new ScrollView(activity);
        scroll.addView(column);
        scroll.setScrollbarFadingEnabled(false);
        scroll.setClipToOutline(true);
        scroll.setBackground(GrabUi.box(activity, palette.surface, palette.border, 16, 1));
        dialog.setContentView(scroll);
        Window window = dialog.getWindow();
        if (window != null) {
            window.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            android.util.DisplayMetrics screen = activity.getResources().getDisplayMetrics();
            // Sized before it shows: capping after the first layout makes a tall sheet open and then snap down.
            column.measure(View.MeasureSpec.makeMeasureSpec(screen.widthPixels, View.MeasureSpec.EXACTLY),
                    View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
            int height = Math.min(column.getMeasuredHeight(), (int) (screen.heightPixels * 0.8f));
            window.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, height);
            window.setGravity(Gravity.BOTTOM);
        }
        dialog.show();
    }

    private TextView header(String label) {
        TextView view = new TextView(activity);
        view.setText(label.toUpperCase(java.util.Locale.ROOT));
        view.setTextSize(12);
        view.setLetterSpacing(0.08f);
        view.setTypeface(null, android.graphics.Typeface.BOLD);
        view.setTextColor(palette.muted);
        int side = GrabUi.dp(activity, 20);
        view.setPadding(side, GrabUi.dp(activity, 14), side, GrabUi.dp(activity, 4));
        return view;
    }

    private TextView entry(String label, Runnable onClick) {
        TextView view = new TextView(activity);
        view.setText(label);
        view.setTextSize(16);
        view.setTextColor(palette.text);
        view.setGravity(Gravity.CENTER_VERTICAL);
        view.setMinHeight(GrabUi.dp(activity, 48));
        int side = GrabUi.dp(activity, 20);
        view.setPadding(side, 0, side, 0);
        view.setBackground(new RippleDrawable(ColorStateList.valueOf((palette.muted & 0x00FFFFFF) | 0x33000000), null, new ColorDrawable(Color.WHITE)));
        view.setOnClickListener(v -> onClick.run());
        return view;
    }
}
