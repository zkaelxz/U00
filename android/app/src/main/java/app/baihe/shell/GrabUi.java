package app.baihe.shell;

import android.content.Context;
import android.content.res.ColorStateList;
import android.content.res.Configuration;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

/** The look of the Grab screen: the main app's colours, the bottom bar buttons and the status chip. */
final class GrabUi {
    private GrabUi() {}

    enum Kind { BUSY, INFO, OK, BAD }

    /** The same dark and light tokens as the app's stylesheet (frontend/src/index.css). */
    static final class Palette {
        final int bg, surface, field, fieldEdge, border, text, muted, accent, onAccent, bad, ok;
        final boolean dark;

        Palette(boolean dark) {
            this.dark = dark;
            bg = dark ? 0xFF0D1220 : 0xFFF3F6FC;
            surface = dark ? 0xFF161B2E : 0xFFFFFFFF;
            field = dark ? 0xFF111729 : 0xFFFFFFFF;
            fieldEdge = dark ? 0xFF6B7694 : 0xFF7D889E;
            border = dark ? 0xFF2A3350 : 0xFFDDE3EF;
            text = dark ? 0xFFEEF1FA : 0xFF17202F;
            muted = dark ? 0xFFA8B2CC : 0xFF44526B;
            accent = dark ? 0xFF8EC5FF : 0xFF1D6FD1;
            onAccent = dark ? 0xFF08111F : 0xFFFFFFFF;
            bad = dark ? 0xFFEF7B73 : 0xFFB3261E;
            ok = dark ? 0xFF5FC08C : 0xFF1A6E44;
        }

        static Palette of(Context context) {
            int night = context.getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK;
            return new Palette(night == Configuration.UI_MODE_NIGHT_YES);
        }
    }

    /**
     * How a finished grab's message reads: a clean success fades away on its own, while a
     * partial result or a failure stays because it is the only record of what happened.
     */
    static Kind kindOf(String message, boolean sent) {
        if (message.startsWith("Opened ") || (sent && !message.contains("Not sent"))) return Kind.OK;
        if (sent) return Kind.INFO;
        return Kind.BAD;
    }

    static int dp(Context c, float v) {
        return Math.round(v * c.getResources().getDisplayMetrics().density);
    }

    static GradientDrawable box(Context c, int fill, int edge, float radiusDp, float strokeDp) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(fill);
        d.setCornerRadius(dp(c, radiusDp));
        if (strokeDp > 0) d.setStroke(dp(c, strokeDp), edge);
        return d;
    }

    /** An icon over a label (or beside it for the primary button) that can be dimmed while a grab runs. */
    static final class BarButton extends LinearLayout {
        private final ImageView icon;
        private final TextView label;
        private final Palette palette;
        private final boolean primary;
        private GradientDrawable fill;

        BarButton(Context c, Palette p, int iconRes, String text, boolean primary) {
            super(c);
            this.palette = p;
            this.primary = primary;
            setGravity(Gravity.CENTER);
            setClickable(true);
            setFocusable(true);
            icon = new ImageView(c);
            icon.setImageResource(iconRes);
            label = new TextView(c);
            label.setText(text);
            label.setSingleLine(true);
            if (primary) {
                setOrientation(HORIZONTAL);
                label.setTextSize(15);
                label.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
                addView(icon, new LayoutParams(dp(c, 24), dp(c, 24)));
                LayoutParams lp = new LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                lp.leftMargin = dp(c, 8);
                addView(label, lp);
                fill = box(c, p.accent, 0, 12, 0);
                setBackground(new RippleDrawable(ColorStateList.valueOf(0x33000000), fill, null));
            } else {
                setOrientation(VERTICAL);
                label.setTextSize(12);
                label.setGravity(Gravity.CENTER);
                addView(icon, new LayoutParams(dp(c, 24), dp(c, 24)));
                addView(label, new LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
                setBackground(new RippleDrawable(ColorStateList.valueOf(0x22888888), null, box(c, 0, 0, 8, 0)));
            }
            tint(primary ? p.onAccent : p.muted);
            setContentDescription(text);
        }

        void setLabel(String text, int color, int iconRes) {
            label.setText(text);
            icon.setImageResource(iconRes);
            tint(color);
            setContentDescription(text);
        }

        /** The primary button turns red while it cancels a running grab. */
        void setDanger(boolean danger) {
            if (fill != null) fill.setColor(danger ? palette.bad : palette.accent);
        }

        void resetTint() {
            tint(primary ? palette.onAccent : palette.muted);
        }

        /** Dimmed but still tappable, so pressing it mid-grab can say why nothing happens. */
        void setBusy(boolean busy) {
            setAlpha(busy ? 0.4f : 1f);
        }

        private void tint(int color) {
            icon.setImageTintList(ColorStateList.valueOf(color));
            label.setTextColor(color);
        }
    }

    /** A rounded status line between the page and the bar; hidden while empty. */
    static final class Chip extends LinearLayout {
        private final TextView text;
        private final ProgressBar spinner;
        private final Palette palette;
        private final GradientDrawable shape;
        private final Runnable hide = () -> setVisibility(GONE);

        Chip(Context c, Palette p) {
            super(c);
            palette = p;
            setOrientation(HORIZONTAL);
            setGravity(Gravity.CENTER_VERTICAL);
            int pad = dp(c, 12);
            setPadding(pad, pad, pad, pad);
            shape = box(c, p.surface, p.border, 12, 1);
            setBackground(shape);
            spinner = new ProgressBar(c);
            spinner.setIndeterminate(true);
            spinner.setIndeterminateTintList(ColorStateList.valueOf(p.accent));
            LayoutParams sp = new LayoutParams(dp(c, 16), dp(c, 16));
            sp.rightMargin = dp(c, 10);
            addView(spinner, sp);
            text = new TextView(c);
            text.setTextColor(p.text);
            text.setTextSize(14);
            text.setMaxLines(3);
            text.setEllipsize(android.text.TextUtils.TruncateAt.END);
            addView(text, new LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
            // A tap opens a long message in full, a second tap dismisses it.
            setOnClickListener(v -> {
                if (text.getMaxLines() == 3) text.setMaxLines(Integer.MAX_VALUE);
                else setVisibility(GONE);
            });
            setVisibility(GONE);
        }

        void show(String message, Kind kind) {
            removeCallbacks(hide);
            if (message == null || message.isEmpty()) {
                setVisibility(GONE);
                return;
            }
            text.setMaxLines(3);
            text.setText(message);
            spinner.setVisibility(kind == Kind.BUSY ? VISIBLE : GONE);
            int edge = kind == Kind.BAD ? palette.bad : kind == Kind.OK ? palette.ok : palette.border;
            shape.setStroke(dp(getContext(), kind == Kind.BAD || kind == Kind.OK ? 2 : 1), edge);
            setVisibility(VISIBLE);
            if (kind == Kind.OK) postDelayed(hide, 6000);
        }
    }
}
