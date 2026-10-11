# Baihe Subtitler: current state of segmentation, line splitting, flagging and the translate pipeline (code audit with offline measurements)

Scope: repository at `/home/user/U00`, `origin/baihe-subtitler` at `a4308e32`. Evidence tags: **[run]** = verified by running code (scripts and outputs in `/tmp/claude-0/-home-user-U00/e1f5a28a-5aba-5e80-80a7-d134605804c8/scratchpad/research-seg/`, `exp1_out.txt` to `exp5_out.txt`); **[read]** = read in the code; **[inferred]** = my reasoning from the code and the runs. Sources are given as `file:line` instead of URLs. Nothing in the repo was edited; the focused tests still pass (`pytest -q -p no:cacheprovider tests/test_resegment.py tests/test_resplit_lines.py tests/test_resegment_llm_preview.py tests/test_translate_engines.py::TestFlagUncertainLines tests/test_en_cleanup.py`: 164 passed) **[run]**.

## Q1. Which features exist, what triggers them, and which cost LLM calls?

### Takeaway
Only translation, plus the automatic rule-based reading-speed flag after it, runs in a normal Translate run. All the segmentation and flag-review features run only when the user asks. **Split long lines** (all presets), **Merge short lines** and **Fix common errors** (clean-en) make no LLM calls at all. The paid features are Re-segment's **Use AI**, the **Flag** review pass, **Fix flagged**, **Reflect**, consistency, notes, emotion and glossary extraction. Re-segment AI is the cheapest of these by orders of magnitude. Fix flagged and Reflect are the expensive ones.

### Cited Findings
- Transcription always cuts long ASR segments with rules: `long_line_split.split_long_segments(...)` runs on every transcription, using 8 s / 40 CJK chars by default or the sentence rules when "Split lines by sentences" is on — `services/transcribe_pipeline.py:580-588`, `segment_splitting.py:17-18`. The Qwen path uses the same splitter — `asr_backend.py:476-478` **[read]**.
- **Re-segment** (Review > Structure) has three parts. A rules-only read-only preview (`preview_resegmentation`, "no LLM call", `services/restructure_service.py:362-380`). An optional LLM preview job (`start_llm_resegment_preview`, `:560-591`). An apply job (`start_resegmentation`, `use_llm=False` by default, `:455-500`). The UI toggle is "Use AI", off by default; its help text reads "Asks an AI engine where to split long lines the rules can't" — `frontend/src/pages/workspace/stages/review/StructureSection.tsx:204-205` **[read]**.
- **Split long lines** (`resplit_long_lines`, presets normal / more / sentence, `dry_run`, optional Qwen3 forced-aligner timing) is entirely rule-based. There is no engine parameter — `services/restructure_service.py:717-775, 952-1000` **[read]**.
- **Merge short lines** is `core.merge_adjacent_short_lines` with defaults min 1.2 s, gap 0.5 s, 80 chars. It is rule-based, with preview and apply steps — `services/review_extras_service.py:85-161`, `core.py:749-798` **[read]**.
- **Fix common errors** (clean-en) is "deterministic, no-AI tidy-up of English subtitle text" (11 regex rules) — `en_cleanup.py:1-33` **[read]**.
- LLM review jobs (on request): consistency, emotion, notes, **flag**, **fix-flagged** — `services/review_jobs_service.py:50-56`. The flag pass is `flag_uncertain_lines` (`engine_backends/llm_tasks.py:764-823`); fix-flagged is `run_fix_flagged_lines_job` (`services/workspace_job_service.py:517-633`) **[read]**.
- After every translation run, `finish_translation_run` always runs `flag_dense_lines` (reading speed) and saves flags — `bulk_translate.py:1608-1617`, called from `services/workspace_job_service.py:197` and `cli_translate.py:349` **[read]**.
- "Check timing" uses VAD only, with no LLM. It runs by itself after a Qwen-only transcription and on demand otherwise — `services/timing_check_service.py:1-17` **[read]**.
- Workflow tiers: Draft = DeepSeek with no reflect; Standard = Sonnet 5.5 with no reflect; **Release = Opus 5.5 with reflect=True** and auto_qc recommended — `engine_backends/engine_registry.py:22-30` **[read]**.
- Glossary proposals: one call that samples up to 400 lines; the results are proposals for human review — `translation_guide.py:296-330`, `services/glossary_extract_service.py:511` **[read]**.

### Inferences
- The owner's sense that the segmentation features "cost money but don't change much" is mostly misplaced on cost. Of the segmentation tools, only Re-segment's Use AI makes paid calls, and its cost is cents per hour (Q3). The real money goes to Reflect, Fix flagged and the Flag pass.

### Gaps
- I did not trace every frontend entry point, for example whether any "Make subtitles" wizard chains the Review jobs. `services/workflow_service.py` only counts flags (`:82`) and starts no LLM review jobs **[read]**.

## Q2. What do the rule-based splitters and AI re-segmentation actually do on CJK lines (measured)?

### Takeaway
There are three splitters with three different thresholds. They interact so that, after a fresh transcription, **"Split long lines → Normal" is a no-op by construction**. "More" cuts CJK words in half, and "Sentence" rarely fires on unpunctuated ASR text. Re-segment's rules do well on Chinese, because jieba is installed and drives the connective cuts. For Japanese and Korean they fall back to even character cuts. Re-segment's even-split fallback means the rules preview always splits every over-limit line, so **Use AI never changes how many lines get split, only where the residue lines are cut**. Whisper's default output is mostly short lines (mean 1.4–1.7 s), so on Whisper titles there is little for any splitter to do.

### Cited Findings
- **Thresholds:**
  - Transcription and Split-long-lines "normal" both use 8 s / 40 CJK chars (`segment_splitting.py:17-18`). `_Resplit.rules` returns `None` (that is, "today's fixed 8 s / 40") for normal with no cap (`services/restructure_service.py:742-749`).
  - Re-segment uses `2 * LINE_CHAR_LIMITS` = 32 for zh/ja and 40 for ko. It counts **characters only, never duration** (`resegment.py:68-72`, `subtitle_formats.py:31`).
  - Split long lines "more" uses the per-line CPL (16 zh/ja, 20 ko) plus 8 s (`restructure_service.py:750-752`).
  - "sentence" cuts at every sentence end, with no length limit (`:753`) **[read]**.
- **Run on 13 hand-written audio-drama lines** (6 zh, 4 ja, 3 ko; durations at 4.5 zh / 6 ja / 6 ko chars/s; `exp1_out.txt`) **[run]**:
  - **At transcription:** 5 of 6 over-32-char zh/ko lines (34–41 chars, 6–8 s) were left whole, because they sit under 40 CJK and 8 s. zh1 was packed greedily into 34 + 11 chars, which leaves a 34-char piece that is still over Re-segment's 32. ja2 (41 CJK, no punctuation) was **even-cut mid-word: 「…でもなか | なか言い出せ…」** and flagged `timing_uncertain`.
  - **Split long lines Normal:** output identical to the transcription split on all 13 lines.
  - **Split long lines More:** cut inside jieba words on zh3, zh4 and zh6 (「喜|欢」「公|司」「一|直」「再|也」). On ja it started pieces with particles (「で新幹線…」「に彼は…」, 「でしたでも…」). It turned a 27-char, 6 s zh line into two even pieces. It produced 2–5 pieces per line, many flagged `timing_uncertain`.
  - **Split long lines Sentence:** changed only the lines that had internal 。？. zh1 (45 chars, 10 s, one final 。) and every unpunctuated line stayed whole.
  - **Re-segment rules:** clean cuts on all 5 long zh lines: clause comma, Whisper space, connective 只是 / 所以, and the quote. ja1/ja3 were cut at 。 or a space. **ja2 and ko3 had no usable boundary and were even-cut** (ja2 mid-word again).
- Why ja/ko behave differently: connective cuts need word boundaries from `segment.segment_words` (`resegment.py:79-94, 122-134`). In this environment sudachipy and kiwipiepy are **not installed**; jieba is **[run]**. Korean spaces are deliberately not clause breaks (`resegment.py:114-118`). The connective lists are short: 20 zh, 7+5 ja, 7 ko (`resegment.py:52-60`) **[read]**.
- **Use AI vs rules** (`exp5_out.txt`, scripted model that answers a sensible break) **[run]**:
  - **0 LLM calls on all zh lines** and on ja1/ja3/ko1/ko2, because the rules had already split them below the cap.
  - The LLM was asked only about ja2, ja4 and ko3. It fixed ja2 (「…好きでした | でもなかなか…」) and gave ja4 a better clause cut.
  - A model answer without a `[br]` cost **3 calls** on ko3 (`LLM_MAX_ATTEMPTS = 3`, `resegment.py:65, 255-267`) and then fell back to the even cut.
- `resegment_lines` defaults to `even_split=True`. Any span still over the cap after rules and the LLM is cut evenly and flagged (`resegment.py:327-331, 375-379`). As a result, the rules preview's "N lines … could not be cut at a boundary. Try Use AI." message (`long_line_split.py:75-76`) and the UI's "Turn on Use AI to split lines the rules can't" (`StructureSection.tsx:271`) can only appear when no line is over the cap **[read + inferred from run]**.
- **Timing:**
  - Rule pieces with no stored word timings get times proportional to character count (`resegment.py:292-302`, `segment_splitting.py:233-241`).
  - Only even-split pieces are flagged `timing_uncertain` (`resegment.py:395-396`, `long_line_split.py:55-56`). Proportional cuts at punctuation are just as estimated but are **not** flagged **[read + run]**.
  - With stored Whisper words, cuts fall on real word times and pauses ≥ 0.35 s (`segment_splitting.py:37, 402-447`; `resegment.py:283-291`) **[read]**.
- **Destructive side effects:**
  - A split line loses its translation, flag, notes and emotion tag; apply needs `confirm=true` if any are present (`restructure_service.py:344-353`; `resegment.py:337-341`). Split long lines clears translations of split lines (`:718-720, 529-533`).
  - Both take a history snapshot first (`_commit`, "before re-segment" / "before re-split") **[read]**.
- **Real line-length data from this repo:** Whisper turbo default on a 15-min Chinese clip gave **384 lines, mean 1.7 s, max 10.4 s**. On the first 5 min it gave 150 lines, mean 1.4 s, max 4.6 s. Qwen3-ASR VAD gave 61 lines in 5 min, mean 3.8 s, max 7.8 s; `qwen3_asr_long` reached max 16.0 s; fast (batched) mode had mean 5.7 s, max 23.9 s — `docs/asr-experiments.md:349-373` **[read]**.
- **Merge short lines** (`exp4_out.txt`) **[run]**:
  - On 24 unlabelled-speaker lines it merged 4 pairs. One was a two-speaker exchange: 「你骗人」+「我没有骗你」 → "You're lying. I'm not lying to you." With no diarization, `same_speaker` is true for any two unlabelled lines (`core.py:773`).
  - It joins source text with no separator (`core.py:784`), so Korean 「안녕하세요」+「반갑습니다」 → 「안녕하세요반갑습니다」 (the word space is lost).
- The OSS gap report says it "did not verify whether `resegment.py` has" a text-equality check (`docs/research/oss-gap-report.md:225`). That is out of date: `match_llm_breaks` exists (`resegment.py:209-252`) **[read]**.

### Inferences
- Lines from Whisper's default segmentation are well under 32 CJK chars (about 6–8 chars at 1.4–1.7 s). So Re-segment and Split-long-lines Normal will report little or nothing on most Whisper titles. Their real targets are Qwen long windows, fast mode and lines merged by hand or by Merge short.
- On Whisper titles the dominant segmentation problem is *fragmentation* (lines that are too short), not over-long lines. Merge short is the only tool aimed at that, and it is crude: it ignores speakers when they are unlabelled and joins Korean text without a space.
- The "More" preset should not be offered as-is for zh/ja. It even-cuts words at 16 chars, a length a two-line cue already holds (export wrapping reflows that).

### Gaps
- No real-title line-length histogram (share of lines over 32 / 40 chars, or over 8 s) was available offline. A real test needs 2–3 real titles per language run through each ASR backend, then `preview_resegmentation` and `resplit_long_lines(dry_run=True)` counts.
- Japanese and Korean results without sudachipy/kiwipiepy are worst-case. A user with them installed gets connective cuts too (not measured).

## Q3. How many LLM calls and tokens per hour of subtitles does each feature use?

### Takeaway
The estimates below are built from the real prompt builders, with tokens taken as 1 per CJK char plus 1 per 4 other chars (±~30%) and Sonnet 5.5 prices. Per hour of Whisper-segmented audio (≈1,536 lines):
- Translate: ~$0.35 (52 calls).
- Reflect: ~$1.04 (156 uncached calls).
- Flag pass: ~$0.11.
- Consistency: ~$0.12.
- Re-segment AI: about $0.004–0.05 (it touches only a handful of lines).
- **Fix flagged: one call per flagged line.** At 20% flagged it costs about **2× the original translation** (~$0.74). At 50% flagged it costs ~$1.84.

### Cited Findings
All measured in `exp2_out.txt` **[run]**, with batch size 30 (default `translate_run_service.py:102`: context 10 back / 6 ahead, batch 30) and a 20-term glossary plus the audio_drama style guide.

**Per-call prompt sizes:**

| Call | Tokens (approx.) |
|---|---|
| Translate system prompt | ~1,109 |
| Translate user message | ~682 |
| Translate output | ~320 |
| Fix flagged, per line (system + 1 line) | ~1,123 |
| Flag pass, 30 lines | ~732 in, ~66 out |
| Consistency, 60 lines | ~1,205 |
| Reflect pass 1 | ~1,803 |
| Reflect pass 2 | ~2,106 |
| Reflect pass 3 | ~2,313 |
| Re-segment AI, one 37-char line | ~170 in, ~41 out |

**Per hour (Sonnet 5.5 at $2/$10 per M tokens, `engine_backends/pricing.py:101`, no cache credit):**

| Feature | At 1,536 lines/hour | At 732 lines/hour (Qwen VAD) |
|---|---|---|
| Translate | 52 calls, ~93k in / 17k out, $0.35 | $0.17 |
| Reflect | 156 calls, ~324k in / 39k out, $1.04 | $0.50 |
| Flag | 52 calls, $0.11 | $0.05 |
| Consistency | 26 calls, $0.12 | $0.06 |
| Fix flagged, 5% / 20% / 50% flagged | 76 / 307 / 768 calls, $0.18 / $0.74 / $1.84 | — |
| Re-segment AI, 5–20 residue lines × 1–3 attempts | $0.004–$0.045 | — |

On DeepSeek Flash every row is about 7× cheaper; for example Translate is $0.048 per hour.

- **Reflect is uncached.** It sends the full instructions inside each of 3 user prompts through `call_llm_json` (`engine_backends/translate_pipeline.py:170-182, 30-99`). `_call_llm_json` sends a single user message with no system block and no `cache_control` (`engine_backends/llm_tasks.py:360-369`). Normal translate does put instructions in a cached system block (`engine_backends/prompts.py:421-435`) **[read]**.
- **Fix flagged** calls `engine.translate_batch([ln.zh], …)` once per flagged line (`services/workspace_job_service.py:584-617`). It has no `recent_context` / `upcoming_lines` (the context is built once without them, `:548-551`). Each flagged line is also re-heard with Whisper on its audio slice (`services/fixflag_transcribe.py:36-59`) **[read]**.
- The translate system prompt came out at ~1,109 tokens (estimate), close to the cacheable-prefix minimum the code comment mentions ("A prefix under the model's minimum cacheable length simply isn't cached", `engine_backends/prompts.py:422-424`). Whether real batches hit the cache depends on the real token count **[run + inferred]**.
- **Cost tracking:** every paid call above logs usage under its own label: "translate", "resegment" (`restructure_service.py:394-398`), "flag_review" (`workspace_job_service.py:457-459`), "fix_flagged_line" (`:611-613`), "consistency_check" (`:487-489`).
- **Caps:**
  - The pre-run estimate exists only for translate/reflect (`engine_backends/pricing.py:214-232`, ~3.5 chars/token heuristic).
  - The LLM re-segment preview refuses when the monthly cap is spent (`restructure_service.py:571`), but `start_resegmentation(use_llm=True)` does not check it (`:455-500`).
  - Live (non-bulk) flag / consistency / emotion / notes jobs check the monthly cap **only in bulk mode** (`services/review_jobs_service.py:139-140`).
  - Fix flagged supports per-job and monthly caps (`:210-221`, enforced at `workspace_job_service.py:619-621`) **[read]**.

### Inferences
- If the goal is cutting spend, the priorities are: (1) Fix flagged's per-line calls, plus the fact that it re-translates lines whose flag reason a re-translation cannot fix (Q5); (2) Reflect's uncached 3× prompt; (3) running the flag pass on lines that already carry automatic flags. Segmentation AI is not a material cost.

### Gaps
- No tokenizer was available offline (tiktoken and Anthropic token counting need network or aren't installed), so the counts are heuristic. A real test should read `usage` from one live batch per engine, which `db.log_usage` already records.
- Real flag rates per title, which drive Fix flagged's cost, are unknown.

## Q4. How is LLM output validated, and what happens when the model misbehaves?

### Takeaway
Re-segment AI is safe by construction: the model's characters are never used, only its break positions. Translation's id-keyed matching catches dropped, reordered, extra and array replies. It does **not** catch a model that **merges** two lines or **shifts** translations by one id; both pass silently. The live Flag pass **crashes the whole job** when the model echoes the prompt's `[n]` id format.

### Cited Findings
- **Re-segment:**
  - `match_llm_breaks` maps the model's `[br]` positions back onto the original. It requires overall similarity of at least 0.9, every part similar at 0.9 or more, cuts in order, and cuts on word boundaries when a segmenter is available (`resegment.py:209-252`). There are 3 attempts, then the even split.
  - Results **[run, exp3_out.txt]**:
    - good break → accepted.
    - break inside 喜|欢 with jieba → rejected; the same break with no segmenter (the ja/ko case here) → **accepted, mid-word**.
    - dropped 1 or 3 characters, or added a comma plus a character → accepted, and the **pieces are the original text** (the model's edit never lands).
    - reordered halves, 2-character rewrite, English output, no marker, 1-character stub → rejected.
- **Translation:**
  - `request_translations_with_retry` / `_id_keyed_batch_request` parse `{id: text}`, retry the missing ids once, and leave a still-missing id blank (`engine_backends/shared.py:493-636`). The pipeline then refuses a wrong-length batch and records empty lines as errors (`engine_backends/translate_pipeline.py:636-655`).
  - Results **[run, exp3_out.txt]**:
    - correct / reordered keys / fenced JSON / extra id 6 → all right in 1 call.
    - dropped id → retried, right.
    - dropped twice → 1 blank line.
    - plain array → treated as malformed, retried, right.
    - **merge (id 2 = lines 2+3, id 3 missing)** → id 3 retried alone, giving **line 2 a duplicated translation** ("I missed you. Where did you go?"), undetected.
    - **shift by one (ids 2..4 hold the next line's text, 5 missing)** → **3 of 5 lines wrong**, undetected.
- **Flag pass** (`engine_backends/llm_tasks.py:764-823`):
  - The prompt numbers lines `[idx] zh -> en` (`:742-761`), but the parser does `int(f["line_idx"])` outside any try (`:811`). A reply of `"line_idx": "[2]"` **raises ValueError**, which ends `run_flag_job` before any flag is written (`services/workspace_job_service.py:453-470`) **[run]**.
  - `"2"` and `2.5` are accepted (2.5 → line 2). An unknown reason becomes `uncertain_translation` (`:813-814`).
  - The simplify triage records this crash as queued but not fixed: "B2 already fixes the line_idx crash (#1096 18)" (`findings-simplify.md:86`). `git log --grep line_idx` shows no fix commit **[run]**.
- Failed flag batches are only logged as a warning (`llm_tasks.py:817-822`). The job result reports `flagged_count` only, not how many batches failed (`workspace_job_service.py:476`), unlike consistency, which reports `failed_batches` (`:493-497`) **[read]**.
- The flag and consistency prompts say "Below are **Chinese** source lines …" whatever the title's language (`llm_tasks.py:575, 753`). Verified on a Japanese line **[run]**.
- Batches that come back blocked (`content_blocked`) are bisected once and then flagged (`translate_pipeline.py:571-614`). Reflect's critique pass has no retry by design (`:196-210`) **[read]**.

### Inferences
- A cheap guard against merge and shift (length ratio of en to source per id, or an embedding or back-check) would close the main silent-error hole. Re-segment needs no more validation, except that ja/ko without a segmenter accept mid-word cuts.

### Gaps
- How often real models merge or shift ids inside a JSON object is unknown offline. A real test would replay ~50 batches per engine with lines planted to tempt a merge (two short consecutive fragments of one sentence) and count misalignments against a reference.

## Q5. Flags: where are they produced, how precise are they, how many, and can users act on them?

### Takeaway
A line holds **one** flag slot shared by 15 reasons: 4 from the LLM and 11 from the system. Most flags come from rules, not the model. The **reading-speed rule fires on half of plausible good English lines at the default "Normal" setting, and on every line long enough to be eligible**. It re-runs after every translation run. The LLM flag pass **overwrites** system flags. **Fix flagged** treats every flag the same: it re-hears, re-translates and **clears the flag even when the reason (timing, reading speed, a block) was not addressed**. Flag counts are therefore likely **too high**, and the count is not broken down by reason. Precision is pinned by no test or fixture.

### Cited Findings
- **Reasons:**
  - LLM-pickable: `uncertain_translation`, `ambiguous_reference`, `name_uncertain`, `slang_idiom` (`engine_backends/llm_tasks.py:698-703`).
  - System: `timing_uncertain`, `language_uncertain`, `timing_overlap`, `timing_drift`, `reading_speed`, `factual_detail`, `gap_untranscribed`, `bulk_source_changed`, `pronoun_check`, `content_blocked` (`:706-723`).
  - A single `flag`/`flag_note` column (`core.py:44`; `db.py:1165`) **[read]**.
- **Producers:**

| Flag | Where set | When |
|---|---|---|
| `timing_uncertain` | forced aligner fallback/repair (`forced_align.py:363-422`); even-split at transcription, re-segment and re-split (`long_line_split.py:55`, `resegment.py:395`) | automatic |
| `language_uncertain` | `mixed_language.py:106` | automatic, at transcription |
| `reading_speed` | after every translate run (`bulk_translate.py:1615`, and bulk apply `:566, 910`) | automatic |
| `pronoun_check` | `translate_pipeline.py:356-362` | when the she/her default is on, zh only |
| `content_blocked` | `translate_pipeline.py:609-613` | automatic |
| `timing_drift` | Check timing (`timing_check_service.py:180-188`) | automatic after Qwen-only transcription, otherwise on demand |
| `timing_overlap` | `export_service.py:389-409` | on request |
| `factual_detail` | Auto QC (`auto_qc.py:500-525`) | on request |
| `gap_untranscribed` | `transcribe_gap_service.py:222` | on request |
| LLM flag pass | `llm_tasks.py:764` | on request |

  **[read]**
- **No ASR-confidence flags exist.** There is no use of `avg_logprob`, `no_speech_prob` or word probability anywhere outside tests (grep, only a comment at `core.py:398`) **[run]**.
- **Reading-speed thresholds:**
  - Normal: latin 8.5 / cjk 6.5 cps. Relaxed: 12 / 9 ("a judgement call, not measured", `subtitle_formats.py:142-147`). Streamer: relaxed × 1.5 (`:153`).
  - Floors: cues under 1.5 s or under 15 Latin chars are never flagged (`:160-163`).
  - CPS counts every character, including spaces and punctuation (`reading_speed`, `:181-182`) **[read]**.
- **Measured on 24 natural audio-drama lines** (`exp4_out.txt`; zh at 4.5 chars/s; English written as normal subtitles, not padded) **[run]**:
  - **Normal 12/24 flagged** (= 12 of 12 eligible lines; cps 10.2–19.3).
  - **Relaxed 11/24**; Streamer 2/24.
  - At 6 zh chars/s: Normal 10/24, Relaxed 10/24, Streamer 7/24.
  - Median English cps of the whole set: 14.1 (at 4.5/s) and 18.3 (at 6/s). Auto QC on the same lines: 0 flagged.
- The translate prompt gives no numeric length or CPS target, only "Keep each translation concise enough to read comfortably as a subtitle" (`engine_backends/prompts.py:132`). It flags afterwards at 8.5 cps **[read]**.
- **Overwrites and persistence:**
  - The LLM pass overwrites any existing flag, system flags included. A pinned test asserts re-flagging ("already_flagged_line_can_be_reflagged", `tests/test_translate_engines.py:2253-2258`).
  - The run confirmed `timing_uncertain` and `content_blocked` replaced by LLM reasons, with the notes wiped (`exp3_out.txt`) **[run]**.
  - The rule-based flaggers never replace a flag (`subtitle_formats.py:233`, `export_service.py:398`, `auto_qc.py:518`) **[read]**.
- **Dismissal:** "Dismiss flag" clears the line; only `timing_drift` dismissals are remembered (`services/lines_service.py:220-236`). `finish_translation_run` re-runs `flag_dense_lines` over all of the run's lines and saves them (`bulk_translate.py:1615-1618`), so a dismissed reading-speed flag comes back after the next whole-drama translate run **[read + inferred]**.
- **Fix flagged:**
  - It selects `flagged = [ln for ln in lines if ln.flag]`, any reason (`workspace_job_service.py:541`). The API has no reason filter (`api/schemas/review.py:454-459`).
  - For each line: re-hear zh from audio (replaces zh if non-empty), re-translate with no neighbouring context, and **set `flag=None` whenever the new English is non-empty** (`:584-617`). That holds even if the English is unchanged, even for `timing_*` or `reading_speed` flags (timing is never touched), and for `content_blocked` (it sends the same text to the same engine).
  - The docstring claims it clears flags only on lines "that actually changed something" (`:521-526`); the code does not check for change **[read]**.
- **What the UI offers:**
  - One total `flagged_count`, a "Flagged" filter, previous/next flagged navigation (`frontend/src/pages/workspace/stages/review/LinesPanel.tsx:440-450`), per-line dismiss (`LineRow.tsx:375`).
  - "Clear reading-speed flags" (optionally re-check) as the only per-reason bulk action (`ReviewFlags.tsx:81-152`; `export_service.py:452-476`).
  - Fix flagged (all reasons). No per-reason counts or filter were found **[read]**.
- **Tests:** the flag pass is pinned only on mechanics (batching, unknown reason, failure, overwrite, cancel; `tests/test_translate_engines.py:2207-2258, 3065-3089`). No fixture measures flag precision or recall, and there is no golden set **[read]**.

### Inferences
- In a typical drama the reading-speed rule alone probably flags a large share of eligible lines (all 12 of 12 eligible lines in my set). Combined with `timing_uncertain` from even splits and alignment fallbacks, that buries the few LLM "uncertain translation" flags that the review queue was built for.
- Fix flagged then spends one paid call, plus one Whisper decode, per buried line to "fix" them by clearing the flag. This is the likeliest concrete reason the owner sees paid passes that "don't change much".
- Measured English reading-rate norms for comparison (for example the commonly cited ~17 cps adult limit) are left to the other researcher. My data only shows that 8.5 and 12 cps sit below natural translated dialogue.

### Gaps
- Precision of the LLM flag pass is not measurable offline. A real test needs ~200 lines with a human label for "needs a second look", run through each engine's flag pass and scored for precision and recall.
- The real per-reason flag mix on users' titles is unknown. A `SELECT flag, COUNT(*) FROM lines GROUP BY flag` on a real library would answer it.

## Q6. Translation pipeline: batching, scene breaks, context, style/locale, reflect, caps

### Takeaway
A normal run uses batches of up to 30, ending at scene breaks (pauses ≥ 3 s in the last 25% of a batch, on by default), with 10 translated lines before and 6 raw lines after as context, a cached system prompt, and id-keyed replies. Reflect is three uncached passes. The CLI and the app share `finish_translation_run` and the style context builder.

### Cited Findings
- **Batching:**
  - `plan_batches` (`engine_backends/translate_pipeline.py:264-293`) is used when `scene_aware_batches` is on, which is the default (`services/settings_service.py:444`).
  - Defaults are context 10 / ahead 6 / batch 30 (`services/translate_run_service.py:102`; the function defaults are 20 / 6 / 3, `translate_pipeline.py:365-374`).
  - Already-translated lines are skipped unless `force_retranslate` is set, and English-looking source lines are copied without a call (`:483-491`) **[read]**.
- **Context:** recent pairs and upcoming lines go in the user message, not the cached prefix (`prompts.py:229-283, 438-445`). Speaker names come from characters; raw diarization labels are hidden (`translate_pipeline.py:409-416`) **[read]**.
- **Style and locale:**
  - The locale sets spelling (`prompts.py:92-97`).
  - The style guide plus a policy-grouped glossary replace the flat glossary (`prompts.py:102-113`; `translation_guide.py:208-260`).
  - The style context is shared by the app and the CLI (`services/workspace_job_service.py:55-83`).
  - Fix flagged uses Settings' default locale and style note (`review_jobs_service.py:233-236`) **[read]**.
- **Pronoun re-check:** zh only, when the she/her default is on. It re-asks only the suspect lines, then flags `pronoun_check` (`translate_pipeline.py:306-362`) **[read]**.
- **Caps:**
  - Per-run cap checked after each batch; it can overshoot by one batch (`translate_pipeline.py:678-682`; docstring `:441-447`).
  - The fallback engine has its own cap (`:674-677`).
  - The month cap refuses a start when spent (`engine_backends/pricing.py:235-253`) **[read]**.

### Inferences
- The pipeline is careful about integrity: ids, caps and field-scoped writes. Its weak spots are quality signals:
  - no CPL/CPS target in the prompt while a strict CPS flag is applied afterwards;
  - no merge/shift detection;
  - flag and consistency prompts that assume Chinese.

### Gaps
- Whether scene-aware batching changes quality could not be measured offline. It needs A/B runs on real titles with a reference translation.

## Q7. What could NOT be measured offline, and what a real-model test needs

### Takeaway
Everything above that concerns mechanics, thresholds, prompt sizes and validation was measured or read. Model *quality* was not, and neither were real flag mixes or real line-length distributions.

### Cited Findings
- No network, keys, GPU or real models were used. The repo's `tests/fake_engine.py` has `client = None`, so free-form LLM features "decline cleanly" (`tests/fake_engine.py:21`); scripted Claude-shaped fakes were used instead (`exp3`, `exp5`) **[read + run]**.

### Inferences
A real-model test plan:
1. **Segmentation:** 3 real titles per language (Whisper default, Qwen VAD, Qwen long). Record the counts from `preview_resegmentation` and `resplit_long_lines(dry_run=True)` for each preset. Have a native speaker judge mid-word and particle cuts on the AI preview versus the rules preview, and log the LLM calls and attempts per line.
2. **Flags:** a 200-line human-labelled set per language. Measure flag-pass precision and recall per engine. Check the reason mix on real libraries.
3. **Fix flagged:** on titles with mixed reasons, measure the share of lines whose English actually changed versus the flags cleared, and the spend per real fix.
4. **Translation integrity:** batches seeded to tempt a merge or shift; count misaligned ids per 1,000 lines.
5. **Reading speed:** compare the 8.5 / 12 cps thresholds with professional subtitles for the same audio (other researcher's norms).

### Gaps
- Real Japanese and Korean behaviour with sudachipy and kiwipiepy installed.
- Real token counts and cache-hit rates per engine.
