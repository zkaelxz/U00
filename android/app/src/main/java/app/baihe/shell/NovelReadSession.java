package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** The translated parts of the novel chapter now open, shared between the grab that fills it and the reader showing it. */
final class NovelReadSession {
    private static final List<String> parts = new ArrayList<>();
    private static boolean complete = true;
    private static boolean stopped;
    private static String summary = "";
    private static int total;
    private static Runnable cancelHook;
    private static Runnable listener;

    private NovelReadSession() {}

    static synchronized void begin(int partCount) {
        parts.clear();
        complete = false;
        stopped = false;
        summary = "";
        total = partCount;
    }

    static synchronized void add(String translated) {
        parts.add(translated);
        notifyListener();
    }

    /** Ends the grab and leaves its closing line under the text, so a stop is never silent. */
    static synchronized void finish(String closingLine) {
        summary = closingLine == null ? "" : closingLine;
        complete = true;
        notifyListener();
    }

    /** What stopping does (end the translating); set while the grab runs. */
    static synchronized void onCancel(Runnable hook) {
        cancelHook = hook;
    }

    /** Cancel from the reader: the parts already read stay on screen, the rest are not translated. */
    static void cancel() {
        Runnable hook;
        synchronized (NovelReadSession.class) {
            stopped = true;
            hook = cancelHook;
        }
        if (hook != null) hook.run();
    }

    /** The reader was closed: the translating stops too. */
    static void abandon() {
        Runnable hook = null;
        synchronized (NovelReadSession.class) {
            if (!complete) hook = cancelHook;
            stopped = true;
            complete = true;
            parts.clear();
            summary = "";
            listener = null;
        }
        if (hook != null) hook.run();
    }

    static synchronized boolean isStopped() {
        return stopped;
    }

    static synchronized boolean isComplete() {
        return complete;
    }

    static synchronized String summary() {
        return summary;
    }

    static synchronized int total() {
        return total;
    }

    static synchronized List<String> snapshot() {
        return new ArrayList<>(parts);
    }

    static synchronized void listen(Runnable onChange) {
        listener = onChange;
    }

    private static void notifyListener() {
        if (listener != null) listener.run();
    }
}
