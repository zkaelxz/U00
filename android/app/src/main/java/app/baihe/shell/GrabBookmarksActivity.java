package app.baihe.shell;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;

/**
 * Grab's bookmarks, or its recent pages when EXTRA_RECENT is set, as a screen of their own styled like
 * the queue and history page: tap a card to open it, Remove to drop it.
 */
public class GrabBookmarksActivity extends Activity {
    static final String EXTRA_RECENT = "recent";

    private boolean recent;
    private GrabUi.Palette palette;
    private LinearLayout list;

    @Override
    protected void onCreate(Bundle state) {
        setTheme(GrabUi.Palette.saved(this).themeRes());
        super.onCreate(state);
        palette = GrabUi.Palette.saved(this);
        recent = getIntent().getBooleanExtra(EXTRA_RECENT, false);
        getWindow().setStatusBarColor(palette.surface);
        getWindow().setNavigationBarColor(palette.surface);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(palette.bg);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        int pad = GrabUi.dp(this, 16);
        list.setPadding(pad, pad, pad, pad);
        scroll.addView(list, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
        scroll.setFitsSystemWindows(true);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refresh();
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(GrabActivity.PREFS, MODE_PRIVATE);
    }

    private String key() {
        return recent ? GrabActivity.KEY_RECENT : GrabActivity.KEY_BOOKMARKS;
    }

    private void refresh() {
        list.removeAllViews();
        List<GrabBookmarks.Mark> marks = GrabBookmarks.parse(prefs().getString(key(), ""));
        list.addView(text(recent ? "Recent pages" : "Bookmarks", 22, true, palette.text, 4));
        LinearLayout top = row();
        top.addView(button("Back", palette.muted, v -> finish()));
        if (recent) {
            if (!marks.isEmpty()) {
                top.addView(button("Clear", palette.bad, v -> {
                    prefs().edit().remove(key()).apply();
                    refresh();
                }));
            }
        } else {
            top.addView(button("Bookmark the open page", palette.accent, v -> command("bookmark", "")));
        }
        list.addView(top);

        if (marks.isEmpty()) {
            list.addView(text(recent ? "Nothing yet. Pages you open in Grab show up here."
                    : "Nothing saved yet. Open a page in Grab and press Bookmark the open page.", 14, false, palette.muted, 16));
            return;
        }
        for (GrabBookmarks.Mark mark : marks) {
            LinearLayout card = card();
            // The whole card opens the page: a small button is easy to miss on a phone.
            card.setOnClickListener(v -> command("open", mark.url));
            String name = (GrabBookmarks.isSite(mark.url) ? "Site: " : "") + mark.name;
            TextView heading = text(name, 16, true, palette.text, 4);
            heading.setMaxLines(2);
            heading.setEllipsize(android.text.TextUtils.TruncateAt.END);
            card.addView(heading);
            TextView address = text(mark.url, 13, false, palette.muted, 4);
            address.setMaxLines(2);
            address.setEllipsize(android.text.TextUtils.TruncateAt.MIDDLE);
            card.addView(address);
            LinearLayout actions = row();
            actions.addView(button("Open", palette.accent, v -> command("open", mark.url)));
            actions.addView(button("Remove", palette.bad, v -> {
                String left = GrabBookmarks.serialize(GrabBookmarks.without(
                        GrabBookmarks.parse(prefs().getString(key(), "")), mark.url));
                prefs().edit().putString(key(), left).apply();
                refresh();
            }));
            card.addView(actions);
            list.addView(card);
        }
    }

    /** Back to Grab with a request for it; Grab is the one place that opens pages. */
    private void command(String command, String url) {
        Intent back = new Intent(this, GrabActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(GrabActivity.EXTRA_COMMAND, command)
                .putExtra(GrabActivity.EXTRA_URL, url);
        startActivity(back);
    }

    private TextView text(String value, int sp, boolean bold, int color, int topDp) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        if (bold) view.setTypeface(null, android.graphics.Typeface.BOLD);
        view.setPadding(0, GrabUi.dp(this, topDp), 0, GrabUi.dp(this, 4));
        return view;
    }

    private LinearLayout row() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, GrabUi.dp(this, 4), 0, GrabUi.dp(this, 8));
        row.setLayoutParams(params);
        return row;
    }

    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        int pad = GrabUi.dp(this, 12);
        card.setPadding(pad, pad, pad, pad);
        card.setBackground(GrabUi.box(this, palette.surface, palette.border, 12, 1));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 0, 0, GrabUi.dp(this, 8));
        card.setLayoutParams(params);
        return card;
    }

    private TextView button(String label, int color, View.OnClickListener onClick) {
        TextView view = text(label, 15, true, color, 0);
        view.setGravity(Gravity.CENTER);
        view.setMinHeight(GrabUi.dp(this, 48));
        view.setPadding(GrabUi.dp(this, 14), 0, GrabUi.dp(this, 14), 0);
        view.setBackground(GrabUi.box(this, palette.surface, palette.border, 10, 1));
        LinearLayout.LayoutParams gap = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        gap.setMargins(0, 0, GrabUi.dp(this, 8), 0);
        view.setLayoutParams(gap);
        view.setOnClickListener(onClick);
        return view;
    }
}
