package app.baihe.shell;

/** Turns the server's translate-run state for a saved title into the phone's status line. */
final class TitleProgress {
    private TitleProgress() {}

    /** Poll gap while the app is open; the run is on the PC, so a quicker check would only cost it. */
    static final long POLL_MS = 5_000;
    /** A run still going after this long is left to the Jobs page rather than polled for ever. */
    static final long GIVE_UP_MS = 30 * 60 * 1000L;

    /** True once the run can no longer change: nothing queued, finished, or stopped. */
    static boolean settled(String state) {
        return !("waiting".equals(state) || "running".equals(state));
    }

    /** The line to show, or null when the title has no translate run to report. `reason` is the server's
     * already shortened why for a stopped run, empty when it gave none. */
    static String line(String state, String step, String title, String reason) {
        switch (state == null ? "" : state) {
            case "waiting":
                return "Waiting to translate " + title;
            case "running":
                if ("run_translate".equals(step) || "run_scanlate".equals(step)) return "Translating " + title;
                return "Preparing " + title + " for translating";
            case "done":
                return "Translated " + title;
            case "stopped":
                if (reason == null || reason.trim().isEmpty()) return "Translating " + title + " stopped. See the Jobs page.";
                return "Translating " + title + " stopped: " + reason.trim();
            default:
                return null;
        }
    }
}
