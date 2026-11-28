package app.baihe.shell;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.io.File;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.List;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class SavedChaptersTest {
    @Rule public TemporaryFolder tmp = new TemporaryFolder();

    private List<File> pages(int count) throws Exception {
        File src = tmp.newFolder();
        List<File> out = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            File page = new File(src, "page-" + i + ".jpg");
            Files.write(page.toPath(), new byte[] {(byte) i});
            out.add(page);
            // The original kept beside a redraw is not part of the saved chapter.
            File orig = new File(src, "page-" + i + ".orig.jpg");
            Files.write(orig.toPath(), new byte[] {9});
            out.add(orig);
        }
        return out;
    }

    @Test
    public void aSavedChapterKeepsItsTranslatedPagesInOrderAndItsTitle() throws Exception {
        File root = new File(tmp.getRoot(), "saved");
        SavedChapters.Saved saved = SavedChapters.save(root, pages(3), "Night Owl\n ch 3");
        assertEquals(3, saved.pages);
        assertEquals("Night Owl ch 3", saved.title);
        List<File> back = SavedChapters.pagesOf(saved.dir);
        assertEquals(3, back.size());
        assertEquals(2, Files.readAllBytes(back.get(2).toPath())[0]);
        assertEquals("Night Owl ch 3", SavedChapters.list(root).get(0).title);
    }

    @Test
    public void nothingToSaveSavesNothing() throws Exception {
        File root = new File(tmp.getRoot(), "saved");
        assertNull(SavedChapters.save(root, new ArrayList<>(), "x"));
        assertEquals(0, SavedChapters.list(root).size());
        assertEquals(0, root.listFiles().length);
    }

    @Test
    public void theOldestChapterIsDroppedPastTheLimitAndDeletingRemovesIt() throws Exception {
        File root = new File(tmp.getRoot(), "saved");
        for (int i = 0; i < SavedChapters.MAX_CHAPTERS + 2; i++) {
            SavedChapters.Saved saved = SavedChapters.save(root, pages(1), "c" + i);
            assertEquals(i < SavedChapters.MAX_CHAPTERS ? 0 : 1, saved.removed);
            Thread.sleep(2);
        }
        List<SavedChapters.Saved> all = SavedChapters.list(root);
        assertEquals(SavedChapters.MAX_CHAPTERS, all.size());
        assertEquals("c" + (SavedChapters.MAX_CHAPTERS + 1), all.get(0).title);
        SavedChapters.delete(all.get(0).dir);
        assertFalse(all.get(0).dir.exists());
        assertEquals(SavedChapters.MAX_CHAPTERS - 1, SavedChapters.list(root).size());
    }

    @Test
    public void onlyAFolderDirectlyInsideTheRootIsOpened() throws Exception {
        File root = new File(tmp.getRoot(), "saved");
        SavedChapters.Saved saved = SavedChapters.save(root, pages(1), "x");
        assertTrue(SavedChapters.isSavedDir(root, saved.dir));
        assertFalse(SavedChapters.isSavedDir(root, root));
        assertFalse(SavedChapters.isSavedDir(root, tmp.newFolder()));
        assertFalse(SavedChapters.isSavedDir(root, new File(saved.dir, "..")));
        assertFalse(SavedChapters.isSavedDir(root, null));
    }
}
