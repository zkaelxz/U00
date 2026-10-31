# Timing, grouping and splitting thresholds: inventory for a "show and adjust" feature

Base checked: `origin/baihe-subtitler` @ `723e6f28`. Read-only survey: no repository file was changed. Every "CLI" mention below (`cli.py`, `cli_translate.py`, `cli_timing.py`, "CLI same") describes the headless command line as it was then; it was removed in #1345, so ignore those clauses.

Legend used in the tables
- **Used by**: T = transcribe, R = review, Tr = translate, E = export, L = live, D = dub, H = hard-sub OCR. "CLI same" means the CLI reaches the same code through the same service.
- **Today**: where it can already be changed. "Hardcoded" means only by editing code.
- **Risk**: *Looks* = changes only how future lines are cut, grouped or flagged; existing data is untouched until the user applies a run or tool. *Data* = can rewrite saved times or text, or invalidates stored state.
- **Expose**: E = everyone (Settings / stage form), D = Developer Mode only, F = keep fixed (safety limit).
- Ranges marked "(proposed)" are my judgment. Ranges marked "(existing)" are already enforced in code.

---

## 1. Summary

- I found about **75 numeric thresholds** that decide how speech is detected, cut, merged, grouped, flagged, padded or time-limited. About **20 are already adjustable**. Most of those are **per title** (columns on the `dramas` row, edited in the Transcribe stage's Advanced fold) or **per run/per request** (Review merge and re-split forms, speech coverage, Live session form, Dub pacing, Export wrap width, translate batch and context). **No timing threshold is a global setting in `lib/settings_schema.py` today.** The only related global is `scene_aware_batches`, an on/off switch whose 3.0 s gap is hardcoded.
- The hardcoded thresholds that matter most to users are:
  - line-splitting limits (8 s / 40 CJK characters; minimum piece 0.8 s / 4 characters / 2 words; the 12 s quick-split cap in the frontend)
  - Qwen3 speech-detection merge gaps and windows (1.0 / 0.3 / 3.0 / 30 s)
  - translate-by-sentence grouping (1.0 s / 4 lines / 60 characters / 12 s) and the scene-break gap (3.0 s)
  - review-check thresholds (coverage 12 s / 3 s / 4.5 cps; pacing 160 wpm, ×1.15, 1.2 s, 0.4; the "Check timing" tolerances of 0.7 s / 40 % / 0.1 s; reading speed 6.5 / 8.5 / 7.5 cps with 1.5 s and 11–15 character floors)
  - per-language characters-per-line limits (zh/ja 16, ko 20, en 42)
- Two review profiles already exist (`review_thresholds.py`: drama "default" vs "streamer"), plus three reading-speed modes (normal/relaxed/off, plus a hidden "streamer" mode). Any new adjustability should **extend these presets** rather than sit beside them. Otherwise there will be two competing ways to loosen the same check.
- **No `db/` edit is needed.**
  - Global values go in `app_settings` through the existing generic `db.get_app_setting` / `db.set_app_setting` with new rows in `lib/settings_schema.py`.
  - A per-title override can also live in `app_settings` under a title-scoped key (the pattern `language_pack_service` already uses: `language_packs.title.<id>`). A JSON sidecar in the title folder (the pattern `timing_check_service` uses: `timing_check.json`) would also work.
  - The existing per-title transcribe knobs already have `dramas` columns.
- Root modules (`segment_splitting`, `sentence_groups`, `subtitle_formats`, `timing_drift`, `vad_segments`, `asr_backend`) must **not** read settings. Services read them and pass them as arguments; the module constants stay as the defaults. Because the CLI calls the same services (`cmd_transcribe` → `transcribe_service.start_transcribe_run`, `cmd_translate` → the translate pipeline, `cli_timing` → `timing_check_service`), it picks the values up automatically.
  - One exception: `cli.py cmd_align` (line 441) calls `core.transcribe_for_timing` and `word_align.realign_oversized_segments` directly with literal defaults. It keeps the old values unless it is changed too, which is acceptable because the CLI is maintenance-only.

---

## 2. Tables by UI group

### Group A: Voice detection (finding where speech is)

| Name | file:line | Default | What it controls (plain) | Used by | Today | Risk | Safe range | Draft tooltip | Expose |
|---|---|---|---|---|---|---|---|---|---|
| Min silence (Whisper VAD) | `services/transcribe_service.py:77` (`_DEFAULT_TUNING`); bounds `core.py:301-302` | 300 ms | How long a silence must last before Whisper treats it as the end of a piece of speech. | T (Whisper path); retranscribe line. CLI same (`cmd_transcribe`, `cmd_align` reads the title value) | **Per title**: Transcribe › Advanced "Min silence" (`TranscribeStage.tsx:719`); auto-tune candidates `core.py:307` = [300, 800, 1500] | Looks | 100–3000 (existing; frontend parity test `tests/test_frontend_limit_parity.py`) | "The shortest silence that ends a piece of speech. Raise it for fewer, longer lines; lower it to split at shorter pauses, which can cut mid-sentence." | E (already) |
| Sentence-mode min silence | `asr_backend.py:87`; used `services/transcribe_pipeline.py:492` | 2000 ms | When "Split lines by sentences" is on, Whisper uses this longer silence instead of the title's value, and lines are cut afterwards by sentence. | T | Hardcoded | Looks | 1000–3000 (proposed; inside the 100–3000 slider bounds) | "With 'Split lines by sentences' on, Whisper only breaks at pauses this long and the app then cuts at sentence ends. Lower gives Whisper shorter pieces, which can split sentences." | D |
| VAD threshold (Whisper) | `transcribe_service.py:78`; `sensitivity_preset.py:16` | 0.5 (probability) | How sure the detector must be that a sound is speech. | T, retranscribe. CLI same | **Per title**, Dev Mode only (`TranscribeStage.tsx:729`), 0.1–0.9 (`transcribe_service.py:379`) | Looks | 0.1–0.9 (existing) | "How confident the detector must be that a sound is a voice. Higher ignores quiet or breathy speech; lower catches it but may write text over music or noise." | D (already) |
| Sensitive preset threshold | `sensitivity_preset.py:17` | 0.35 | The threshold used when the title's Sensitivity is "sensitive" and the VAD threshold was never changed. | T | Preset choice per title; the number is hardcoded | Looks | 0.2–0.45 (proposed; must stay below 0.5 to differ from Normal) | "The detector threshold the 'Sensitive' preset uses. Lower catches quieter speech but invents more text over music." | D |
| Whisper speech pad | not set; faster-whisper's own `VadOptions.speech_pad_ms` (**uncertain**: 400 ms in current faster-whisper releases) | ~400 ms (library) | Extra audio kept around each detected speech piece on the Whisper path. | T | Not passed at all (`core.py:548` passes only min silence and threshold) | Looks | 0–800 (proposed) | "Extra audio kept before and after each piece of speech so first and last syllables aren't clipped. Raise if words are cut off; lower if lines start early." | D (new pass-through) |
| Qwen VAD min speech | `vad_segments.py:63` (`speech_spans` default) | 250 ms | Detected speech shorter than this is ignored (Qwen3 speech-detection backend, language detection, ASMR detector). | T (Qwen3 VAD, mixed language, ASMR) | Hardcoded | Looks | 0–500 (proposed; the long-window backend already uses 0) | "Speech blips shorter than this are ignored. Lower keeps short interjections ('嗯', 'え'); higher drops clicks and breaths." | D |
| Qwen VAD pad | `vad_segments.py:63` | 100 ms per side | Audio added on each side of a detected speech piece so word edges survive. | T (Qwen3 VAD), mixed language | Hardcoded | Looks | 0–500 (proposed) | "Audio kept on each side of detected speech. Raise if word endings are clipped; lower if neighbouring lines run together." | D |
| Qwen VAD threshold / min silence | `vad_segments.py:62` (0.5 / 300 ms); the comment at `asr_backend.py:390` says the title's values are deliberately *not* used | 0.5 / 300 ms | Silero settings for the Qwen3 speech-detection backend. | T | Hardcoded | Looks | 0.2–0.8 / 100–3000 (proposed) | "Speech detector settings for the Qwen3 'detect speech first' engine. Separate from the Whisper settings because the two engines need different tuning." | D |
| Long-window VAD settings | `asr_backend.py:75` `LONG_VAD_KWARGS` | threshold 0.35, min speech 0, pad 300 ms | Speech detection for the "Qwen3 long window" backend, tuned on FLEURS (docs/asr-experiments.md). | T | Hardcoded | Looks | as above | (as above, "long-window engine") | D |
| Merge gap (Qwen VAD) | `asr_backend.py:65` | 1.0 s | Two speech pieces closer than this are sent to Qwen3 as one. | T | Hardcoded | Looks | 0.2–3.0 (proposed) | "Pauses shorter than this stay inside one transcription window. Raise for fewer cuts mid-sentence; lower to keep quick speaker changes apart." | D |
| Merge gap, mixed languages | `asr_backend.py:66` | 0.3 s | The same, when each span's language is detected. Kept short so a quick change of language isn't merged. | T | Hardcoded | Looks | 0.1–1.0 (proposed) | "Pause that keeps two pieces apart when the language is detected per piece. Lower separates language switches better." | D |
| Long-window merge gap | `asr_backend.py:76` | 3.0 s | Pauses up to this length stay inside one long window; longer ones are treated as music or silence. | T | Hardcoded | Looks | 1.0–6.0 (proposed) | "Pauses up to this length stay inside one window. Raise to keep more context; lower if text gets invented over music." | D |
| Long window length | `asr_backend.py:77` | 30 s | The longest audio window the long-window backend sends at once. Tied to aligner drift past ~30 s (`forced_align.py:59-62` comment). | T | Hardcoded | Looks (quality) | 15–30 (proposed; above 30 the aligner drifts) | "Longest window sent to the model at once. Longer gives more context; above 30 s the timing drifts." | D |
| Long-window cut search | `asr_backend.py:78` | 8 s | How far back from the window limit the app looks for the quietest place to cut. | T | Hardcoded | Looks | 2–10, and less than the window (proposed) | "How far back the app may look for a quiet spot to cut a long window. Wider finds better cuts but gives shorter windows." | D |
| Span cap (normal Qwen VAD) | `vad_segments.py:113-114` (`cap_spans` max 15 s, search 3 s) | 15 s / 3 s | Speech longer than this is cut at its quietest point for the Qwen3 VAD backend and language detection. | T | Hardcoded | Looks | 8–30 / 1–5 (proposed) | "Longest speech piece sent to the model; longer pieces are cut at the quietest point near this length." | D |
| Context pad | `asr_backend.py:64`; `vad_segments.py:135` | 2.0 s | Silence around a span the model also hears (not shown in line times); helps number formatting. | T | Hardcoded | Looks | 0–3 (proposed) | "Extra surrounding audio the model hears but the line doesn't show. Helps accuracy; 0 turns it off." | D |
| ASMR detector on/off thresholds | `asmr_vad.py:32-33` | 0.5 / 0.35 | When the ASMR whisper-speech detector switches a span on and off. | T (voice detector = asmr) | Global choice `voice_detector` (Settings › System › Transcription experiments); numbers hardcoded | Looks | start 0.3–0.8, stop < start (proposed) | "When the whisper-voice detector starts and stops a speech piece. Lower catches softer whispering." | D |
| Speech-coverage VAD threshold | `services/speech_coverage_service.py:33` | 0.3 | The deliberately low threshold used by "Find missed speech" and "Check timing". | R | Hardcoded | Looks (flags only) | 0.2–0.5 (proposed) | "Detector threshold for the missed-speech and timing checks. Lower finds more possible missed speech, including music." | D |

### Group B: Line splitting and tidying (transcription and Review tools)

| Name | file:line | Default | What it controls | Used by | Today | Risk | Safe range | Draft tooltip | Expose |
|---|---|---|---|---|---|---|---|---|---|
| Max line length (time) | `segment_splitting.py:17` `SPLIT_MAX_SECONDS` | 8.0 s | A transcribed line longer than this is cut into subtitle-sized pieces. Also the default for the "Normal"/"More" re-split. | T (all ASR paths via `long_line_split`), R re-split. CLI same (`cmd_transcribe`) | Hardcoded. The Review re-split takes a per-request cap of 2–120 s (`restructure_service.py:983`) | Looks | 4–20 (proposed; above ~12 s Review flags lines as "long" anyway) | "Lines that run longer than this are cut at sentence ends, commas or pauses. Raise for fewer, longer lines; lower for shorter lines that are easier to read." | E |
| Max line length (CJK chars) | `segment_splitting.py:18` `SPLIT_MAX_CJK_CHARS` | 40 chars | A line with more CJK characters than this is cut; the even-split last resort also uses it (`long_line_split.py:27`). | T, R. CLI same | Hardcoded | Looks | 20–80 (proposed; 2× `LINE_CHAR_LIMITS` zh = 32 is the export two-line cue) | "Lines with more Chinese, Japanese or Korean characters than this are split. Lower gives shorter lines; very low values make choppy subtitles." | E |
| Pause that can split a long line | `segment_splitting.py:37-39` | 0.35 s (0.1–2.0) | A gap between words at least this long is a place to cut a line with no punctuation. | T, R re-split. CLI same (`--min-pause`) | **Per title**: Transcribe › Advanced (`TranscribeStage.tsx:720`) | Looks | 0.1–2.0 (existing; parity test) | (existing help text) | E (already) |
| Min piece length | `segment_splitting.py:30-32` | 0.8 s / 4 CJK chars / 2 words | When re-split rules apply, pieces smaller than this are folded into a neighbour so nothing flashes by. | T (sentence mode, long-window), R re-split | Hardcoded | Looks | 0.3–2.0 s / 2–8 chars / 1–4 words (proposed) | "Pieces shorter than this are joined to a neighbour instead of shown on their own. Raise to avoid flashes; lower to allow very short lines." | D |
| Similar-gap ratio | `segment_splitting.py:42` | 0.75 | Pauses within 75 % of the longest count as equally good, so the cut nearest the middle wins. | T, R | Hardcoded | Looks | 0.5–1.0 | (internal) | F (algorithm detail) |
| Even-split fallback | `long_line_split.py:23-55` (uses the two limits above) | — | A piece still over the character limit is cut into equal parts with guessed times and flagged `timing_uncertain`. | T, R | Follows the limits above | Looks | — | — | (inherits) |
| Quick split cap (frontend) | `frontend/src/pages/workspace/stages/review/ResplitLines.tsx:26` | 12 s | The one-click "Split long lines" button uses this duration cap. | R | Hardcoded in the frontend | Looks | 2–120 (server bounds) | "Duration cap used by the one-click split. Lines longer than this are cut." | E (read the global setting) |
| "Too long" re-segment threshold | `resegment.py:68-72` (2 × `LINE_CHAR_LIMITS`) | zh/ja 32, ko 40, en 84 | A line over twice the per-language subtitle line is "too long" for re-segmenting. | R | Derived from export limits | Looks | follows export limits | — | (inherits) |
| Long-line reason hints | `long_line_split.py:60-61` | 12 s / 40 chars | Only words the explanation when a re-segment preview finds nothing. | R | Hardcoded | Looks (message) | — | — | F (should follow the coverage thresholds) |
| Merge short lines | `services/review_extras_service.py:85-86`; `core.py:759`, ×2.5 at `core.py:793` | min 1.2 s, gap 0.5 s, 80 chars; merged result < 2.5 × min | Joins short, close lines from the same speaker. | R (AI extras merge preview/apply). CLI: not used | **Per request** in Review › Merge (bounds 0.1–10, 0–5, 10–500; mirrored in `aiExtrasLogic.ts:14-17`) | Looks (applied with a snapshot) | existing | (existing form) | E (already; could take its default from a global) |
| Word realign trigger | `word_align.py:262` | 12.0 s | Whisper segments at least this long are re-split by word alignment when "Realign long segments" is on. | T. CLI `cmd_align` calls it with the default | Toggle per title; number hardcoded | Looks | 6–30 (proposed) | "With 'Realign long segments' on, lines at least this long are re-cut by word timing. Lower re-cuts more lines but takes longer." | D |
| Word realign pause | `word_align.py:160,263` | 0.6 s | A gap between aligned words this long starts a new line during realignment. | T | Hardcoded | Looks | 0.2–2.0 (proposed) | "During realignment, a pause this long between words starts a new line." | D |
| Stock-phrase isolation | `core.py:377` | 5.0 s | "Thanks for watching"-type text is dropped only with this much silence on both sides. | T (all) | Hardcoded | **Data** (drops text from the run) | 2–15 (proposed) | "Credit lines Whisper invents over silence are removed only when this isolated. Lower removes more but may drop real dialogue." | D |
| Repeat-loop collapse | `core.py:399`; `transcribe_for_timing` default `core.py:470` | 4 repeats | Four or more identical consecutive segments collapse to one. | T | Hardcoded | **Data** (drops text) | 3–8 (proposed) | "Identical lines repeated this many times in a row are treated as a model loop and kept once." | D |
| Hallucination guard | `core.py:321`; `transcribe_service.py:386-389` | 0 (off); 0 or 0.5–10 s | Whisper drops a segment containing this much internal silence. | T (Whisper) | **Per title**, Dev Mode (`TranscribeStage.tsx:730`) | **Data** (drops lines) | existing | (existing) | D (already) |
| Transcript-align fallback times | `core.py:850,854,856` | 0.5 s min, 0.05 s nudge, 1.0 s fill | Minimum length and spacing for lines whose time had to be guessed when aligning a supplied transcript. | T (have-transcript), CLI `cmd_align` | Hardcoded | Looks | — | — | F (repair constants) |
| Forced-align chunk / hard cap | `forced_align.py:63-64` | 60 s / 300 s | Lines are aligned in ~60 s chunks; a line over 300 s can't be aligned. Also used by retime (`services/retime_service.py:89,98`). | T, R retime | Hardcoded | Looks | chunk 20–60; cap fixed (documented model limit) | — | chunk D, cap F |
| Qwen oversize segment skip | `asr_backend.py:59` | 300 s | A Whisper segment longer than this isn't re-transcribed by Qwen3 (Whisper text is kept). | T | Hardcoded | Looks | — | — | F |
| Retime window pad / min line | `services/retime_service.py:27,30` | 3.0 s / 0.1 s | Re-time looks this far around a line for its speech; a re-timed line is never shorter than 0.1 s. | R | Hardcoded | **Data** (writes start/end on apply) | pad 1–5 (proposed) | "How far around a line Re-time searches for its speech. Raise if lines are badly late." | D |
| Gap fill (add missing lines) | `services/transcribe_gap_service.py:27-47` | min gap 2.0 s; piece 1–30 s; add 0.5–600 s; pause 0.2 s | Which silent stretches can be transcribed into new lines, and how they are cut. | R | Hardcoded (the 600 s cap is a safety limit) | **Data** (adds lines) | min gap 0.5–10; piece max 10–30 (proposed) | "Silent stretches at least this long are offered for 'transcribe this gap'." | min gap E, rest F |

### Group C: Review checks (what gets flagged)

| Name | file:line | Default | What it controls | Used by | Today | Risk | Safe range | Draft tooltip | Expose |
|---|---|---|---|---|---|---|---|---|---|
| Long line (coverage) | `review_thresholds.py:38-39`, `core.py:955` | 12 s (streamer 20 s) | A line longer than this, and more than 2.5× the time its text needs, is listed as "maybe several merged lines". | R (Review lists, autotune scoring `services/autotune_service.py:42`) | Profile chosen by content mode; numbers hardcoded | Looks (list only) | 6–60 (proposed) | "Lines longer than this, with little text for their length, are listed as possibly missing dialogue. Raise to list fewer." | E |
| Large gap (coverage) | `review_thresholds.py:39`, `core.py:956` | 3.0 s (streamer 15 s) | A silence between lines longer than this is listed as a possible missed line. | R | as above | Looks | 1–60 (proposed) | "Silences longer than this between lines are listed as possible missed speech." | E |
| Speaking-rate baseline | `core.py:956`; ×2.5 at `core.py:994` | 4.5 chars/s | Expected Mandarin speaking speed, used only to spot extreme long-line outliers. | R | Not in the profile (always 4.5) | Looks | 2–8 (proposed) | "How fast speech normally is, in characters per second. Used to decide that a long line has too little text." | D |
| Pacing: words per minute | `review_thresholds.py:40`, `engine_backends/llm_tasks.py:505` | 160 wpm (streamer 200) | English speed used to judge whether a translation fits its time slot. | R, Tr (auto-shorten `services/line_tools_service.py:34`), D prep | Profile only | Looks (the shorten action edits English after confirm) | 120–260 (proposed) | "Assumed English speaking speed. Raise if your speakers talk fast so fewer lines are flagged too long." | E |
| Pacing: too-long margin | `review_thresholds.py:40` | ×1.15 (streamer ×1.5) | How far a line may overrun its slot before it's flagged "too long". | R, Tr | Profile only | Looks | 1.0–2.0 | "How much a translation may overrun its time slot before it's flagged." | D |
| Pacing: short line | `review_thresholds.py:40` | min 1.2 s, ratio 0.4 (streamer 6.0 s, 0.2) | A slot longer than 1.2 s that the English fills less than 40 % of is flagged as a long pause. | R | Profile only | Looks | 0.5–10 s / 0.1–0.8 | "Lines this long that the English fills only a small part of are flagged as feeling empty." | D |
| Timing check: early/late edge | `timing_drift.py:20-21` | 0.7 s each | A line may start this much before its speech (or end after it) before "Check timing" flags it. | R (Check timing; auto after Qwen-only transcription). CLI same (`cli_timing.py`) | Hardcoded; ×1.5 for streamer (`timing_drift.py:28`) | Looks (flag) | 0.3–2.0 (proposed) | "How far a line may start before or end after its speech before it's flagged. Raise for fewer flags." | E |
| Timing check: speech share | `timing_drift.py:23` | 40 % | Lines that are less than 40 % speech are flagged as mostly silence. | R | Hardcoded | Looks | 0.1–0.7 | "Lines with less speech than this share of their time are flagged as mostly silence." | D |
| Timing check: overlap tolerance | `timing_drift.py:25` | 0.1 s | Consecutive lines may overlap this much before it's flagged. | R | Hardcoded | Looks | 0–0.5 | — | D |
| Timing check: broken-audio share | `timing_drift.py:31` | 2 % | Below this share of speech, the whole check is skipped as "broken audio". | R | Hardcoded | Looks | — | — | F |
| Timing snap limits | `timing_drift.py:33-38` | max shift 3.0 s, pad 0.1 s, min shift 0.05 s, min length 0.3 s | How far the one-click "snap to speech" may move a line's edges. | R | Hardcoded; **`SNAP_MAX_SHIFT_S` also validates the stored sidecar** (`services/timing_check_service.py:85`) | **Data** (writes start/end); lowering it discards saved suggestions | pad 0–0.3; max shift F | "Space kept around speech when a line is snapped to it." | pad D, shift F |
| Reading speed limits | `subtitle_formats.py:137-138` (normal), `:148-149` (relaxed), `:154-155` (streamer = relaxed × 1.5) | CJK 6.5 / RTL+Indic+Thai 7.0 / Latin 8.5 / other 7.5 cps; relaxed 9 / 10 / 12 / 10.5 | Characters per second above which a cue is flagged "hard to read". | R/E (`export_service.flag_dense_lines`, `:323`) | **Per title** mode normal/relaxed/off (`export_service.py:437`); numbers hardcoded | Looks (flag) | CJK 4–12, Latin 12–25 (proposed; Netflix-style English ~17, CJK ~9) | "Lines faster than this many characters per second are flagged hard to read. Raise for fast talkers." | E (custom cps per script) |
| Reading speed floors | `subtitle_formats.py:162-164` | 1.5 s; 11 / 12 / 15 / 13 chars | Very short cues, or cues with little text, are never flagged. | R/E | Hardcoded | Looks | 0.5–3 s; 5–25 chars | "Cues shorter than this are never flagged for reading speed." | D |
| Coverage warning | `asr_backend.py:90-91` | 15 % of audio, files ≥ 30 s | After transcription, warns when text covers under 15 % of the audio. | T | Hardcoded | Looks (message) | 5–40 % | "Warn after transcription when lines cover less than this share of the audio." | D |
| Missed-speech scan | `speech_coverage_service.py:34-46` | min gap 2.0 s (0.5–30 per request); line tolerance 0.3 s; min piece 0.25 s; merge 1.0 s; raw overlap 0.2 s; max 500 gaps | What "Find missed speech" lists. | R | Min gap per request (`api/speechCoverage.ts:9`); rest hardcoded | Looks | existing / tolerances F | (existing) | gap E (already); rest F |
| Mixed-language disagreement | `mixed_language.py:41` | 50 % | Share of letters outside the expected scripts above which a line is flagged "language uncertain". | T | Hardcoded | Looks (flag) | 0.3–0.8 | — | D |

### Group D: Sentence grouping and batching for translation

| Name | file:line | Default | What it controls | Used by | Today | Risk | Safe range | Draft tooltip | Expose |
|---|---|---|---|---|---|---|---|---|---|
| Scene-break gap | `engine_backends/translate_pipeline.py:269` `SCENE_BREAK_GAP_SECONDS` | 3.0 s | A pause this long counts as a scene change, so a translation batch ends there instead of mid-scene. | Tr (app and CLI `cli_translate.py:295`; bulk passes the switch) | **Global** on/off `scene_aware_batches` (Settings › Translation style); number hardcoded | Looks (batch boundaries, cost-neutral) | 1.5–15 (proposed); must stay above the sentence gap | "Pauses at least this long count as scene changes, and translation batches end there. Raise for fewer, larger scenes." | E |
| Batch cut window | `translate_pipeline.py:272` `tail_fraction` | 0.25 | A batch may end early only in its last quarter. | Tr | Hardcoded | Looks | 0.1–0.5 | — | F |
| Sentence fragment gap | `sentence_groups.py:12` | 1.0 s | With "Translate by sentence" on, lines closer than this (same speaker, no sentence end) are translated as one sentence. Must stay below the scene gap (comment `:9-11`). | Tr (per-title toggle; CLI `--translate-by-sentence`) | Toggle per title; number hardcoded | Looks (translation wording) | 0.3–2.5 (proposed), and less than the scene gap | "Lines closer than this that don't end a sentence are translated together as one sentence." | E |
| Max lines per sentence | `sentence_groups.py:13` | 4 | Most fragments joined into one sentence. | Tr | Hardcoded | Looks | 2–8 | "Largest number of short lines joined into one sentence for translation." | D |
| Max chars per sentence | `sentence_groups.py:14` | 60 | Longest joined sentence (source characters). | Tr | Hardcoded | Looks | 30–150 | — | D |
| Max seconds per sentence | `sentence_groups.py:15` | 12 s | Longest joined sentence in time. | Tr | Hardcoded | Looks | 5–30 | — | D |
| Batch size / context | `services/translate_run_service.py:102` | 30 lines, context 10 back / 6 ahead (engine defaults `translate_pipeline.py:369-374`: 20, 6, 3) | Count-based, not time-based. | Tr | **Per run** (Translate form) | Looks / cost | existing | — | (already) |

### Group E: Subtitle shape and export

| Name | file:line | Default | What it controls | Used by | Today | Risk | Safe range | Draft tooltip | Expose |
|---|---|---|---|---|---|---|---|---|---|
| Characters per subtitle line | `subtitle_formats.py:31-32` | zh 16, ja 16, ko 20, en 42, other 42 | The per-language line length. Feeds the "More" re-split (`restructure_service.py:751`), the "too long" re-segment threshold (×2) and wrapping. | R, E | Export wrap width is **per request** (`export_routes.py:48-49`, 1–200); the table itself is hardcoded | Looks | CJK 10–24, Latin 30–50 (proposed) | "Most characters on one subtitle row for this language. Lower wraps sooner and makes re-split cut shorter lines." | E |
| Wrap stub floor | `subtitle_formats.py:52` | limit // 3 | Avoids a 1–2 character first row when wrapping. | E | Hardcoded | Looks | — | — | F |
| Overlap clamp | `subtitle_formats.py:91-104` | no threshold (any overlap) | Exports trim a cue that runs into the next. | E | Not a threshold | Looks | — | — | F |
| Min/max cue duration, min gap | — | **none exist** | The exporter has no minimum or maximum cue length and no minimum gap between cues. | E | — | — | — | — | Open question |

### Group F: Live, dubbing, hard-sub OCR

| Name | file:line | Default | What it controls | Used by | Today | Risk | Safe range | Draft tooltip | Expose |
|---|---|---|---|---|---|---|---|---|---|
| Live chunk length | `services/live_service.py:279`, range `:56` | 20 s (3–60) | Audio length transcribed at a time; longer is more accurate but lags more. | L | **Per session** (Live page) | Looks | existing | (existing) | E (already) |
| Live overlap | `live_translate.py:427`, range `live_service.py:57` | 3 s (0–8, ≤ chunk/2) | Audio re-heard at each chunk start so words cut at the boundary aren't lost. | L | **Per session** | Looks | existing | (existing) | E (already) |
| Live backlog / lag window | `live_translate.py:432`; `live_pipeline.py:22-31` | 2 chunks; translate backlog 2; lag median over 5; min final chunk 0.5 s; idle 0.25 s | Queue limits that keep captions near the live edge. | L | Hardcoded | Looks | — | — | F (stability) |
| LocalAgreement line rules | `live_agreement.py:16-29` (module removed 2026-10-31, unused) | window 2–15 s, force commit 6 s, edge guard 1 s, line close 0.7 s, line max 7 s / 40 chars | Streaming line commit rules. Was never imported by a production module; removed. | (none) | Hardcoded | — | — | — | Out of scope until wired |
| Dub pacing | `dub.py:324-325`; bounds `services/dub_service.py:264` | speed-up ≤ 1.4×, slow-down ≥ 0.85× | How much a dubbed clip may be stretched to fit its line. | D. CLI same (`--max-speedup/--max-slowdown`) | **Per run** (Dub form `dubForm.ts`) | Looks (audio) | 1.0–2.0 / 0.5–1.0 (existing) | (existing) | E (already) |
| Hard-sub frame interval | `hardsub_ocr.py:51`; `transcribe_service.py:319,409` | 1.0 s (0.5–3.0) | How often a video frame is read for burned-in captions. | T (hard-sub mode) | **Per title**, Dev Mode (`TranscribeStage.tsx:731`) | Looks | existing | (existing) | D (already) |
| Hard-sub stability | `hardsub_ocr.py:296` | 2 consecutive frames | A caption must appear on at least 2 sampled frames to become a cue. | T | Hardcoded | Looks | 1–4 | "A caption must appear on this many frames in a row to count. Lower catches brief captions but adds OCR noise." | D |

### Kept fixed (safety and integrity limits; listed so the builder doesn't expose them)

| Name | file:line | Value | Why fixed |
|---|---|---|---|
| `MIN_SILENCE_MS_MIN/MAX`, `MIN_WORD_GAP_SECONDS_MIN/MAX` | `core.py:301-302`; `segment_splitting.py:38-39` | 100–3000 ms; 0.1–2.0 s | These are the bounds of user settings, mirrored in the frontend and pinned by a parity test. |
| `_MIN_PIECE_S` | `vad_segments.py:17` | 1.0 s | Stops tiny model inputs; cap logic depends on it. |
| `_FRAME_S`, sample rates, ASMR `WINDOW_S=30` / `FRAMES_PER_WINDOW=1500` | `vad_segments.py:14-15`; `asmr_vad.py:24-28` | — | Model input format; changing them breaks the model. |
| `MAX_STORED_WORDS`, `MAX_STORED_WORD_BYTES`, `_MAX_WORD_MS` | `segment_splitting.py:450-453` | 1000 / 32 000 / 100 h | Size of stored rows; validation of stored data. |
| `HARD_CAP_SECONDS`, `SEGMENT_DURATION_WARNING_SECONDS` | `forced_align.py:64`; `asr_backend.py:59` | 300 s | Model limit / VAD-merge guard. |
| `SNAP_MAX_SHIFT_S`, `BROKEN_AUDIO_SPEECH_FRACTION` | `timing_drift.py:31-33` | 3.0 s / 2 % | Validate the stored suggestions sidecar; whole-check guard. |
| `MAX_LINE_TEXT_CHARS`, `RESPLIT_MAX_CHARS`, `MAX_MERGE_LINES` | `services/restructure_service.py:68-73`; `services/lines_service.py:36` | 2000 / 20 000 / 50 | Request size and denial-of-service caps. |
| Gap-fill `MAX_ADD_SECONDS`, `MAX_GAPS` | `transcribe_gap_service.py:35`; `speech_coverage_service.py:46` | 600 s / 500 | One request can't create hundreds of lines. |
| All `*_TIMEOUT*`, `_CHUNK_SECONDS` decode chunk (600 s), live fetch limits | various | — | Process and network safety. |
| Progress fractions `RUNNING_MAX`, `QWEN_SPLIT`, `REALIGN_START` | `services/transcribe_pipeline.py:51-55` | 0.99 / 0.85 / 0.9 | Progress bar only; not timing. |
| `audio_preprocess` chunk 30 s / overlap 1 s | `audio_preprocess.py:66-67` | — | Vocal separation chunking. Not subtitle timing (**uncertain** whether worth exposing; I'd say no). |
| `stereo_cues.py` constants | `stereo_cues.py:16-33` (module removed 2026-10-31, unused) | — | Audio rendering DSP, not line timing. |

`auto_qc.py` has **no** timing thresholds (numbers, names and banned terms only). `qa.py` and `line_tools.py` have none either.

---

## 3. Settings mechanism notes

**Global setting, end to end (no `db/` edit):**
1. Add a row to `SETTINGS` in `lib/settings_schema.py`.
   - Use `_pref(name, "float"|"int", default, min=, max=, unit=, label=, help=, tab="…", section="…")`. `_pref` stores under `pref.<name>`.
   - A `pref.*` key is automatically writable through `services/settings_service.set_settings` (it accepts `_is_preference` keys, `settings_service.py:258-283`), returned by `get_preferences()` in the overview, and validated by `settings_schema.validate` / `coerce`. A bad stored value reads back as the default.
   - For Developer-Mode-only rows: `scope="developer", dev_only=True`. `tests/test_settings_schema.py:222` requires that `dev_only == (scope == "developer")`.
   - `SCOPES` already contains `"title"` (`settings_schema.py:17`), but no row uses it and nothing reads it. It is free to adopt for metadata, but adopting it doesn't add any storage.
2. Read the value with `settings_service.get("<name>")` in the **service** that calls the root module, then pass it as an argument. `test_get_callers_name_db_rows` requires literal keys that are declared rows.
   - Root modules may not import services. A root module that reads `app_settings` directly must be added to `ROOT_READERS` with a reason and must call `settings_schema.coerce`. Avoid that; pass arguments instead.
3. `GET /api/settings/schema` (`services/settings_schema_service.py`, model `api/schemas/settings.py` `SettingsSchemaRow`) sends `label`, `help`, `unit`, `min`, `max`, `section` and `dev_only`. It already hides dev-only rows unless Developer Mode is on.
4. Frontend: `frontend/src/pages/settings/SchemaCard.tsx` renders every non-custom row of a `section` with `Field`. `Field` shows `help` through `HelpTip` (the (i) tooltip, `components/HelpTip.tsx`). So the schema `help` string **is** the tooltip, and declaring the row is enough for it to appear once a `SchemaCard` for that section is mounted (`PreferencesSections.tsx` mounts sections by name; a new section such as "Timing and splitting" needs one `<SchemaCard section=…>` line).
5. Writes go through `POST /api/settings`, which is **local_only**, so only the PC owner can change globals. That fits "safety-relevant" knobs.

**Gotchas for the builder (from `tests/test_settings_schema.py`):**
- `test_reader_defaults_round_trips_and_survives_bad_rows` (`:283-293`) writes `2.5` for every **float** row and expects it to read back (`_valid_and_bad`, `:268-270`). Any float row whose range excludes 2.5 (a 0–1 threshold, or 0.1–2.0 s) will **fail** that test. The helper needs to pick an in-range, non-default value, for example the midpoint of min and max. That is a test-helper fix, not a loosening, and should be stated as such in the PR.
- `PROBES` (`:225`) are run through `validate`/`coerce` for every `pref.*` row, so ints must have `min`/`max`.
- `SchemaCard.toDraftValue` shows a stored `0` as blank, and a blank saves the default (`SchemaCard.tsx:66-75`). A threshold where 0 is meaningful (for example pad 0 ms, overlap tolerance 0) cannot be set to 0 from the generic card. Use `min > 0`, or a custom field.
- `set_settings` does not refuse a write to a `dev_only` row when Developer Mode is off (it only checks `_is_preference`). Hiding is UI-only. **Uncertain** whether that matters: writes are already local_only.

**Per-title overrides without touching `db/`:**
- What exists today:
  - The Transcribe stage's per-title knobs are **`dramas` columns**, set by `transcribe_service.update_transcribe_config` (`services/transcribe_service.py:342-420`) and read by `get_transcribe_config` with fallback to `_DEFAULT_TUNING` (`:74-86`). They are listed in `services/run_settings_service.py ALLOWED_KEYS` and saved in the raw-transcript run record (`raw_transcript.build_run_settings`).
  - Reading-speed mode is also a column (`export_service.set_reading_speed_mode`, `:437-447`).
  - Adding *new* columns would need `db/` (`init_db` ALTER plus `_INIT_DB_MIGRATED_COLUMNS`), which is off limits while `db/` is being split.
- Option 1 (recommended): one JSON document per title in `app_settings` under `timing_overrides.title.<drama_id>`, written and read with the existing `db.get_app_setting` / `db.set_app_setting`, exactly like `services/language_pack_service.py:18,73,95`.
  - Needs one `DYNAMIC_KEYS` entry in `tests/test_settings_schema.py:33` with its reason.
  - Values are validated against the same schema rows (min/max) and merged over the global value: title override → global setting → module constant.
  - Caveats:
    - (a) Title-scoped `app_settings` keys are **not removed when a title is deleted** (language packs aren't either; I found no cleanup). The builder should add a delete hook in `delete_service`, or accept the orphan.
    - (b) They don't travel with a per-title backup or export. **Uncertain**: I did not trace the backup code.
- Option 2: a sidecar `timing_overrides.json` in `db.drama_dir(id)`, the pattern of `services/timing_check_service.py:39,54`. It is deleted with the title folder. Whether backups include it is **uncertain**. It needs its own lock and validation like `timing_check_service._read_state`.
- Either way: the drama ownership check (`ownership_service`), a new route with `require_permission(...)`, and a regenerated `docs/route-permissions.md` row (`python tools/route_table.py --write`).
- For job workers (transcription runs in a spawned process): resolve the effective values in the parent (`start_transcribe_run`) and pass them as picklable args. Add the keys to `run_settings_service.ALLOWED_KEYS` so the Jobs page shows what a run used.

**Which items suit a per-title override better than a global:**
- Already per title: min silence, VAD threshold, sensitivity preset, split pause, hallucination guard, hard-sub interval, reading-speed mode, translate-by-sentence toggle.
- Should be per title (with a global default): max line seconds and CJK characters (speaker pace, language), review-check profile numbers (coverage long/gap, pacing wpm, timing tolerances), reading-speed cps, scene-break gap, sentence-group gap.
- Global only: Qwen VAD / window internals, pads, filters, characters-per-line table (per language, not per title), quick-split cap.

**`db/` edits needed:** none for either option. The `db` package split doesn't affect this work as long as the builder calls `db.get_app_setting` / `db.set_app_setting` / `db.get_drama` / `db.drama_dir` through the `db` front door.

---

## 4. Proposed build plan

**Phase 0: one source of truth (small, about 0.5 day).**
- Rename nothing and add no new defaults module; CLAUDE.md rules out new abstractions unless they are asked for.
- The module constants stay as the defaults. `lib/settings_schema.py` may not import root modules, so each row repeats its default as a literal.
- Extend `test_constants_match` (`tests/test_settings_schema.py:235`) so it asserts that every row's default equals its module constant, as it already does for two keys.

**Phase 1: read-only "what's in effect" panel (about 1 day).**
- Add the schema rows (Groups B–E "E" items first) with `label`/`help`/`unit`/`min`/`max`, `tab="system"` or a new `tab`, `section="Timing and splitting"` plus one or two more sections matching the UI groups.
- Mount `SchemaCard`s in Settings. This shows the values and tooltips immediately, and they become editable globally.
- Files: `lib/settings_schema.py`, `frontend/src/pages/settings/PreferencesSections.tsx` (or `Settings.tsx`), `tests/test_settings_schema.py` (float-probe helper, constants-match test).

**Phase 2: wire the globals into services (about 2–3 days).** For each exposed row, the service reads `settings_service.get` and passes the value down:
- `transcribe_service.start_transcribe_run` → `transcribe_pipeline` → `long_line_split.split_long_segments(rules=SplitRules(max_seconds, max_chars))`. Also `restructure_service._Resplit.rules` (`:745-753`; note its docstring hardcodes "8 s / 40").
- `review_thresholds.thresholds_for(drama)`: build `ReviewThresholds` from settings for the default profile and keep the streamer multipliers. Consumers are `review_lines_service.py:221,244`, `line_tools_service.py:34` and `autotune_service.py:42`. Decide whether autotune scoring should follow the user's numbers (probably not: it is a comparison metric).
- `timing_check_service.apply_spans` → `timing_drift.find_drift(...)` (thresholds become kwargs; keep `SNAP_MAX_SHIFT_S` fixed).
- `translate_pipeline.plan_batches(min_gap=…)` and `sentence_groups.group_fragments(...)` limits. Callers: `workspace_job_service.py:146-173`, `translate_run_service.py:602`, `cli_translate.py:295-333`, `bulk_translate.py:1283`. Enforce sentence gap < scene gap in validation.
- `subtitle_formats.dense_lines` (cps tables) via `export_service.py:323,412-420,472`.
- Frontend `ResplitLines.tsx:26` reads the quick-split cap from the settings overview.
- Add keys to `run_settings_service.ALLOWED_KEYS` and `raw_transcript.build_run_settings` so runs record what they used.
- Tests: per area (`tests/test_segment.py`, `test_sentence_groups.py`, `test_core.py`, the timing-check tests, `test_translate_run_service.py`, `test_export*`). Assert that a changed setting changes the output, and that defaults reproduce today's output byte for byte.
- Static guards before pushing.

**Phase 3: per-title overrides (about 2 days).**
- `services/timing_overrides_service.py` (new file, needs a docstring). Get/set/clear, validated by the schema rows, merged over globals.
- Two routes with `require_permission`, plus the `route-permissions.md` regen.
- A "Timing and splitting" fold in the Transcribe stage (or Review), showing "global value / this title" with a reset per item.
- Delete-hook cleanup.
- Tests: ownership, validation, merge order, delete cleanup, `DYNAMIC_KEYS` entry.

**Phase 4: Developer Mode items (about 1 day).** The D rows (Qwen VAD internals, pads, filters, realign, ASMR) as `dev_only` rows shown only in Developer Mode. Most of these feed `asr_backend.WhisperVadQwenBackend.transcribe` (`asr_backend.py:380-460`), which today uses module constants. Thread one `dict` of VAD options through `transcribe_pipeline` → `asr_backend`.

**Risks**
- Silent behaviour change if a default drifts from the constant. Mitigate with the constants-match test, and with "defaults reproduce today's output" tests.
- Two ways to loosen checks (streamer profile / relaxed mode vs custom numbers). Decide the precedence: custom numbers replace the "default" profile only, and streamer stays a multiplier.
- Thresholds that interact: sentence gap < scene gap; long-window search < window; min piece < max line. These need cross-field validation in `set_settings`, which today validates each key alone, so a new hook is needed.
- Spawned workers: values must be resolved before spawn.
- Tests that pin today's numbers (for example `tests/test_frontend_limit_parity.py`, `tests/test_sentence_groups.py`, `tests/test_core.py`) must keep passing at the defaults. They are not loosened.
- `.md`/AGENTS.md are untouched unless a repeated rule changes.

**Effort:** about 6–8 focused days in total; Phase 1 alone gives visible value in about 1 day. Per CLAUDE.md, use **one branch per phase**.

---

## 5. Open questions for the owner

1. **Global vs per title:** should the first version be global only (Settings page), with per-title overrides later? Per-title needs a new route and the `app_settings`/sidecar storage choice above.
2. **Where in the UI:** a new Settings tab/section ("Timing and splitting") for globals, and a fold in the Transcribe or Review stage for per-title values. Or should every value live next to the stage that uses it?
3. **Presets vs numbers:** keep "Normal / Relaxed / Off" reading speed and the automatic streamer profile as presets, with custom numbers as an extra "Custom" choice? Or replace the presets?
4. **Who may change them:** global settings are PC-owner-only (local_only) today. Should household members be able to change per-title overrides on titles they own?
5. **Export:** the exporter has no minimum or maximum cue duration and no minimum gap between cues. Do you want those added (that would be a new feature, not exposing an existing number)?
6. **LocalAgreement live rules** (`live_agreement.py`) were never used by the live pipeline; the module was removed on 2026-10-31.
7. **Data-dropping filters** (stock-phrase isolation, repeat collapse, hallucination guard): keep them in Developer Mode only, as proposed?
8. **Autotune scoring** uses the coverage thresholds. Should it follow the user's custom numbers or stay on the fixed defaults so results are comparable?
9. **Whisper speech pad** (library default ~400 ms, not set by the app today): expose it? That changes the call to faster-whisper, so the installed version must accept it (**uncertain** which versions are installed on user PCs).
