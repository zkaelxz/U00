package app.baihe.shell;

import android.app.Activity;
import android.media.AudioManager;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.text.Layout;
import android.text.Spannable;
import android.text.style.BackgroundColorSpan;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;
import java.util.Locale;

/**
 * Shows a novel chapter that Grab's Quick read translated on the PC. The text is kept only in memory
 * and is gone when this screen closes, so nothing reaches the library and nothing stays on the phone.
 */
public class NovelReadActivity extends Activity {
    private TextView body;
    private TextView status;
    private Button cancel;
    private Button play;
    private ScrollView scroll;

    // Read aloud. Each utterance id carries a generation, so callbacks for speech a pause flushed are ignored.
    private TextToSpeech tts;
    private boolean ttsReady;
    private boolean speechMissing;
    private boolean reading;
    private int generation;
    /** Where reading resumes: the start of the sentence being read, or the end of the one just read. */
    private int readFrom;
    /** The end of the last sentence handed to the voice; text after it is queued as it arrives. */
    private int queuedTo;
    private int spokenTo;
    private int highlightStart = -1, highlightEnd = -1;
    private BackgroundColorSpan highlight;
    private int highlightColor;
    private String speechNote = "";
    private static final String KEY_READ_FROM = "readFrom";

    @Override
    protected void onCreate(Bundle state) {
        setTheme(GrabUi.Palette.saved(this).themeRes());
        super.onCreate(state);
        // Reading has long stretches with no touch, and the screen would otherwise dim mid-paragraph.
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        GrabUi.Palette palette = GrabUi.Palette.saved(this);
        getWindow().setStatusBarColor(palette.bg);
        getWindow().setNavigationBarColor(palette.surface);
        if (!palette.dark) {
            getWindow().getDecorView().setSystemUiVisibility(android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                    | android.view.View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
        }
        // The accent at a quarter strength over the page shows on every look and keeps the text readable on it.
        highlightColor = (palette.accent & 0x00FFFFFF) | 0x40000000;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(palette.bg);
        root.setFitsSystemWindows(true);

        scroll = new ScrollView(this);
        body = new TextView(this);
        body.setTextColor(palette.text);
        body.setTextSize(18);
        body.setLineSpacing(0, 1.35f);
        body.setTextIsSelectable(true);
        int pad = GrabUi.dp(this, 16);
        body.setPadding(pad, pad, pad, pad);
        scroll.addView(body);
        root.addView(scroll, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout footer = new LinearLayout(this);
        footer.setOrientation(LinearLayout.VERTICAL);
        footer.setBackgroundColor(palette.surface);
        footer.setPadding(pad, GrabUi.dp(this, 8), pad, GrabUi.dp(this, 8));
        status = new TextView(this);
        status.setTextColor(palette.muted);
        status.setTextSize(14);
        footer.addView(status);
        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        play = new Button(this);
        play.setText("Read aloud");
        play.setOnClickListener(v -> togglePlay());
        buttons.addView(play, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        cancel = new Button(this);
        cancel.setText("Cancel");
        cancel.setOnClickListener(v -> NovelReadSession.cancel());
        buttons.addView(cancel, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        footer.addView(buttons, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(footer, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(root);

        // The voice plays on the media stream, so the volume keys reach it while it reads.
        setVolumeControlStream(AudioManager.STREAM_MUSIC);
        // Turning the phone recreates the screen; the chapter text lives in the session, the place here.
        if (state != null) readFrom = state.getInt(KEY_READ_FROM, 0);
        refresh();
        if (readFrom > 0 && readFrom < body.length()) play.setText("Resume");
        else readFrom = 0;
        // Parts arrive while the reader is open, so the text grows as the grab translates them.
        NovelReadSession.listen(() -> runOnUiThread(this::refresh));
    }

    private void refresh() {
        List<String> parts = NovelReadSession.snapshot();
        body.setText(String.join("\n\n", parts), TextView.BufferType.SPANNABLE);
        highlight = null;
        // Parts are only ever added at the end; shorter text means a different chapter, so reading starts over.
        if (queuedTo > body.length() || highlightEnd > body.length()) resetReading();
        if (highlightEnd > highlightStart) showSentence(highlightStart, highlightEnd, false);
        boolean running = !NovelReadSession.isComplete() && !NovelReadSession.isStopped();
        String line = running
                ? "Translating more (" + parts.size() + " of " + NovelReadSession.total() + " parts so far)..."
                : NovelReadSession.summary();
        if (!speechNote.isEmpty()) line = line.isEmpty() ? speechNote : line + "\n" + speechNote;
        status.setText(line);
        cancel.setVisibility(running ? View.VISIBLE : View.GONE);
        play.setVisibility(body.length() == 0 || speechMissing ? View.GONE : View.VISIBLE);
        status.setVisibility(line.isEmpty() ? View.GONE : View.VISIBLE);
        // A part that arrives while reading is read on into, with no new press of the button.
        if (reading && ttsReady) {
            queueMore();
            finishIfDone();
        }
    }

    private void togglePlay() {
        if (reading) {
            pause();
            return;
        }
        // Before reading is set, or this refresh would queue from where the paused queue ended.
        if (!speechNote.isEmpty()) {
            speechNote = "";
            refresh();
        }
        reading = true;
        play.setText("Pause");
        if (tts == null) {
            // Made on first use, so a reader that never plays never binds the phone's speech engine.
            tts = new TextToSpeech(this, this::onSpeechReady);
        } else if (ttsReady) {
            speakFrom(readFrom);
        }
    }

    private void onSpeechReady(int result) {
        if (isDestroyed()) return;
        if (result != TextToSpeech.SUCCESS) {
            // With no engine at all the error comes from inside the constructor, before tts is assigned.
            body.post(() -> {
                if (!isDestroyed()) speechUnavailable();
            });
            return;
        }
        if (tts == null) return;
        // The phone's own English (en-GB, say) when it is set to one, else US English, else the voice it has.
        Locale want = "en".equals(Locale.getDefault().getLanguage()) ? Locale.getDefault() : Locale.US;
        if (tts.setLanguage(want) < TextToSpeech.LANG_AVAILABLE) tts.setLanguage(Locale.ENGLISH);
        // The PC's default narration pace.
        tts.setSpeechRate(0.9f);
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override
            public void onStart(String id) {
                runOnUiThread(() -> onSentenceStart(id));
            }

            @Override
            public void onDone(String id) {
                runOnUiThread(() -> onSentenceDone(id));
            }

            @Override
            @SuppressWarnings("deprecation")
            public void onError(String id) {
                runOnUiThread(() -> onSentenceError(id));
            }
        });
        ttsReady = true;
        if (reading) speakFrom(readFrom);
    }

    private void speechUnavailable() {
        reading = false;
        ttsReady = false;
        speechMissing = true;
        if (tts != null) tts.shutdown();
        tts = null;
        speechNote = "Read aloud needs a text-to-speech voice, and none is turned on in this phone's settings.";
        refresh();
    }

    private void speakFrom(int offset) {
        generation++;
        tts.stop();
        readFrom = offset;
        queuedTo = offset;
        spokenTo = offset;
        queueMore();
        finishIfDone();
    }

    private void queueMore() {
        String text = body.getText().toString();
        int max = Math.min(ReadAloudText.MAX_CHARS, TextToSpeech.getMaxSpeechInputLength() - 1);
        for (ReadAloudText.Sentence s : ReadAloudText.sentences(text, queuedTo, max)) {
            String id = generation + ":" + s.start + ":" + s.end;
            tts.speak(text.substring(s.start, s.end), TextToSpeech.QUEUE_ADD, null, id);
            queuedTo = s.end;
        }
    }

    /** At the end of the text shown: stop once the chapter is all there, else wait for its next part. */
    private void finishIfDone() {
        if (!reading || spokenTo < queuedTo) return;
        if (!NovelReadSession.isComplete() && !NovelReadSession.isStopped()) return;
        resetReading();
    }

    private void resetReading() {
        reading = false;
        generation++;
        if (tts != null) tts.stop();
        readFrom = 0;
        queuedTo = 0;
        spokenTo = 0;
        clearHighlight();
        play.setText("Read aloud");
    }

    private void pause() {
        reading = false;
        generation++;
        if (tts != null) tts.stop();
        play.setText(readFrom > 0 ? "Resume" : "Read aloud");
    }

    /** The sentence a callback names, or null when it belongs to speech flushed since. */
    private int[] sentenceOf(String id) {
        String[] bits = id == null ? new String[0] : id.split(":");
        if (!reading || bits.length != 3 || !bits[0].equals(String.valueOf(generation))) return null;
        return new int[] {Integer.parseInt(bits[1]), Integer.parseInt(bits[2])};
    }

    private void onSentenceStart(String id) {
        int[] s = sentenceOf(id);
        if (s == null) return;
        readFrom = s[0];
        showSentence(s[0], s[1], true);
    }

    private void onSentenceDone(String id) {
        int[] s = sentenceOf(id);
        if (s == null) return;
        spokenTo = s[1];
        readFrom = s[1];
        finishIfDone();
    }

    private void onSentenceError(String id) {
        if (sentenceOf(id) == null) return;
        pause();
        speechNote = "The phone's voice stopped reading. Resume tries this sentence again.";
        refresh();
    }

    private void showSentence(int start, int end, boolean follow) {
        CharSequence text = body.getText();
        if (!(text instanceof Spannable) || end > text.length()) return;
        Spannable spans = (Spannable) text;
        if (highlight != null) spans.removeSpan(highlight);
        highlight = new BackgroundColorSpan(highlightColor);
        spans.setSpan(highlight, start, end, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE);
        highlightStart = start;
        highlightEnd = end;
        // After layout, since the text may have just been set and not measured yet.
        if (follow) body.post(() -> keepInView(start, end));
    }

    /** Scrolls only once the sentence is off screen, so a glance back up is not pulled away mid-line. */
    private void keepInView(int start, int end) {
        Layout layout = body.getLayout();
        if (layout == null || end > body.length()) return;
        int offset = body.getTop() + body.getPaddingTop();
        int top = offset + layout.getLineTop(layout.getLineForOffset(start));
        int bottom = offset + layout.getLineBottom(layout.getLineForOffset(Math.max(start, end - 1)));
        int shown = scroll.getScrollY(), height = scroll.getHeight();
        if (top >= shown && bottom <= shown + height) return;
        scroll.smoothScrollTo(0, Math.max(0, top - height / 4));
    }

    private void clearHighlight() {
        CharSequence text = body.getText();
        if (highlight != null && text instanceof Spannable) ((Spannable) text).removeSpan(highlight);
        highlight = null;
        highlightStart = -1;
        highlightEnd = -1;
    }

    /**
     * The volume keys scroll, so the phone can be read one-handed; most of a screen, to keep a line of overlap.
     * While reading aloud they set the voice's volume instead.
     */
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (reading || (keyCode != KeyEvent.KEYCODE_VOLUME_DOWN && keyCode != KeyEvent.KEYCODE_VOLUME_UP)) {
            return super.onKeyDown(keyCode, event);
        }
        int step = (int) (scroll.getHeight() * 0.85f);
        scroll.smoothScrollBy(0, keyCode == KeyEvent.KEYCODE_VOLUME_DOWN ? step : -step);
        return true;
    }

    /** Swallowed with its key-down, or the system would still change the volume and show its slider. */
    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        if (reading) return super.onKeyUp(keyCode, event);
        return keyCode == KeyEvent.KEYCODE_VOLUME_DOWN || keyCode == KeyEvent.KEYCODE_VOLUME_UP
                || super.onKeyUp(keyCode, event);
    }

    /** Reading aloud has no background service, so leaving the reader pauses at the sentence being read. */
    @Override
    protected void onStop() {
        if (reading) pause();
        super.onStop();
    }

    @Override
    protected void onSaveInstanceState(Bundle out) {
        super.onSaveInstanceState(out);
        out.putInt(KEY_READ_FROM, readFrom);
    }

    @Override
    protected void onDestroy() {
        if (tts != null) {
            tts.stop();
            tts.shutdown();
            tts = null;
        }
        if (isFinishing()) NovelReadSession.abandon();
        super.onDestroy();
    }
}
