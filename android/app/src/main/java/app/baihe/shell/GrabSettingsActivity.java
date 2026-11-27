package app.baihe.shell;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/** Grab's settings as a screen of their own: connection, Quick read, English lettering and extra ad blocking. */
public class GrabSettingsActivity extends Activity {
    private GrabUi.Palette palette;
    private LinearLayout list;
    private TextView connectionStatus;
    private TextView adStatus;

    @Override
    protected void onCreate(Bundle state) {
        setTheme(GrabUi.Palette.saved(this).themeRes());
        super.onCreate(state);
        palette = GrabUi.Palette.saved(this);
        getWindow().setStatusBarColor(palette.surface);
        getWindow().setNavigationBarColor(palette.surface);
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(palette.bg);
        scroll.setFillViewport(true);
        list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        int pad = GrabUi.dp(this, 16);
        list.setPadding(pad, pad, pad, pad);
        scroll.addView(list, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);
        scroll.setFitsSystemWindows(true);
        build();
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(GrabActivity.PREFS, MODE_PRIVATE);
    }

    private void build() {
        list.addView(text("Settings", 22, true, palette.text, 4));
        list.addView(button("Back", palette.muted, v -> finish()));

        list.addView(section("Connection"));
        LinearLayout card = card();
        EditText server = field("Server address", prefs().getString(GrabActivity.KEY_SERVER, ""), false);
        // The saved token is never shown again; an empty field keeps the one already saved.
        EditText token = field("Token (starts with baihe_dt_)", "", true);
        card.addView(server);
        card.addView(token);
        card.addView(text("Make the token at the PC in Baihe: Settings > Extension tokens per computer.", 13, false, palette.muted, 8));
        connectionStatus = text("", 14, false, palette.muted, 4);
        card.addView(connectionStatus);
        card.addView(button("Save and connect", palette.accent, v -> saveConnection(server, token)));
        list.addView(card);

        list.addView(section("Quick read"));
        LinearLayout reading = card();
        reading.addView(toggle("Translate the next chapter before I reach it",
                prefs().getBoolean(GrabActivity.KEY_READ_AHEAD, true),
                on -> prefs().edit().putBoolean(GrabActivity.KEY_READ_AHEAD, on).apply()));
        list.addView(reading);

        list.addView(section("English lettering"));
        LinearLayout style = card();
        TextStyle now = GrabActivity.textStyle(prefs());
        style.addView(text("Size", 14, true, palette.text, 0));
        style.addView(choices(TextStyle.SIZES, now.size, i -> prefs().edit().putInt("text_size", i).apply()));
        style.addView(text("Typeface", 14, true, palette.text, 12));
        style.addView(choices(TextStyle.FONTS, now.font, i -> prefs().edit().putInt("text_font", i).apply()));
        style.addView(toggle("Outline the letters", now.outline, on -> prefs().edit().putBoolean("text_outline", on).apply()));
        style.addView(text("Larger text can get tight in small bubbles. Changes apply to pages translated from now on.", 13, false, palette.muted, 8));
        list.addView(style);

        list.addView(section("Block more sites"));
        LinearLayout ads = card();
        EditText hosts = field("One site per line, like ads.example.com", prefs().getString(GrabActivity.KEY_EXTRA_ADS, ""), false);
        hosts.setSingleLine(false);
        hosts.setMinLines(4);
        hosts.setGravity(Gravity.TOP);
        hosts.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_VARIATION_URI);
        ads.addView(hosts);
        adStatus = text("Blocking one of these stops its pop-ups and its requests from loading.", 13, false, palette.muted, 8);
        ads.addView(adStatus);
        ads.addView(button("Save list", palette.accent, v -> {
            String saved = String.join("\n", AdHosts.parseExtra(hosts.getText().toString()));
            prefs().edit().putString(GrabActivity.KEY_EXTRA_ADS, saved).apply();
            AdHosts.setExtra(saved);
            adStatus.setText("Blocking " + AdHosts.parseExtra(saved).size() + " more sites. Reload the page to see it.");
        }));
        list.addView(ads);
    }

    /**
     * Nothing is saved until the server answers /health with this token, and the host it
     * answered from is shown, so a lookalike address is visible before the token is trusted.
     */
    private void saveConnection(EditText server, EditText token) {
        String origin = ServerAddress.normalizeServer(server.getText().toString());
        if (origin == null) {
            connectionStatus.setText("The server address must start with https://.");
            return;
        }
        String candidate = ServerAddress.tokenToCheck(token.getText().toString(),
                prefs().getString(GrabActivity.KEY_TOKEN, ""), origin, prefs().getString(GrabActivity.KEY_SERVER, ""));
        if (candidate.isEmpty()) {
            connectionStatus.setText("Enter the token.");
            return;
        }
        connectionStatus.setText("Checking...");
        new Thread(() -> {
            String message;
            try {
                new BaiheClient(origin, candidate).health();
                prefs().edit().putString(GrabActivity.KEY_SERVER, origin).putString(GrabActivity.KEY_TOKEN, candidate).apply();
                message = "Connected to " + ServerAddress.hostOf(origin);
            } catch (BaiheClient.BridgeException e) {
                message = e.getMessage();
            }
            String shown = message;
            runOnUiThread(() -> connectionStatus.setText(shown));
        }, "grab-settings-check").start();
    }

    private interface IntChange { void on(int value); }
    private interface BoolChange { void on(boolean value); }

    /** One tappable row per option, saved the moment it is chosen. */
    private LinearLayout choices(String[] options, int selected, IntChange change) {
        LinearLayout group = new LinearLayout(this);
        group.setOrientation(LinearLayout.VERTICAL);
        TextView[] rows = new TextView[options.length];
        for (int i = 0; i < options.length; i++) {
            int index = i;
            rows[i] = optionRow(options[i]);
            mark(rows[i], true, i == selected);
            rows[i].setOnClickListener(v -> {
                for (int j = 0; j < rows.length; j++) mark(rows[j], true, j == index);
                change.on(index);
            });
            group.addView(rows[i]);
        }
        return group;
    }

    /** Puts a themed radio or check mark before the row's text. */
    private void mark(TextView row, boolean radio, boolean on) {
        int size = GrabUi.dp(this, 22);
        GrabMark mark = new GrabMark(radio, on, palette);
        mark.setBounds(0, 0, size, size);
        row.setCompoundDrawablesRelative(mark, null, null, null);
        row.setCompoundDrawablePadding(GrabUi.dp(this, 12));
    }

    private TextView optionRow(String shown) {
        TextView row = text(shown, 15, false, palette.text, 0);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setMinHeight(GrabUi.dp(this, 48));
        return row;
    }

    private TextView toggle(String label, boolean on, BoolChange change) {
        boolean[] state = {on};
        TextView row = optionRow(label);
        mark(row, false, on);
        row.setOnClickListener(v -> {
            state[0] = !state[0];
            mark(row, false, state[0]);
            change.on(state[0]);
        });
        return row;
    }

    private EditText field(String hint, String value, boolean secret) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setHintTextColor(palette.muted);
        view.setTextColor(palette.text);
        view.setSingleLine(true);
        view.setText(value);
        view.setInputType(secret
                ? InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
                : InputType.TYPE_TEXT_VARIATION_URI);
        int pad = GrabUi.dp(this, 12);
        view.setPadding(pad, pad, pad, pad);
        view.setBackground(GrabUi.box(this, palette.field, palette.fieldEdge, 10, 1));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, 0, 0, GrabUi.dp(this, 8));
        view.setLayoutParams(params);
        return view;
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

    private TextView section(String value) {
        return text(value, 15, true, palette.muted, 16);
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
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.setMargins(0, GrabUi.dp(this, 8), 0, 0);
        view.setLayoutParams(params);
        view.setOnClickListener(onClick);
        return view;
    }
}
