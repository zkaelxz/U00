package app.baihe.shell;

/** Decides which WebView load errors the shell covers with its own retry screen, and how soon it retries. */
final class LoadFailure {
    /** Quiet retries before the person is asked to tap Retry; a busy PC can take 20 s or more to answer, so the person sees "Connecting" for about half a minute before being asked to tap Retry. */
    static final int AUTO_RETRIES = 4;
    private static final long[] DELAYS_MS = {2000, 5000, 10000, 15000};

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
