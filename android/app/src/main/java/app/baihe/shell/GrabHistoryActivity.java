package app.baihe.shell;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.format.DateUtils;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;

/**
 * Grab's queue and history as a screen of their own. Each row can go back to the page it came
 * from, and a Quick read can be read again: the PC remembers a page it translated for a while,
 * so that costs no tokens, and nothing is kept on the phone.
 */
public class GrabHistoryActivity extends Activity {
    private GrabUi.Palette palette;
    private LinearLayout list;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        palette = GrabUi.Palette.of(this);
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

    @Override
    protected void onStart() {
        super.onStart();
        AppUpdate.screenStarted();
    }

    @Override
    protected void onStop() {
        super.onStop();
        AppUpdate.screenStopped(this);
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(GrabActivity.PREFS, MODE_PRIVATE);
    }

    private void refresh() {
        list.removeAllViews();
        List<GrabQueue.Item> waiting = GrabStore.queue(prefs());
        List<GrabQueue.Done> done = GrabStore.history(prefs());

        TextView title = text("Queue and history", 22, true, palette.text);
        list.addView(title);
        LinearLayout top = row();
        top.addView(button("Back", palette.muted, v -> finish()));
        if (!waiting.isEmpty()) top.addView(button("Start the queue", palette.accent, v -> command("start", "")));
        if (!done.isEmpty()) {
            top.addView(button("Clear history", palette.bad, v -> {
                GrabStore.clearHistory(prefs());
                refresh();
            }));
        }
        list.addView(top);

        list.addView(section(waiting.isEmpty() ? "Waiting: nothing" : "Waiting"));
        for (GrabQueue.Item item : waiting) {
            LinearLayout card = card(item.name, item.chapters + (item.chapters == 1 ? " chapter" : " chapters")
                    + " - saved to your library");
            LinearLayout actions = row();
            actions.addView(button("Open page", palette.accent, v -> command("open", item.url)));
            actions.addView(button("Remove", palette.bad, v -> {
                GrabStore.remove(prefs(), item.url);
                refresh();
            }));
            card.addView(actions);
            list.addView(card);
        }

        list.addView(section(done.isEmpty() ? "History: nothing yet" : "History, newest first"));
        for (GrabQueue.Done entry : done) {
            CharSequence when = DateUtils.getRelativeTimeSpanString(
                    entry.at, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS);
            LinearLayout card = card(entry.name, when + ": " + entry.message);
            if (!entry.url.isEmpty()) {
                LinearLayout actions = row();
                actions.addView(button("Open page", palette.accent, v -> command("open", entry.url)));
                if (entry.quick) actions.addView(button("Read again", palette.accent, v -> command("read", entry.url)));
                card.addView(actions);
            }
            list.addView(card);
        }
        if (done.isEmpty() && waiting.isEmpty()) {
            list.addView(text("Add a chapter from Grab's More menu, or run a grab: it shows up here.", 14, false, palette.muted));
        } else {
            list.addView(text("Read again asks the PC for a Quick read of that page. The PC remembers pages it "
                    + "translated for 14 days, so it costs nothing while it does. Nothing is kept on this phone.",
                    13, false, palette.muted));
        }
    }

    /** Back to Grab with a request for it; Grab is the one place that opens pages and works the queue. */
    private void command(String command, String url) {
        Intent back = new Intent(this, GrabActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(GrabActivity.EXTRA_COMMAND, command)
                .putExtra(GrabActivity.EXTRA_URL, url);
        startActivity(back);
    }

    private TextView text(String value, int sp, boolean bold, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(sp);
        view.setTextColor(color);
        if (bold) view.setTypeface(null, android.graphics.Typeface.BOLD);
        view.setPadding(0, GrabUi.dp(this, 4), 0, GrabUi.dp(this, 4));
        return view;
    }

    private TextView section(String value) {
        TextView view = text(value, 15, true, palette.muted);
        view.setPadding(0, GrabUi.dp(this, 16), 0, GrabUi.dp(this, 4));
        return view;
    }

    private LinearLayout row() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        return row;
    }

    private LinearLayout card(String name, String detail) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        int pad = GrabUi.dp(this, 12);
        card.setPadding(pad, pad, pad, pad);
        card.setBackgroundColor(palette.surface);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 0, 0, GrabUi.dp(this, 8));
        card.setLayoutParams(params);
        card.addView(text(name, 16, true, palette.text));
        card.addView(text(detail, 14, false, palette.muted));
        return card;
    }

    private TextView button(String label, int color, View.OnClickListener onClick) {
        TextView view = text(label, 15, true, color);
        int pad = GrabUi.dp(this, 10);
        view.setPadding(0, pad, pad * 2, pad);
        view.setOnClickListener(onClick);
        return view;
    }
}
