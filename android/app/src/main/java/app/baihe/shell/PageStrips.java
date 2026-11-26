package app.baihe.shell;

import java.util.ArrayList;
import java.util.List;

/** The strips taken of one page so far; a strip left over from another page is never joined onto this one. */
final class PageStrips<T> {
    private String page = "";
    private final List<T> strips = new ArrayList<>();

    /** Adds a strip of {@code forPage}; what was held for a different page is dropped. */
    synchronized void add(String forPage, T strip) {
        if (!forPage.equals(page)) {
            strips.clear();
            page = forPage;
        }
        strips.add(strip);
    }

    /** The strips of {@code forPage} (none when the held ones belong to another page); the buffer is emptied either way. */
    synchronized List<T> take(String forPage) {
        List<T> out = page.equals(forPage) ? new ArrayList<>(strips) : new ArrayList<>();
        clear();
        return out;
    }

    synchronized void clear() {
        strips.clear();
        page = "";
    }
}
