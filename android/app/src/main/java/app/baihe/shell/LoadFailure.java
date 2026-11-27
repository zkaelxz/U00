package app.baihe.shell;

/** Decides which WebView load errors the shell covers with its own retry screen, and how soon it retries. */
final class LoadFailure {
    /** Quiet retries before the person is asked to tap Retry; a server restarting or a dropped first connection usually clears in seconds. */
    static final int AUTO_RETRIES = 2;
    private static final long[] DELAYS_MS = {1500, 4000};

    private LoadFailure() {}

    /**
     * Only the page itself matters: a failed picture or script inside a loaded page must not replace it.
     * WebViewClient.ERROR_* codes are all negative; a cancelled load (a newer one began) is not a failure.
     */
    static boolean coversPage(boolean mainFrame, int errorCode) {
        return mainFrame && errorCode != -999;
    }

    /** Wait before automatic retry number {@code attempt} (0-based), or -1 once the quiet retries are spent. */
    static long retryDelayMs(int attempt) {
        return attempt >= 0 && attempt < AUTO_RETRIES ? DELAYS_MS[attempt] : -1;
    }
}
