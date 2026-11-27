package app.baihe.shell;

import android.app.Activity;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Bundle;
import android.util.LruCache;
import android.view.GestureDetector;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.ScaleGestureDetector;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
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
    // The footer is read from the grab's state, which its thread changes at any moment; ListView throws
    // if the count moves without notifyDataSetChanged, so the adapter reads this UI-thread copy instead.
    private boolean footerRunning;
    private String footerSummary = "";
    private ListView list;
    private View tint;
    // Pages the reader flipped to the original art; the cache holds one picture per position, so a flip drops it.
    static final String EXTRA_SAVED = "saved_chapter";
    /** Set when a chapter saved on the phone is open: it has no grab behind it and is never cleared. */
    private File savedDir;
    // True even when the folder was refused, so leaving never clears or detaches a grab it does not own.
    private boolean savedMode;
    private final java.util.Set<Integer> showingOriginal = new java.util.HashSet<>();

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        cache = new LruCache<>(6);
        // Reading has long stretches with no touch, and the screen would otherwise dim mid-page.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        list = new ListView(this);
        list.setDivider(null);
        adapter = new PageAdapter();
        list.setAdapter(adapter);
        list.setOnScrollListener(new android.widget.AbsListView.OnScrollListener() {
            @Override public void onScrollStateChanged(android.widget.AbsListView view, int state) {}
            @Override public void onScroll(android.widget.AbsListView view, int first, int visible, int total) {
                if (!savedMode) QuickReadSession.reached(first + visible - 1);
            }
        });
        list.setOnItemLongClickListener((parent, view, position, id) -> {
            if (position >= files.size()) return false;
            askReport(files.get(position));
            return true;
        });
        // The list gives a click no position, and the bubble tapped is found from where the finger went down.
        final float[] down = {0f, 0f};
        list.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_DOWN) {
                down[0] = event.getX();
                down[1] = event.getY();
            }
            return false;
        });
        list.setOnItemClickListener((parent, view, position, id) -> {
            if (position >= files.size()) return;
            if (!showingOriginal.contains(position) && view.getWidth() > 0 && view.getHeight() > 0
                    && showBubble(files.get(position), position, (down[0] - view.getLeft()) / view.getWidth(),
                            (down[1] - view.getTop()) / view.getHeight())) {
                return;
            }
            flipOriginal(position);
        });
        String savedName = getIntent().getStringExtra(EXTRA_SAVED);
        savedMode = getIntent().hasExtra(EXTRA_SAVED);
        if (savedMode) {
            File candidate = new File(SavedChapters.root(this), new File(savedName == null ? "" : savedName).getName());
            if (SavedChapters.isSavedDir(SavedChapters.root(this), candidate)) savedDir = candidate;
            if (savedDir == null) {
                finish();
                return;
            }
            files.addAll(SavedChapters.pagesOf(savedDir));
            syncFooter();
        } else {
            QuickReadSession.attach();
            refresh();
            // Pages arrive while the reader is open, so the list grows as the grab writes them.
            QuickReadSession.listen(() -> runOnUiThread(this::refresh));
        }
        ZoomFrame frame = new ZoomFrame();
        frame.addView(list, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        tint = new View(this);
        tint.setClickable(false);
        frame.addView(tint, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(frame);
        frame.setFitsSystemWindows(true);
        applyNight();
    }

    /** Flips a page between the PC's redraw and the picture the site sent. */
    private void flipOriginal(int position) {
        File original = new File(files.get(position).getParentFile(), originalName(files.get(position).getName()));
        if (!original.isFile()) {
            android.widget.Toast.makeText(this, "No original kept for this page.", android.widget.Toast.LENGTH_SHORT).show();
            return;
        }
        if (!showingOriginal.remove(position)) showingOriginal.add(position);
        cache.remove(position);
        adapter.notifyDataSetChanged();
    }

    /**
     * A tap on lettering shows the text the PC read there, beside its English, and can ask for that
     * text translated again on its own. False when the tap was not on lettering, so it flips the art.
     */
    private boolean showBubble(File page, int position, float fx, float fy) {
        File meta = new File(page.getParentFile(), reportName(page.getName()));
        if (!meta.isFile()) return false;
        try {
            org.json.JSONObject info = new org.json.JSONObject(new String(
                    java.nio.file.Files.readAllBytes(meta.toPath()), java.nio.charset.StandardCharsets.UTF_8));
            org.json.JSONArray regions = info.optJSONArray("regions");
            if (regions == null) return false;
            int[][] boxes = new int[regions.length()][4];
            for (int i = 0; i < boxes.length; i++) {
                org.json.JSONObject r = regions.optJSONObject(i);
                if (r == null || r.optString("source_text").trim().isEmpty()) continue;
                boxes[i] = new int[] {r.optInt("x"), r.optInt("y"), r.optInt("w"), r.optInt("h")};
            }
            int hit = BubbleHit.at(boxes, info.optInt("width"), info.optInt("height"), fx, fy);
            if (hit < 0) return false;
            org.json.JSONObject region = regions.getJSONObject(hit);
            askBubble(region.optString("source_text").trim(), region.optString("translated_text").trim(), position);
            return true;
        } catch (java.io.IOException | org.json.JSONException e) {
            return false;
        }
    }

    private void askBubble(String source, String english, int position) {
        String shown = "Original:\n" + source + "\n\nEnglish:\n" + (english.isEmpty() ? "(none)" : english);
        android.app.AlertDialog dialog = new android.app.AlertDialog.Builder(this)
                .setMessage(shown)
                .setPositiveButton("Close", null)
                .setNegativeButton("Show art", (d, w) -> flipOriginal(position))
                .setNeutralButton("Translate again", null)
                .show();
        // Set after show() so the dialog stays open while the PC answers.
        dialog.getButton(android.app.AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> {
            v.setEnabled(false);
            ((android.widget.Button) v).setText("Translating...");
            translateAgain(source, shown, dialog);
        });
    }

    /** This text translated on its own, without the page around it, so a doubtful line has a second opinion. */
    private void translateAgain(String source, String shown, android.app.AlertDialog dialog) {
        android.content.SharedPreferences prefs = getSharedPreferences(GrabActivity.PREFS, MODE_PRIVATE);
        String server = prefs.getString(GrabActivity.KEY_SERVER, "");
        String token = prefs.getString(GrabActivity.KEY_TOKEN, "");
        String language = BubbleHit.languageOf(source);
        new Thread(() -> {
            String answer;
            try {
                if (language.isEmpty()) throw new BaiheClient.BridgeException(0, "This text is not Chinese, Japanese or Korean.");
                answer = new BaiheClient(server, token).translateText(source, language, "en");
            } catch (BaiheClient.BridgeException e) {
                answer = e.getMessage();
            }
            String second = answer;
            runOnUiThread(() -> {
                if (isFinishing() || !dialog.isShowing()) return;
                dialog.setMessage(shown + "\n\nThis text alone:\n" + second);
                dialog.getButton(android.app.AlertDialog.BUTTON_NEUTRAL).setVisibility(View.GONE);
            });
        }, "bubble-translate").start();
    }

    private android.content.SharedPreferences nightPrefs() {
        return getSharedPreferences(GrabActivity.PREFS, MODE_PRIVATE);
    }

    private void applyNight() {
        android.content.SharedPreferences p = nightPrefs();
        WindowManager.LayoutParams attrs = getWindow().getAttributes();
        attrs.screenBrightness = NightFilter.windowBrightness(p.getInt("night_brightness", 100));
        getWindow().setAttributes(attrs);
        tint.setBackgroundColor(NightFilter.tint(p.getBoolean("night_warm", false), p.getInt("night_warmth", 60)));
    }

    private void askNight() {
        android.content.SharedPreferences p = nightPrefs();
        android.widget.LinearLayout box = new android.widget.LinearLayout(this);
        box.setOrientation(android.widget.LinearLayout.VERTICAL);
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad, pad, 0);
        android.widget.TextView label = new android.widget.TextView(this);
        label.setText("Brightness");
        box.addView(label);
        android.widget.SeekBar level = new android.widget.SeekBar(this);
        level.setMax(100);
        level.setProgress(p.getInt("night_brightness", 100));
        level.setOnSeekBarChangeListener(new android.widget.SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(android.widget.SeekBar bar, int value, boolean user) {
                p.edit().putInt("night_brightness", value).apply();
                applyNight();
            }
            @Override public void onStartTrackingTouch(android.widget.SeekBar bar) {}
            @Override public void onStopTrackingTouch(android.widget.SeekBar bar) {}
        });
        box.addView(level);
        android.widget.CheckBox warm = new android.widget.CheckBox(this);
        warm.setText("Warm night colours");
        warm.setChecked(p.getBoolean("night_warm", false));
        warm.setOnCheckedChangeListener((b, on) -> {
            p.edit().putBoolean("night_warm", on).apply();
            applyNight();
        });
        box.addView(warm);
        new android.app.AlertDialog.Builder(this).setTitle("Night reading").setView(box)
                .setPositiveButton("Done", null).show();
    }

    /** The report text sits beside the picture under the same name, and goes with it. */
    private static String reportName(String pictureName) {
        return pictureName.replaceAll("\\.jpg$", ".json");
    }

    /** The sent picture sits beside the redraw; a page the PC left alone has none, since it is its own original. */
    static String originalName(String pictureName) {
        return pictureName.replaceAll("\\.jpg$", ".orig.jpg");
    }

    static void writeOriginal(android.content.Context context, String pictureName, byte[] jpeg) {
        try (java.io.FileOutputStream out = new java.io.FileOutputStream(
                new File(quickReadDir(context), originalName(pictureName)))) {
            out.write(jpeg);
        } catch (java.io.IOException ignored) {
            // The page still reads; a tap on it just has no original to show.
        }
    }

    static void writeReportText(android.content.Context context, String pictureName, String text) {
        try (java.io.FileOutputStream out = new java.io.FileOutputStream(
                new File(quickReadDir(context), reportName(pictureName)))) {
            out.write(text.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        } catch (java.io.IOException ignored) {
            // The page still reads; its report just goes without the PC's regions.
        }
    }

    private void askReport(File page) {
        if (savedMode) {
            // A saved copy has no PC-side page memory or report text beside it.
            new android.app.AlertDialog.Builder(this)
                    .setItems(new String[] {"Brightness and night colours"}, (d, which) -> askNight()).show();
            return;
        }
        new android.app.AlertDialog.Builder(this)
                .setItems(new String[] {"Send a report", "Redo this page", "Brightness and night colours", "Save to phone"},
                        (d, which) -> {
                            if (which == 0) askNote(page);
                            else if (which == 1) redoPage(page);
                            else if (which == 2) askNight();
                            else saveToPhone();
                        })
                .show();
    }

    /** Copies the finished pages into the phone's own storage so the chapter reads without the PC or a connection. */
    private void saveToPhone() {
        if (!QuickReadSession.isComplete()) {
            android.widget.Toast.makeText(this, "Wait until the chapter has finished translating.", android.widget.Toast.LENGTH_SHORT).show();
            return;
        }
        java.util.List<File> pages = new java.util.ArrayList<>(files);
        String title = QuickReadSession.title();
        File root = SavedChapters.root(this);
        android.widget.Toast.makeText(this, "Saving...", android.widget.Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            String message;
            try {
                SavedChapters.Saved saved = SavedChapters.save(root, pages, title);
                message = saved == null ? "Nothing to save yet." : "Saved " + saved.pages + " pages. Find them in Grab > More > Saved chapters.";
            } catch (java.io.IOException e) {
                message = "Could not save: the phone may be full.";
            }
            String shown = message;
            runOnUiThread(() -> android.widget.Toast.makeText(this, shown, android.widget.Toast.LENGTH_LONG).show());
        }).start();
    }

    /**
     * Asks the PC to read this page again, ignoring what it remembers, and swaps the new redraw in.
     * The picture comes from the original kept beside the page, else from the address the report
     * sidecar holds, which the PC downloads itself.
     */
    private void redoPage(File page) {
        android.content.SharedPreferences prefs = getSharedPreferences(GrabActivity.PREFS, MODE_PRIVATE);
        String server = prefs.getString(GrabActivity.KEY_SERVER, "");
        String token = prefs.getString(GrabActivity.KEY_TOKEN, "");
        File original = new File(page.getParentFile(), originalName(page.getName()));
        File text = new File(page.getParentFile(), reportName(page.getName()));
        android.widget.Toast.makeText(this, "Redoing this page...", android.widget.Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            String message = "Redone.";
            try {
                String meta = text.isFile() ? new String(java.nio.file.Files.readAllBytes(text.toPath()),
                        java.nio.charset.StandardCharsets.UTF_8) : "{}";
                org.json.JSONObject info = new org.json.JSONObject(meta);
                PageImage image;
                if (original.isFile()) {
                    String data = java.util.Base64.getEncoder().encodeToString(java.nio.file.Files.readAllBytes(original.toPath()));
                    image = new PageImage("redo", "", "image/jpeg", data);
                } else if (info.optString("image_url").startsWith("http")) {
                    image = new PageImage("redo", info.optString("image_url"), "image/jpeg", "", true);
                } else {
                    throw new BaiheClient.BridgeException(0, "This page has no picture to read again.");
                }
                BaiheClient client = new BaiheClient(server, token);
                client.redo = true;
                client.typesetMaxWidth = getResources().getDisplayMetrics().widthPixels;
                client.textStyle = GrabActivity.textStyle(prefs);
                byte[][] fresh = {null};
                client.streamPages(info.optString("page_url"), java.util.Collections.singletonList(image), answer -> {
                    try {
                        java.util.List<byte[]> jpegs = QuickRead.typesetPages(new org.json.JSONObject().put("pages",
                                new org.json.JSONArray().put(answer)));
                        if (!jpegs.isEmpty()) fresh[0] = jpegs.get(0);
                    } catch (org.json.JSONException ignored) {
                        // No redraw came back; the check below reports it.
                    }
                });
                if (fresh[0] == null) throw new BaiheClient.BridgeException(0, "The PC could not redo this page.");
                try (java.io.FileOutputStream out = new java.io.FileOutputStream(page)) {
                    out.write(fresh[0]);
                }
            } catch (BaiheClient.BridgeException e) {
                message = e.getMessage();
            } catch (Exception e) {
                message = "Could not redo this page.";
            }
            String shown = message;
            runOnUiThread(() -> {
                int at = files.indexOf(page);
                if (at >= 0) {
                    cache.remove(at);
                    showingOriginal.remove(at);
                    adapter.notifyDataSetChanged();
                }
                android.widget.Toast.makeText(this, shown, android.widget.Toast.LENGTH_LONG).show();
            });
        }, "page-redo").start();
    }

    private void askNote(File page) {
        android.widget.EditText note = new android.widget.EditText(this);
        note.setHint("What is wrong with this page? (optional)");
        note.setFilters(new android.text.InputFilter[] {
                new android.text.InputFilter.LengthFilter(PageReport.MAX_NOTE_CHARS)});
        new android.app.AlertDialog.Builder(this)
                .setTitle("Send a report")
                .setMessage("The page, what the PC read on it and its address go to your Baihe so a person can fix it.")
                .setView(note)
                .setPositiveButton("Send", (d, which) -> sendReport(page, note.getText().toString()))
                .setNegativeButton("Cancel", null)
                .show();
    }

    private void sendReport(File page, String note) {
        android.content.SharedPreferences prefs = getSharedPreferences(GrabActivity.PREFS, MODE_PRIVATE);
        String server = prefs.getString(GrabActivity.KEY_SERVER, "");
        String token = prefs.getString(GrabActivity.KEY_TOKEN, "");
        File text = new File(page.getParentFile(), reportName(page.getName()));
        new Thread(() -> {
            String message;
            try {
                byte[] jpeg = java.nio.file.Files.readAllBytes(page.toPath());
                String meta = text.isFile() ? new String(java.nio.file.Files.readAllBytes(text.toPath()),
                        java.nio.charset.StandardCharsets.UTF_8) : "";
                new BaiheClient(server, token).sendReport(PageReport.body(meta, jpeg, note));
                message = "Report sent.";
            } catch (BaiheClient.BridgeException e) {
                message = e.getMessage();
            } catch (Exception e) {
                message = "Could not send the report.";
            }
            String shown = message;
            runOnUiThread(() -> android.widget.Toast.makeText(this, shown, android.widget.Toast.LENGTH_LONG).show());
        }, "page-report").start();
    }

    /** The volume keys turn the page, so the phone can be read one-handed; most of a screen, to keep a line of overlap. */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode != KeyEvent.KEYCODE_VOLUME_DOWN && keyCode != KeyEvent.KEYCODE_VOLUME_UP) {
            return super.onKeyDown(keyCode, event);
        }
        int step = (int) (list.getHeight() * 0.85f);
        list.smoothScrollBy(keyCode == KeyEvent.KEYCODE_VOLUME_DOWN ? step : -step, 250);
        return true;
    }

    /** Swallowed with its key-down, or the system would still change the volume and show its slider. */
    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        return keyCode == KeyEvent.KEYCODE_VOLUME_DOWN || keyCode == KeyEvent.KEYCODE_VOLUME_UP
                || super.onKeyUp(keyCode, event);
    }

    /**
     * Pinch and double-tap zoom for the page list. The list itself is scaled, so it keeps scrolling
     * up and down by itself; this frame only adds the scale and the sideways drag.
     */
    private final class ZoomFrame extends FrameLayout {
        private float scale = QuickReadZoom.FIT;
        private float pan;
        // A pinch's fingers would also scroll the list, so the list is cut off until they all lift.
        private boolean pinching;
        private final ScaleGestureDetector pinch;
        private final GestureDetector taps;

        ZoomFrame() {
            super(QuickReadActivity.this);
            pinch = new ScaleGestureDetector(QuickReadActivity.this, new ScaleGestureDetector.SimpleOnScaleGestureListener() {
                @Override
                public boolean onScale(ScaleGestureDetector d) {
                    zoomTo(scale * d.getScaleFactor(), d.getFocusX(), d.getFocusY());
                    return true;
                }
            });
            // Its double-tap-and-drag zoom would fight the double-tap toggle below.
            pinch.setQuickScaleEnabled(false);
            taps = new GestureDetector(QuickReadActivity.this, new GestureDetector.SimpleOnGestureListener() {
                @Override
                public boolean onDoubleTap(MotionEvent e) {
                    zoomTo(QuickReadZoom.afterDoubleTap(scale), e.getX(), e.getY());
                    // The second tap is the zoom, not a tap or a scroll for the list underneath.
                    pinching = true;
                    return true;
                }

                @Override
                public boolean onScroll(MotionEvent down, MotionEvent move, float dx, float dy) {
                    if (scale > QuickReadZoom.FIT && !pinching) {
                        pan = QuickReadZoom.clampPan(pan - dx, scale, list.getWidth());
                        list.setTranslationX(pan);
                    }
                    return false;
                }
            });
        }

        /** focusX and focusY are in this frame; the list sits inside its padding (the system bars). */
        private void zoomTo(float wanted, float focusX, float focusY) {
            float next = QuickReadZoom.clampScale(wanted);
            pan = QuickReadZoom.panAfterZoom(pan, scale, next, focusX - list.getLeft(), list.getWidth());
            int scroll = QuickReadZoom.scrollAfterZoom(focusY - list.getTop(), scale, next);
            scale = next;
            list.setPivotX(0);
            list.setPivotY(0);
            list.setScaleX(scale);
            list.setScaleY(scale);
            list.setTranslationX(pan);
            ViewGroup.LayoutParams size = list.getLayoutParams();
            int height = scale == QuickReadZoom.FIT
                    ? ViewGroup.LayoutParams.MATCH_PARENT
                    : QuickReadZoom.listHeight(getHeight() - getPaddingTop() - getPaddingBottom(), scale);
            if (size.height != height) {
                size.height = height;
                list.setLayoutParams(size);
            }
            if (scroll != 0) list.scrollListBy(scroll);
        }

        @Override
        public boolean dispatchTouchEvent(MotionEvent event) {
            // Before the detectors: a double-tap sets it again for its own second tap.
            if (event.getActionMasked() == MotionEvent.ACTION_DOWN) pinching = false;
            boolean wasPinching = pinching;
            pinch.onTouchEvent(event);
            taps.onTouchEvent(event);
            // The list never saw a double-tap's second press, so it has nothing to cancel.
            if (pinching && !wasPinching) return true;
            if (event.getPointerCount() > 1 && !pinching) {
                pinching = true;
                MotionEvent cancel = MotionEvent.obtain(event);
                cancel.setAction(MotionEvent.ACTION_CANCEL);
                super.dispatchTouchEvent(cancel);
                cancel.recycle();
            }
            return pinching || super.dispatchTouchEvent(event);
        }
    }

    private void syncFooter() {
        footerRunning = !QuickReadSession.isComplete();
        footerSummary = QuickReadSession.summary();
    }

    /** Only bare names the grab itself wrote inside the cache folder are opened, never a path an intent chose. */
    private void refresh() {
        File dir = quickReadDir(this);
        files.clear();
        for (String name : QuickReadSession.snapshot()) files.add(new File(dir, new File(name).getName()));
        syncFooter();
        adapter.notifyDataSetChanged();
        // A reader already waiting at the end does not scroll again, so the offer is checked here too.
        QuickReadSession.reached(list.getLastVisiblePosition());
    }

    @Override
    protected void onDestroy() {
        // A chapter still being translated keeps going without the reader; its pages stay for the next open.
        if (!savedMode && !QuickReadSession.detach()) {
            QuickReadSession.abandon();
            clear(this);
        }
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

    private File fileFor(int position) {
        File page = files.get(position);
        if (!showingOriginal.contains(position)) return page;
        File original = new File(page.getParentFile(), originalName(page.getName()));
        return original.isFile() ? original : page;
    }

    private Bitmap load(int position) {
        Bitmap cached = cache.get(position);
        if (cached != null) return cached;
        File source = fileFor(position);
        int width = getResources().getDisplayMetrics().widthPixels;
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeFile(source.getPath(), bounds);
        int sample = 1;
        // Pages are often much wider than the screen; half-size steps keep them sharp enough.
        while (bounds.outWidth / (sample * 2) >= width) sample *= 2;
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inSampleSize = sample;
        Bitmap bitmap = BitmapFactory.decodeFile(source.getPath(), options);
        if (bitmap != null) cache.put(position, bitmap);
        return bitmap;
    }

    private final class PageAdapter extends BaseAdapter {
        @Override public int getCount() { return files.size() + (footerShown() ? 1 : 0); }
        private boolean footerShown() {
            return footerRunning || !footerSummary.isEmpty();
        }
        @Override public Object getItem(int position) { return position < files.size() ? files.get(position) : null; }
        @Override public long getItemId(int position) { return position; }

        @Override
        public View getView(int position, View convert, ViewGroup parent) {
            if (position >= files.size()) {
                android.widget.LinearLayout row = new android.widget.LinearLayout(QuickReadActivity.this);
                row.setOrientation(android.widget.LinearLayout.VERTICAL);
                row.setGravity(android.view.Gravity.CENTER);
                int pad = (int) (24 * getResources().getDisplayMetrics().density);
                row.setPadding(pad, pad, pad, pad);
                android.widget.TextView more = new android.widget.TextView(QuickReadActivity.this);
                boolean running = footerRunning;
                more.setText(running ? "Redrawing more pages (" + files.size() + " so far)..." : footerSummary);
                more.setTextSize(16);
                row.addView(more);
                if (!running) {
                    row.setLayoutParams(new ListView.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
                    return row;
                }
                android.widget.Button stop = new android.widget.Button(QuickReadActivity.this);
                stop.setText("Cancel");
                stop.setOnClickListener(v -> QuickReadSession.cancel());
                row.addView(stop);
                row.setLayoutParams(new ListView.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
                return row;
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
