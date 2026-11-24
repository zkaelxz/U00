package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** The pages of the Quick read now open, shared between the grab that fills it and the reader showing it. */
final class QuickReadSession {
    private static final List<String> names = new ArrayList<>();
    private static boolean complete = true;
    private static boolean abandoned;
    private static boolean cancelled;
    private static String summary = "";
    private static Runnable cancelHook;
    private static Runnable listener;
    // Reading ahead: what fetches the chapter after this one, and the first page of the newest chapter.
    private static Runnable nextHook;
    private static int newestFrom;
    private static final int PAGES_BEFORE_END = 3;

    private QuickReadSession() {}

    static synchronized void begin() {
        names.clear();
        complete = false;
        abandoned = false;
        cancelled = false;
        summary = "";
        nextHook = null;
        newestFrom = 0;
    }

    /** Offers the chapter after a finished one; the reader takes it up once the last pages are near. */
    static synchronized void offerNext(Runnable hook) {
        if (!complete || abandoned || cancelled) return;
        nextHook = hook;
        notifyListener();
    }

    /**
     * The reader reports the last page on screen. The offered chapter is fetched once, and only
     * after the reader has reached the newest chapter: otherwise a run of very short chapters
     * would keep fetching, and spending, with nobody reading.
     */
    static void reached(int lastVisiblePage) {
        Runnable hook;
        synchronized (QuickReadSession.class) {
            if (nextHook == null || !complete || abandoned || cancelled) return;
            if (lastVisiblePage < Math.max(names.size() - PAGES_BEFORE_END, newestFrom)) return;
            hook = nextHook;
            nextHook = null;
        }
        hook.run();
    }

    /** The next chapter is on its way: its pages join the ones already open. */
    static synchronized void resume() {
        if (abandoned || cancelled) return;
        complete = false;
        newestFrom = names.size();
        notifyListener();
    }

    /** What stopping the grab does (tell the server, end the batches); set while the grab runs. */
    static synchronized void onCancel(Runnable hook) {
        cancelHook = hook;
    }

    /** Cancel from the reader: the pages already read stay on screen, the rest are not fetched. */
    static void cancel() {
        Runnable hook;
        synchronized (QuickReadSession.class) {
            cancelled = true;
            complete = true;
            nextHook = null;
            hook = cancelHook;
            notifyListener();
        }
        if (hook != null) hook.run();
    }

    static synchronized void add(String name) {
        names.add(name);
        notifyListener();
    }

    static synchronized void finish() {
        complete = true;
        notifyListener();
    }

    /** Ends the grab and leaves its closing line under the pages, so a stop is never silent. */
    static synchronized void finish(String closingLine) {
        summary = closingLine == null ? "" : closingLine;
        finish();
    }

    static synchronized String summary() {
        return summary;
    }

    /** The reader was closed: the grab stops asking the server for more pages. */
    static void abandon() {
        Runnable hook = null;
        synchronized (QuickReadSession.class) {
            // Closing the reader mid-grab also stops the server's work, as Cancel does.
            if (!complete) hook = cancelHook;
            abandoned = true;
            complete = true;
            names.clear();
            summary = "";
            listener = null;
            nextHook = null;
        }
        if (hook != null) hook.run();
    }

    static synchronized boolean isAbandoned() {
        return abandoned || cancelled;
    }

    static synchronized boolean isComplete() {
        return complete;
    }

    static synchronized List<String> snapshot() {
        return new ArrayList<>(names);
    }

    static synchronized void listen(Runnable onChange) {
        listener = onChange;
    }

    private static void notifyListener() {
        if (listener != null) listener.run();
    }
}
