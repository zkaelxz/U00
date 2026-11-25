package app.baihe.shell;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** Translated chapters kept on the phone for reading offline: one folder of page pictures per chapter, with its title beside them. */
final class SavedChapters {
    private SavedChapters() {}

    /** The oldest chapter is dropped when a new one would pass this, so saving cannot fill the phone. */
    static final int MAX_CHAPTERS = 20;
    private static final String TITLE_FILE = "title.txt";

    static File root(android.content.Context context) {
        return new File(context.getFilesDir(), "saved");
    }

    static final class Saved {
        final File dir;
        final String title;
        final int pages;

        Saved(File dir, String title, int pages) {
            this.dir = dir;
            this.title = title;
            this.pages = pages;
        }
    }

    /** Copies the translated pages (not the originals kept beside them) into a new folder; the saved chapter, or null when no page could be copied. */
    static Saved save(File root, List<File> pages, String title) throws IOException {
        if (!root.isDirectory() && !root.mkdirs()) throw new IOException("Cannot create the saved folder.");
        File dir = new File(root, String.format("%013d", System.currentTimeMillis()));
        // Two saves in one millisecond must not share a folder.
        while (dir.exists()) dir = new File(root, dir.getName() + "x");
        if (!dir.mkdirs()) throw new IOException("Cannot create the saved folder.");
        int copied = 0;
        try {
            for (File page : pages) {
                if (!page.isFile() || page.getName().endsWith(".orig.jpg")) continue;
                copy(page, new File(dir, String.format("%04d.jpg", copied)));
                copied++;
            }
            if (copied == 0) {
                delete(dir);
                return null;
            }
            Files.write(new File(dir, TITLE_FILE).toPath(), clean(title).getBytes(StandardCharsets.UTF_8));
        } catch (IOException e) {
            // A half-copied chapter would read as a short one.
            delete(dir);
            throw e;
        }
        trim(root);
        return new Saved(dir, clean(title), copied);
    }

    /** Saved chapters, newest first. */
    static List<Saved> list(File root) {
        List<Saved> out = new ArrayList<>();
        File[] dirs = root.listFiles(File::isDirectory);
        if (dirs == null) return out;
        Arrays.sort(dirs, (a, b) -> b.getName().compareTo(a.getName()));
        for (File dir : dirs) {
            List<File> pages = pagesOf(dir);
            if (pages.isEmpty()) continue;
            String title = "Saved chapter";
            try {
                File named = new File(dir, TITLE_FILE);
                if (named.isFile()) title = clean(new String(Files.readAllBytes(named.toPath()), StandardCharsets.UTF_8));
            } catch (IOException ignored) {
                // The chapter still opens under a plain name.
            }
            out.add(new Saved(dir, title.isEmpty() ? "Saved chapter" : title, pages.size()));
        }
        return out;
    }

    /** The picture files of one saved chapter, in reading order. */
    static List<File> pagesOf(File dir) {
        File[] files = dir.listFiles((d, name) -> name.endsWith(".jpg"));
        if (files == null) return new ArrayList<>();
        Arrays.sort(files, (a, b) -> a.getName().compareTo(b.getName()));
        return new ArrayList<>(Arrays.asList(files));
    }

    /** True when `dir` is a folder directly inside the saved root, so an intent cannot point the reader elsewhere. */
    static boolean isSavedDir(File root, File dir) {
        try {
            return dir != null && dir.isDirectory() && root.getCanonicalFile().equals(dir.getCanonicalFile().getParentFile());
        } catch (IOException e) {
            return false;
        }
    }

    static void delete(File dir) {
        File[] files = dir.listFiles();
        if (files != null) for (File f : files) f.delete();
        dir.delete();
    }

    private static void trim(File root) {
        List<Saved> all = list(root);
        for (int i = MAX_CHAPTERS; i < all.size(); i++) delete(all.get(i).dir);
    }

    private static String clean(String title) {
        String text = title == null ? "" : title.replaceAll("\\s+", " ").trim();
        return text.length() > 80 ? text.substring(0, 80) : text;
    }

    private static void copy(File from, File to) throws IOException {
        try (InputStream in = new FileInputStream(from); OutputStream out = new FileOutputStream(to)) {
            byte[] buffer = new byte[16384];
            int n;
            while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
        }
    }
}
