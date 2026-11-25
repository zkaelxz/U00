package app.baihe.shell;

import android.content.ClipData;
import android.content.ClipDescription;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.SharedPreferences;
import java.util.HashSet;
import java.util.Set;

/** Notices a video or comic link the person just copied, once per link, without reading the clipboard on every return. */
final class ClipboardOffer {
    /** The last links already offered; older ones may be offered again. */
    private static final int REMEMBER = 40;
    private static final String SEEN_TS = "clip_seen_ts";
    private static final String OFFERED = "clip_offered";

    private ClipboardOffer() {}

    /**
     * The link to offer, or null. The clip's description and time are read first because they cost
     * nothing; Android announces reading the text itself ("Baihe pasted from your clipboard"), so the
     * text is read once per newly copied thing and never on each return to the app.
     */
    static String pending(Context context) {
        SharedPreferences prefs = ShellSettings.prefs(context);
        if (!ShellSettings.isOn(context, ShellSettings.CLIPBOARD)) return null;
        ClipboardManager clipboard = context.getSystemService(ClipboardManager.class);
        if (clipboard == null || !clipboard.hasPrimaryClip()) return null;
        ClipDescription description = clipboard.getPrimaryClipDescription();
        if (description == null || !description.hasMimeType(ClipDescription.MIMETYPE_TEXT_PLAIN)) return null;
        long stamp = description.getTimestamp();
        if (stamp <= prefs.getLong(SEEN_TS, 0)) return null;
        prefs.edit().putLong(SEEN_TS, stamp).apply();
        ClipData clip = clipboard.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) return null;
        CharSequence text = clip.getItemAt(0).coerceToText(context);
        String link = ShareLink.firstLink(text == null ? null : text.toString());
        if (link == null || LinkKind.of(link) == LinkKind.Kind.NONE || ServerAddress.browsable(link) == null) return null;
        if (alreadyOffered(prefs, link)) return null;
        remember(prefs, link);
        return link;
    }

    static boolean alreadyOffered(SharedPreferences prefs, String link) {
        return prefs.getStringSet(OFFERED, new HashSet<>()).contains(key(link));
    }

    private static void remember(SharedPreferences prefs, String link) {
        Set<String> kept = new HashSet<>(prefs.getStringSet(OFFERED, new HashSet<>()));
        // A set has no order, so when it is full the oldest cannot be told apart: start again.
        if (kept.size() >= REMEMBER) kept.clear();
        kept.add(key(link));
        prefs.edit().putStringSet(OFFERED, kept).apply();
    }

    /** A short stand-in for the link, so the phone does not keep a list of what was copied. */
    static String key(String link) {
        return Integer.toHexString(link.hashCode());
    }
}
