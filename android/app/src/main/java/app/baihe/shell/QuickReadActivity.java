package app.baihe.shell;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.util.LruCache;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.ImageView;
import android.widget.ListView;

import java.io.File;

/**
 * Shows the typeset pages Grab's Quick read got back, one after another. The pages are temporary
 * files in the app's cache that are deleted when this screen closes, so nothing reaches the
 * library and nothing stays on the phone.
 */
public class QuickReadActivity extends Activity {
    static final String EXTRA_FILES = "files";

    private File[] files = new File[0];
    private LruCache<Integer, Bitmap> cache;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        String[] names = getIntent().getStringArrayExtra(EXTRA_FILES);
        File dir = quickReadDir(this);
        if (names != null) {
            files = new File[names.length];
            for (int i = 0; i < names.length; i++) {
                // Only a bare name inside the cache folder is opened, never a path an intent chose.
                files[i] = new File(dir, new File(names[i]).getName());
            }
        }
        cache = new LruCache<>(6);
        ListView list = new ListView(this);
        list.setDivider(null);
        list.setAdapter(new PageAdapter());
        setContentView(list);
        list.setFitsSystemWindows(true);
    }

    @Override
    protected void onDestroy() {
        clear(this);
        super.onDestroy();
    }

    static File quickReadDir(android.content.Context context) {
        File dir = new File(context.getCacheDir(), "quickread");
        if (!dir.isDirectory()) dir.mkdirs();
        return dir;
    }

    /** Deletes every temporary page; also called before a new Quick read starts. */
    static void clear(android.content.Context context) {
        File[] old = quickReadDir(context).listFiles();
        if (old == null) return;
        for (File f : old) f.delete();
    }

    private Bitmap load(int position) {
        Bitmap cached = cache.get(position);
        if (cached != null) return cached;
        int width = getResources().getDisplayMetrics().widthPixels;
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(files[position].getPath(), bounds);
        int sample = 1;
        // Pages are often much wider than the screen; half-size steps keep them sharp enough.
        while (bounds.outWidth / (sample * 2) >= width) sample *= 2;
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        Bitmap bitmap = BitmapFactory.decodeFile(files[position].getPath(), options);
        if (bitmap != null) cache.put(position, bitmap);
        return bitmap;
    }

    private final class PageAdapter extends BaseAdapter {
        @Override public int getCount() { return files.length; }
        @Override public Object getItem(int position) { return files[position]; }
        @Override public long getItemId(int position) { return position; }

        @Override
        public View getView(int position, View convert, ViewGroup parent) {
            ImageView view = convert instanceof ImageView ? (ImageView) convert : new ImageView(QuickReadActivity.this);
            view.setAdjustViewBounds(true);
            view.setScaleType(ImageView.ScaleType.FIT_CENTER);
            view.setLayoutParams(new ListView.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            view.setImageBitmap(load(position));
            return view;
        }
    }
}
