package app.baihe.shell;

import org.json.JSONException;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/** The Watch page player's report to the lock-screen controls, checked before the phone shows it, and the presses sent back. */
final class MediaState {
    enum Status { PLAYING, PAUSED, STOPPED }

    static final String PLAY = "play";
    static final String PAUSE = "pause";
    static final String FORWARD = "forward";
    static final String BACK = "back";
    static final String SEEK = "seek";
    static final double SKIP_SECONDS = 10;
    static final int MAX_TEXT = 200;
    /** The bridge call is synchronous, so a report older than this is a clock change, not a delay. */
    static final long MAX_AGE_MS = 5_000;
    private static final int MAX_JSON = 4096;
    private static final List<String> PAGE_ACTIONS = Arrays.asList(PLAY, PAUSE, FORWARD, BACK, SEEK);

    final Status status;
    final long positionMs;
    /** -1 while the player does not know it. */
    final long durationMs;
    final float speed;
    final String title;
    final String artist;
    /** The page's wall clock when it read the position; 0 when it did not say. */
    final long reportedAtMs;

    private MediaState(Status status, long positionMs, long durationMs, float speed, String title, String artist, long reportedAtMs) {
        this.status = status;
        this.positionMs = positionMs;
        this.durationMs = durationMs;
        this.speed = speed;
        this.title = title;
        this.artist = artist;
        this.reportedAtMs = reportedAtMs;
    }

    /** The page's JSON, or null when it is not a report this app understands. */
    static MediaState parse(String json) {
        if (json == null || json.length() > MAX_JSON) return null;
        try {
            JSONObject o = new JSONObject(json);
            Status status = statusOf(o.opt("state"));
            if (status == null) return null;
            double duration = number(o, "duration");
            long durationMs = duration > 0 ? Math.round(duration * 1000) : -1;
            long positionMs = Math.round(Math.max(0, number(o, "position")) * 1000);
            if (durationMs > 0) positionMs = Math.min(positionMs, durationMs);
            double rate = number(o, "rate");
            float speed = rate > 0 && rate <= 16 ? (float) rate : 1f;
            double at = number(o, "at");
            return new MediaState(status, positionMs, durationMs, speed, text(o, "title"), text(o, "artist"),
                    at > 0 ? (long) at : 0);
        } catch (JSONException e) {
            return null;
        }
    }

    /** How long ago the page read the position, so the lock screen's clock starts where the sound really is. */
    long ageMs(long nowMs) {
        if (reportedAtMs <= 0) return 0;
        return Math.max(0, Math.min(MAX_AGE_MS, nowMs - reportedAtMs));
    }

    /** The rate the system moves the shown position at; a paused player's place stands still. */
    float extrapolationSpeed() {
        return status == Status.PLAYING ? speed : 0f;
    }

    /**
     * The script that hands a press to the page, or null for an action the page does not take. Only
     * fixed names and a number reach the script, so nothing from outside is ever run as code.
     */
    static String pageScript(String action, double seconds) {
        if (!PAGE_ACTIONS.contains(action)) return null;
        double value = Double.isFinite(seconds) ? Math.max(0, seconds) : 0;
        return String.format(Locale.ROOT, "window.__baiheMedia&&window.__baiheMedia('%s',%s)", action,
                Double.toString(value));
    }

    private static Status statusOf(Object state) {
        if ("playing".equals(state)) return Status.PLAYING;
        if ("paused".equals(state)) return Status.PAUSED;
        if ("stopped".equals(state)) return Status.STOPPED;
        return null;
    }

    /** NaN for a missing, null or non-number field; Android's and the test library's org.json differ on those. */
    private static double number(JSONObject o, String key) {
        Object v = o.opt(key);
        if (!(v instanceof Number)) return Double.NaN;
        double d = ((Number) v).doubleValue();
        return Double.isFinite(d) ? d : Double.NaN;
    }

    private static String text(JSONObject o, String key) {
        Object v = o.opt(key);
        if (!(v instanceof String)) return "";
        String s = ((String) v).trim();
        if (s.length() <= MAX_TEXT) return s;
        // Never half a character: a cut surrogate pair shows as a broken glyph.
        int end = Character.isHighSurrogate(s.charAt(MAX_TEXT - 1)) ? MAX_TEXT - 1 : MAX_TEXT;
        return s.substring(0, end);
    }
}
