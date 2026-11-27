package app.baihe.shell;

import android.content.Context;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;
import java.util.function.Consumer;

/** The tappable list of supported sites Grab shows while no page is open. */
final class GrabSites {
    private GrabSites() {}

    static final String KEY_SITES = "sites";

    /** The panel for `sites`; an empty list shows `emptyNote` instead, so the screen is never blank. */
    static View panel(Context c, GrabUi.Palette p, List<GrabBookmarks.Mark> sites, String emptyNote, Consumer<String> open) {
        ScrollView scroll = new ScrollView(c);
        scroll.setBackgroundColor(p.bg);
        LinearLayout list = new LinearLayout(c);
        list.setOrientation(LinearLayout.VERTICAL);
        int pad = GrabUi.dp(c, 16);
        list.setPadding(pad, pad, pad, pad);
        list.addView(text(c, "Sites you can open", 18, p.text, true));
        list.addView(text(c, sites.isEmpty() ? emptyNote : "Tap one to open it, then open a chapter to save or read it.",
                14, p.muted, false), spaced(c, 4, 12));
        for (GrabBookmarks.Mark site : sites) {
            TextView row = text(c, site.name, 16, p.text, false);
            row.setGravity(Gravity.CENTER_VERTICAL);
            row.setMinHeight(GrabUi.dp(c, 52));
            int inset = GrabUi.dp(c, 14);
            row.setPadding(inset, 0, inset, 0);
            row.setBackground(GrabUi.box(c, p.field, p.fieldEdge, 8, 1));
            row.setContentDescription("Open " + site.name);
            row.setOnClickListener(v -> open.accept(site.url));
            list.addView(row, spaced(c, 0, 8));
        }
        scroll.addView(list, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return scroll;
    }

    private static TextView text(Context c, String value, int sp, int color, boolean bold) {
        TextView t = new TextView(c);
        t.setText(value);
        t.setTextSize(sp);
        t.setTextColor(color);
        if (bold) t.setTypeface(null, android.graphics.Typeface.BOLD);
        return t;
    }

    private static LinearLayout.LayoutParams spaced(Context c, int topDp, int bottomDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, GrabUi.dp(c, topDp), 0, GrabUi.dp(c, bottomDp));
        return lp;
    }
}
