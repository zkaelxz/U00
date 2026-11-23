package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** The pages of the Quick read now open, shared between the grab that fills it and the reader showing it. */
final class QuickReadSession {
    private static final List<String> names = new ArrayList<>();
    private static boolean complete = true;
    private static boolean abandoned;
    private static Runnable listener;

    private QuickReadSession() {}

    static synchronized void begin() {
        names.clear();
        complete = false;
        abandoned = false;
    }

    static synchronized void add(String name) {
        names.add(name);
        notifyListener();
    }

    static synchronized void finish() {
        complete = true;
        notifyListener();
    }

    /** The reader was closed: the grab stops asking the server for more pages. */
    static synchronized void abandon() {
        abandoned = true;
        complete = true;
        names.clear();
        listener = null;
    }

    static synchronized boolean isAbandoned() {
        return abandoned;
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
