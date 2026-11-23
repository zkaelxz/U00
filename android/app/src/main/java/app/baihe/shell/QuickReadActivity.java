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
    private final java.util.List<File> files = new java.util.ArrayList<>();
    private LruCache<Integer, Bitmap> cache;
    private PageAdapter adapter;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        cache = new LruCache<>(6);
        ListView list = new ListView(this);
        list.setDivider(null);
        adapter = new PageAdapter();
        list.setAdapter(adapter);
        refresh();
        // Pages arrive while the reader is open, so the list grows as the grab writes them.
        QuickReadSession.listen(() -> runOnUiThread(this::refresh));
        setContentView(list);
        list.setFitsSystemWindows(true);
    }

    /** Only bare names the grab itself wrote inside the cache folder are opened, never a path an intent chose. */
    private void refresh() {
        File dir = quickReadDir(this);
        files.clear();
        for (String name : QuickReadSession.snapshot()) files.add(new File(dir, new File(name).getName()));
        adapter.notifyDataSetChanged();
    }

    @Override
    protected void onDestroy() {
        QuickReadSession.abandon();
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
        BitmapFactory.decodeFile(files.get(position).getPath(), bounds);
        int sample = 1;
        // Pages are often much wider than the screen; half-size steps keep them sharp enough.
        while (bounds.outWidth / (sample * 2) >= width) sample *= 2;
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        Bitmap bitmap = BitmapFactory.decodeFile(files.get(position).getPath(), options);
        if (bitmap != null) cache.put(position, bitmap);
        return bitmap;
    }

    private final class PageAdapter extends BaseAdapter {
        @Override public int getCount() { return files.size() + (QuickReadSession.isComplete() ? 0 : 1); }
        @Override public Object getItem(int position) { return position < files.size() ? files.get(position) : null; }
        @Override public long getItemId(int position) { return position; }

        @Override
        public View getView(int position, View convert, ViewGroup parent) {
            if (position >= files.size()) {
                android.widget.TextView more = new android.widget.TextView(QuickReadActivity.this);
                more.setText("Redrawing more pages...");
                more.setTextSize(16);
                more.setGravity(android.view.Gravity.CENTER);
                int pad = (int) (24 * getResources().getDisplayMetrics().density);
                more.setPadding(pad, pad, pad, pad);
                more.setLayoutParams(new ListView.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
                return more;
            }
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
