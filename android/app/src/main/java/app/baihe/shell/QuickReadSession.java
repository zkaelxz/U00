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

    private QuickReadSession() {}

    static synchronized void begin() {
        names.clear();
        complete = false;
        abandoned = false;
        cancelled = false;
        summary = "";
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
