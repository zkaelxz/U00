# Baihe code scoping: Thai (th) as a source language for videos, translated to English

Scope: checkout of branch `baihe-subtitler` at commit `723e6f28` (2026-10-11), read-only. Citations are `file:line` in the repository at /home/user/U00 unless a URL is given. "Thai" means Thai as the source of a video (or audio) title translated to English. Novels, comics and Thai as a translation target are out of scope.

## 1. Where the zh/ja/ko list lives, who imports it, and whether the database needs a change

### Takeaway
The language list lives in one place, `core.py:21-25`, and about 15 modules import it. The database stores the language as free `TEXT` with no CHECK constraint, and line filters read `core.LINE_LANGUAGES`, so **Thai needs no `db/` edit and no migration**. The catch is that `core.LANGUAGE_NAMES` also gates Qwen3-ASR and the Qwen3 forced aligner through re-exports. Adding `"th": "Thai"` there would switch on the aligner for Thai, and that aligner does not support Thai.

### Cited Findings
- `SOURCE_LANGUAGES = ("zh", "ja", "ko")`, `LANGUAGE_NAMES = {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}`, `LINE_LANGUAGES = SOURCE_LANGUAGES + ("en",)`. The comment says "Callers fall back to 'Chinese' for anything else." — `core.py:19-25`
- `normalize_line_lang` refuses any code outside `LINE_LANGUAGES` (`core.py:89-99`). `_stored_line_lang` reads an unknown stored code as None, which means the title's language (`core.py:102-108`).
- Importers of `SOURCE_LANGUAGES` (each validates against it):
  - `services/drama_service.py:38,123-125`. The error text hard-codes "must be one of zh, ja, ko".
  - `services/transcribe_service.py:60,509,657`
  - `services/live_service.py:42,298`
  - `services/source_service.py:12,78`
  - `services/discover_catalog_service.py:12,50-51`. The text hard-codes "zh, ja, ko".
  - `services/benchmark_lab_service.py:56,89,220,386,408`
  - `services/benchmark_case_service.py:12,33`
  - `services/benchmark_set_builder_service.py:22,59,120`
  - `epub_io.py:15,99`
  - `asr_benchmark.py:41,198` (argparse `choices`)
  - `word_align.py:217-223` (gate before the CJK segmenters)
  - `api/benchmark_schemas.py:12,16`: `SourceLanguage = Literal[SOURCE_LANGUAGES]`, which follows the constant automatically
- Importers of `LANGUAGE_NAMES` (prompt text, falling back to "Chinese" or "the source language"):
  - `translate_engines.py:18`
  - `line_tools.py:13,24,51,78,99`
  - `engine_backends/shared.py:10,415-418`
  - `engine_backends/prompts.py:4,116,292-293`
  - `engine_backends/standalone.py:4,38`
  - `translation_guide.py:20,325,629,730`
  - `story_context.py:14,183`
  - `resegment.py:40,184`
  - `services/benchmark_judge_service.py:32,175`
  - `services/reader_service.py:444`
  - `scanlate.py:316-320`
  - `mixed_language.py:14,45`
- The gating chain: `forced_align.py:55` imports `LANGUAGE_NAMES` from core, and `forced_align.py:66-68` builds `ALIGNER_LANGUAGE_NAMES = {**LANGUAGE_NAMES, "en": "English"}`. `asr_backend.py:48` imports `LANGUAGE_NAMES` from `forced_align` and refuses Qwen3-ASR for any language outside it (`asr_backend.py:254-258, 384-388`). `services/retime_service.py:82` gates re-timing on `forced_align.ALIGNER_LANGUAGE_NAMES`. `services/compare_transcription_service.py:76,312` gates on `asr_backend.LANGUAGE_NAMES`.
- `qwen3_native.py:42-43` keeps its own hard-coded `ALIGNER_LANGUAGE_NAMES = {"ja","ko","zh"}` and `ALIGNER_LANGUAGE_PACKAGES = {"Japanese": "nagisa", "Korean": "soynlp"}`.
- Qwen3-ForcedAligner supports 11 languages (Chinese, English, Cantonese, French, German, Italian, Japanese, Korean, Portuguese, Russian, Spanish), not Thai. Qwen3-ASR lists 30 languages including "Thai (th)". — [Qwen3-ForcedAligner-0.6B model card](https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B)
- Database:
  - `dramas.source_language TEXT DEFAULT 'zh'` has only a comment `-- 'zh', 'ja', or 'ko'` (`db/__init__.py:336`). The same type is used at `db/__init__.py:824, 870` (benchmark and other tables) and in the ADD COLUMN migration (`db/__init__.py:1200`).
  - `lines.lang TEXT` was added by ALTER (`db/__init__.py:1180-1183`).
  - The only CHECK constraints in the schema are on `gpu_lock.id` (`db/__init__.py:912, 5001`).
  - The line-language filter imports `core.LINE_LANGUAGES` at query time (`db/__init__.py:2434, 2518-2525`), so it picks up `th` automatically.
  - The source text column is named `zh` but holds the source text whatever the language (`core.py:30-31, 40`).
- Tests that pin the list:
  - `tests/test_shared_constants.py:19-22` asserts `SOURCE_LANGUAGES == ("zh","ja","ko")` and the exact `LANGUAGE_NAMES` dict.
  - `tests/test_shared_constants.py:25-37, 52-58` assert the identity of every copy (these keep passing).
  - `tests/test_shared_constants.py:40-42`: the benchmark Literal equals the tuple (keeps passing).
  - `tests/test_forced_align.py:32` asserts `fa.LANGUAGE_NAMES == {zh, ja, ko}`.
  - `tests/test_forced_align.py:38` asserts `ALIGNER_LANGUAGE_NAMES == {**LANGUAGE_NAMES, "en": ...}`.
  - `tests/test_qwen3_native.py:438` asserts `set(fa.LANGUAGE_NAMES) == {"zh","ja","ko"}`.
  - `tests/test_mixed_language.py:207-208` asserts `QWEN_LANGUAGE_NAMES == {zh, ja, ko, en}`.

### Inferences
- **No `db/` edit is needed.** Validation is all in code, and the db reads `LINE_LANGUAGES` from core. The stale comment at `db/__init__.py:336` can stay; changing it would touch `db/` during the split for no behavioural reason.
- **Decouple before adding Thai.** Adding `"th": "Thai"` to `core.LANGUAGE_NAMES` without other changes would:
  - open Qwen3-ASR for Thai, which is correct because Qwen3-ASR lists Thai;
  - also put Thai into `forced_align.ALIGNER_LANGUAGE_NAMES`, which is wrong. Thai titles would then pass the `qwen3_forced_align`, `qwen3_asr_long` (always aligns) and re-time gates, and fail inside the model.
- The safe change is to derive the aligner set from an explicit list (for example `qwen3_native.ALIGNER_LANGUAGE_NAMES` plus `en`) rather than from `core.LANGUAGE_NAMES`. Keep the ASR set as `core.LANGUAGE_NAMES`, which then includes Thai.
- Tests that break the moment `core.SOURCE_LANGUAGES` gains `"th"`:
  - `test_shared_constants.py:20-21`
  - `test_forced_align.py:32` and `test_qwen3_native.py:438`, unless `forced_align.LANGUAGE_NAMES` stops being core's (that is the decoupling above, and `test_shared_constants.py:26` pins `forced_align.LANGUAGE_NAMES is core.LANGUAGE_NAMES`, so it changes too)
  - `test_mixed_language.py:207`
  - `test_forced_align.py:38` if the aligner dict is redefined
- Frontend tests break when the frontend lists change (see section 5).

### Gaps
- I did not run the test suite (the task was read-only), so the breakage list comes from reading the assertions, not from a run.

## 2. Transcription: Whisper, filters, Qwen3, mixed-language, alignment

### Takeaway
Plain Whisper transcription of Thai works almost as-is once `th` passes the validators. faster-whisper gets `language=` passed straight through, and the "letter filter" uses `str.isalnum()`, which accepts Thai letters. The CJK assumptions that break Thai are:
- **mixed-language script checking**: no Thai entry, so every Thai span reads as "disagreeing";
- **`segment.py` falling back to jieba** for any non-ja/ko language, reached through `word_align.realign_long_segment` once `th` is in `SOURCE_LANGUAGES`;
- **the forced-aligner gates** described in section 1.

### Cited Findings
- Whisper call: `core.transcribe_for_timing(..., language="zh", ...)` passes `"language": language` straight to faster-whisper (`core.py:466, 546-554`). Groq passes `language` as well (`core.py:619, 649`).
- The letter filter keeps a segment only if `any(ch.isalnum() for ch in text)` (`core.py:564-566`).
- Hallucination guards are language-neutral except the stock-phrase list:
  - `filter_hallucinated_segments` collapses runs of identical segments (`core.py:399-454`);
  - `_STOCK_PHRASE_RES` holds only English, Chinese, Japanese and Korean outro phrases (`core.py:362-373`), and `_stock_key` keeps only `isalnum` characters (`core.py:380-381`).
- The repeat guard and hallucination-silence defaults are justified by CJK behaviour (`core.py:313-321`).
- Whisper model list: `WHISPER_MODELS` has small, medium, large-v3 and large-v3-turbo, and the default is `large-v3-turbo` (`whisper_models.py:16-22`). `default_whisper_size()` is one global default, not per language (`services/transcribe_service.py:681-686`).
- A user-chosen local CTranslate2 folder is supported through the `whisper_model_path` setting (`services/transcribe_service.py:748`, `cli.py:494`). The Japanese fine-tune tooling is a precedent (`tools/ja_whisper_models.py:38`; `docs/asr-experiments.md:160`).
- The repo's own research report says OpenAI documents turbo as degrading more on Thai: "degrades more on Cantonese and Thai" — `reports/ASR alignment translation best practices.md:23`, citing [openai/whisper README](https://github.com/openai/whisper).
- Mixed-language:
  - `_SCRIPTS` has hangul, kana, han and latin; `_EXPECTED_SCRIPTS` has ko, ja, zh and en only (`mixed_language.py:25-38`).
  - `text_matches_language` returns **False** for a language with no entry (`mixed_language.py:51-62`).
  - `transcribe_spans` then retries the span and flags it `language_uncertain` (`mixed_language.py:89-107`).
  - `QWEN_LANGUAGE_NAMES = {**LANGUAGE_NAMES, "en": "English"}` (`mixed_language.py:45-46`).
- Qwen3 backends: `Qwen3ASRBackend.transcribe` and `Qwen3ASRVadBackend` refuse a language outside `LANGUAGE_NAMES` (`asr_backend.py:254-258, 384-388`).
- `qwen3_asr_long` always runs the forced aligner (`services/transcribe_pipeline.py:390-391`; `services/qwen3_requirements_service.py:42-48`). `require_qwen3_packages(..., aligner_language)` checks only the nagisa/soynlp tokenisers (`qwen3_native.py:46-56`).
- A title stays on Whisper unless it chose a Qwen3 backend (`services/asr_options_service.py:205-212`). The choices are `("whisper", "qwen3_asr", "qwen3_asr_vad", "qwen3_asr_long")` (`services/asr_options_service.py:41`).
- `_check_run_choices` refuses `qwen3_forced_align` only in Whisper-only mode (`services/transcribe_service.py:422-436`). It has no language check beyond the package checks.
- Space-delimited aligner handling: `forced_align._WORD_UNIT_LANGUAGES = {"English"}` (`forced_align.py:70-72`).
- `word_align.realign_long_segment`:
  - returns the segment unsplit when `language not in SOURCE_LANGUAGES` (`word_align.py:216-223`);
  - otherwise calls `segment.segment_and_annotate` (`word_align.py:224-225`);
  - its docstring says joining words "with no separator is correct across zh/ja/ko" (`word_align.py:171-173`).
- `segment.segment_words` and `segment_and_annotate` handle `ko` and `ja`, and **everything else goes to jieba / `segment_zh`** (`segment.py:107-121, 179-184`). Callers: `resegment.py:84-85`, `word_align.py:224`, `reader.py:58-69`, `services/reader_service.py:486-498`.
- Content-mode gate: transcription runs only for `audio_drama` and `streamer_vod` content modes (`services/transcribe_service.py:501-505, 653-656`). Video dramas use those modes (`db/__init__.py:333, 337`).
- Live (`services/live_service.py:298`) accepts `SOURCE_LANGUAGES`, so adding `th` there opens the Live API for Thai too. The Live UI list is separate (`frontend/src/api/live.ts:24-25`).

### Inferences
- **P0 Whisper path:** once `th` passes `drama_service` and `transcribe_service` validation, `large-v3`/`turbo` run with `language="th"`. Without a `segment.py` change, `word_align` would jieba-segment Thai for MMS realignment of oversized segments (alignment method `whisper_diff` plus long-segment realign). P0 needs either a Thai branch in `segment.py` or a gate (`if language == "th": return [segment]`).
- Mixed-language mode needs `"thai": re.compile(r"[฀-๿]")` and `_EXPECTED_SCRIPTS["th"] = ("thai", "latin")`. Without these, every Thai line in a mixed-language run gets a `language_uncertain` flag.
- `isalpha()` is False for the Thai combining vowel and tone marks (category Mn), so `text_matches_language` counts only base consonants. That is harmless.
- Thai stock outro phrases (for example "ขอบคุณที่รับชม", "thanks for watching") should be added to `_STOCK_PHRASE_RES` in P0/P1. I have no measurement of how often Whisper emits them for Thai.
- The default model should probably be `large-v3`, not turbo, for Thai. That needs a per-language default or at least a UI warning. `whisperModelWarning` (`frontend/src/pages/workspace/sourceForm.ts:161-170`) is the obvious place for the warning.

### Gaps
- No Thai ASR measurements exist in the repo (`docs/asr-experiments.md` covers zh/ja/ko). I found no primary zero-shot Thai WER/CER for Whisper large-v3 in this session. The fine-tune cards I checked ([BuzzASR/thai](https://huggingface.co/BuzzASR/thai), [typhoon-whisper-large-v3](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3)) don't give the baseline in the visible text.
- I could not determine how well faster-whisper's word timestamps line up with Thai text in `segment_splitting._WordIndex.build`, which requires the words to spell the text exactly (`segment_splitting.py:290-318`). Whisper's Thai word pieces should concatenate to the text, but this is unverified.

## 3. Line building, splitting, merging, wrapping, reading speed, fonts

### Takeaway
Every length rule counts CJK characters with a regex that excludes Thai (`[぀-ヿ㐀-鿿가-힯]`). For Thai the splitters therefore:
- cut only on duration (8 s) by default;
- treat every unspaced Thai piece as a one-word "stub", which defeats the More/Sentence re-split presets;
- find no sentence punctuation;
- fall back to an even split by codepoint that can separate a combining mark from its base consonant.

Wrapping keeps a spaceless Thai line whole. Merge-short joins Thai lines with no space, which loses Thai's phrase-separating space. Reading-speed checks run on the English field only, so they don't apply to Thai. A PyThaiNLP `newmm` branch in `segment.py` is the natural place to plug in a word segmenter.

### Cited Findings
- `segment_splitting.py`:
  - `SPLIT_MAX_CJK_CHARS = 40`, `_CJK_RE = [぀-ヿ㐀-鿿가-힯]`, and `_CJK_SPACE_RE` (only spaces next to CJK count as phrase breaks) (`segment_splitting.py:16-25`);
  - `_SENTENCE_END_RE`/`_CLAUSE_END_RE` hold CJK and Latin punctuation only (`segment_splitting.py:19-20`);
  - `SplitRules.measure` returns the CJK count, else `len(text.strip())` if `count_latin`, else 0 (`segment_splitting.py:64-67`);
  - without rules, `split_long_segments` counts only CJK characters (`segment_splitting.py:171`);
  - it cuts at sentence ends, then commas, then `_CJK_SPACE_RE` (`segment_splitting.py:196-210`);
  - `_is_stub`: with no CJK characters a piece is a stub if `len(text.split()) < MIN_PIECE_WORDS (2)` (`segment_splitting.py:269-272`).
- `long_line_split.py:27-29, 97`: the default limits and count also use `_CJK_RE`.
- `services/restructure_service.py:741-752`: re-split presets. "normal" is `SplitRules(seconds, SPLIT_MAX_CJK_CHARS, count_latin=False)`; "more" uses `subtitle_formats.line_char_limit(ln.lang or self.language)`.
- `resegment.py`:
  - `_SENTENCE_MARKS`/`_CLAUSE_MARKS` are CJK and Latin only, and `_CONNECTIVES_BEFORE` has zh, ja and ko entries (`resegment.py:43-58`);
  - `max_line_chars = 2 * line_char_limit(language)` (`resegment.py:66-72`);
  - word boundaries come from `segment.segment_words` (`resegment.py:79-93`);
  - the even-split fallback slices by codepoint count (`resegment.py:175-178`);
  - the LLM re-split prompt uses `LANGUAGE_NAMES.get(language, "Chinese")` (`resegment.py:183-185`).
- `subtitle_formats.py`:
  - `LINE_CHAR_LIMITS = {"zh": 16, "ja": 16, "ko": 20, "en": 42}` with default 42 (`subtitle_formats.py:31-32`);
  - `_break_point` breaks at CJK punctuation, then at a space, then anywhere only if `_is_cjk`; otherwise it keeps a word whole (`subtitle_formats.py:48-63`);
  - CPS tables are per script and include an `abugida_rtl` class whose regex covers Thai `฀-๿` (`subtitle_formats.py:134-139, 160-170`);
  - `reading_speed = len(text.strip())/duration` counts codepoints, including combining marks (`subtitle_formats.py:179-180`);
  - `dense_lines` defaults to `field="en"`, and all callers use the default (`services/export_service.py:323, 420`; `bulk_translate.py:521, 1624`).
- Merge-short: `core.merge_adjacent_short_lines` joins source text as `prev.zh.rstrip() + ln.zh.strip()` with no separator (`core.py:759-794`). Restructure's merge does the same (`services/restructure_service.py:261`).
- Translate-by-sentence: `sentence_groups._SENTENCE_FINAL` is CJK and Latin marks only, and `is_sentence_final` checks the last character (`sentence_groups.py:19-38`). `join_fragments` inserts a space between non-CJK pieces (`sentence_groups.py:55-64`). Limits are `MAX_GROUP_CHARS = 60` and `MAX_GROUP_LINES = 4` (`sentence_groups.py:12-15`).
- Fonts and burn-in:
  - `FONT_CHOICES` has no Thai-capable face (Arial, Arial Black, Segoe UI, Microsoft YaHei, Noto Sans CJK SC, Meiryo, Malgun Gothic) (`subtitle_formats.py:333-334`);
  - the export options say `"custom_font_allowed": True` (`services/export_service.py:168-169`);
  - burn-in passes `FontName=` in libass `force_style` (`video_export.py:129-147`);
  - the source-field and bilingual language label comes from `export_service.field_language` (`services/export_service.py:70-73`).
- UI fonts: `:lang(zh|ja|ko)` rules and `--font-zh/ja/ko` stacks with `--leading-cjk: 1.6` (`frontend/src/index.css:59, 74-76, 192-194`). The base font is a Latin-only Atkinson Hyperlegible subset (`docs/frontend-design-system.md:74`).
- PyThaiNLP: `word_tokenize(text, custom_dict=None, engine='newmm', keep_whitespace=True, ...)`; newmm is the default and is "constrained by Thai Character Cluster (TCC) boundaries"; `subword_tokenize` defaults to TCC. — [PyThaiNLP tokenize docs](https://pythainlp.org/dev-docs/api/tokenize.html)
- PyThaiNLP on PyPI: version 5.4.0, Apache-2.0, Python >=3.9, pure-Python wheel of 21.9 MB. — [PyPI pythainlp](https://pypi.org/project/pythainlp/)

### Inferences
- **Integration point for a Thai segmenter:** add `language == "th"` branches to `segment.segment_words` and `segment.segment_and_annotate`. Use `pythainlp.tokenize.word_tokenize(text, engine="newmm", keep_whitespace=True)` so the words reassemble exactly into the text (as `resegment.word_boundaries` requires, `resegment.py:88-89`), with reading None. `segment.py` is 6.9 KB, so this needs no new file.
- That one branch makes these work for Thai:
  - `resegment` word boundaries;
  - `word_align` realignment;
  - Reader segmentation, which comes for free although Reader is out of scope.
- **`segment_splitting` needs a "dense script" notion that includes Thai.** Options:
  - widen `_CJK_RE` to a "no-space script" regex that adds `฀-๿`, so spaces next to Thai become phrase breaks and Thai characters count toward limits;
  - better, count Thai by base characters (exclude Mn marks U+0E31, U+0E34-0E3A, U+0E47-0E4E) so a 40-unit limit means visible width.

  Without this, stub detection merges Thai pieces back together.
- `LINE_CHAR_LIMITS` needs a `"th"` entry. The number is an owner decision: Netflix-style Thai guides commonly use about 35-42 characters per line. The sibling note `thai_segmentation_and_subtitle_style.md` covers the evidence, which I did not verify here.
- Even-split and `wrap_text` hard cuts must never cut before a combining mark. Use TCC (`subword_tokenize`) or a check against Mn characters.
- Merge-short should join with a space when both sides are Thai, because in Thai the space marks a phrase or sentence boundary. The same no-space join already looks wrong for Korean (pre-existing; see section 7).
- For translate-by-sentence, Thai rarely uses sentence-final punctuation, so `is_sentence_final` is always False. Consecutive same-speaker Thai lines under 1 s apart will group (up to 4 lines or 60 codepoints). This is probably acceptable, but it changes batching behaviour and needs a test.
- Burned-in subtitles are usually English, so Thai fonts matter only for bilingual or source-field exports and the UI. Adding "Leelawadee UI" (Windows) and "Noto Sans Thai" to `FONT_CHOICES` and a `:lang(th)` CSS rule with a taller line height is P1 work.

### Gaps
- I could not determine libass font fallback for Thai glyphs on the Windows ffmpeg build the app ships (DirectWrite vs fontconfig). That needs a manual burn-in test.
- I did not determine PyThaiNLP's transitive runtime dependencies or its first-use data download behaviour from the PyPI page. The page doesn't list them; check `pyproject.toml` before adding it to `constraints.txt`.

## 4. Translation, glossary, names, prompts, CLI

### Takeaway
LLM translation is mostly driven by language names, so adding `"th": "Thai"` to `LANGUAGE_NAMES` fixes most prompts. Two content problems remain:
- **glossary term matching treats Thai as a word-boundary script**, so a Thai glossary term inside running Thai text won't match;
- the metadata romanisation and term policies are written for pinyin, Hepburn and Revised Romanization.

The CLI takes language from the title or `--language` and validates through the services, so it needs no flag changes.

### Cited Findings
- Main prompt: `source_language_name = LANGUAGE_NAMES.get(source_language, "the source language")` (`engine_backends/prompts.py:115-122`). The flag prompts use `language_name(source_language, 'source-language')` (`engine_backends/llm_tasks.py:577, 759`; `engine_backends/shared.py:415-427`).
- The pronoun-default block is Chinese-only (`engine_backends/prompts.py:162-168, 198`).
- Fallback to "Chinese": `translation_guide.py:325, 629, 730`; `story_context.py:183`; `line_tools.py:24, 51, 78, 99`; `resegment.py:184`; `services/reader_service.py:444`; `services/benchmark_judge_service.py:175`.
- Metadata romanisation rules name "pinyin for Chinese, Hepburn for Japanese, Revised Romanization for Korean" (`translation_guide.py:736-737`). The glossary term policy default is `"keep_pinyin"` (`translation_guide.py:245, 356, 680`).
- Glossary matching: `_CJK_CHARS` excludes Thai, and `_variant_pattern` adds `(?<!...)`/`(?!...)` word-boundary lookarounds when the variant's first or last character is `\w` and not CJK (`translation_guide.py:482-497`). It is used at `translation_guide.py:552`.
- The "terms in play" helper is a plain substring test (`engine_backends/llm_tasks.py:736-745`), so it works for Thai.
- Auto-QC: number words are parsed only for CJK sources (`auto_qc.py:77, 428-433`), and name aliases are split into source and target forms by `_CJK_CHAR_RE` (`auto_qc.py:331-338`).
- `en_cleanup.has_cjk_letters` protects non-English text from the English cleanup rules, for CJK only (`en_cleanup.py:40, 75-79, 286`).
- `sentence_groups.join_fragments` puts a space between Thai fragments (`sentence_groups.py:55-64`).
- CLI:
  - `cli.py transcribe --language` has no `choices` and passes through to `transcribe_service` (`cli.py:1052-1053, 806`);
  - transcription reads `d.get("source_language") or "zh"` (`cli.py:468`);
  - `asr_benchmark.py:198` uses `choices=list(SOURCE_LANGUAGES)`;
  - `scripts/smoke_pack.py:596` hard-codes `("zh","ja","ko")` for language packs, which are out of scope;
  - `cli_translate.py`/`cli_subtitle.py` have no language choice lists (grep found none).

### Inferences
- P0 translation needs only `LANGUAGE_NAMES["th"] = "Thai"`.
- P1/P3 should:
  - add Thai to `_CJK_CHARS` (rename it to "no-space scripts") in `translation_guide.py`, so glossary enforcement works inside Thai text;
  - add "RTGS / common English spelling for Thai names" to `romanize_metadata`;
  - add `฀-๿` to `en_cleanup._CJK_LETTER_RE` so untranslated Thai left in the English field isn't "cleaned" as English;
  - add Thai to auto-QC's source-script regex.
- Thai has politeness particles (ครับ/ค่ะ) and pronoun systems that the prompt's "pronouns, honorifics, and register" line covers generically. Whether a Thai-specific style note is needed is a quality question for the owner and the translation research note.

### Gaps
- I did not inspect `bulk_translate.py` (86 KB) line by line. A grep of it found no zh/ja/ko literals or `LANGUAGE_NAMES` fallbacks, but behaviour on Thai is untested.

## 5. API, frontend, Benchmark Lab, docs

### Takeaway
The API schemas mostly take free `str` and rely on service validation. The exceptions:
- the benchmark `Literal`, which follows core automatically;
- Jellyfin's `Literal["en","zh","ja","ko"]` with ISO-639-2 maps.

The frontend hard-codes the list in at least eight places, plus CSS and three tests.

### Cited Findings
- API:
  - `api/schemas/library.py:125, 255-258` and `api/schemas/transcribe.py:107, 122, 222, 251` use `str` with descriptions naming zh/ja/ko;
  - `api/schemas/review.py:123-124, 374-375, 401-402` use `lang: str` (described as "zh/ja/ko/en");
  - `api/benchmark_schemas.py:16, 59, 78` use `SourceLanguage = Literal[SOURCE_LANGUAGES]`;
  - `api/jellyfin_schemas.py:48, 75` use `Literal["en","zh","ja","ko"]`;
  - `services/jellyfin_service.py:69-71` has `LANG_CODES` and `_LANG_ALIASES` without Thai.
- Other code maps:
  - subtitle sidecar import tokens have no `th`/`tha` (`subtitle_sidecar.py:20-25`);
  - OCR maps have no Thai (`ocr.py:20, 118`; `hardsub_ocr.py:174`);
  - `page_server.py:115, 706` has `ALLOWED_TEXT_LANGUAGES = {"zh","ja","ko","en"}` (extension page translate, out of scope).
- Frontend lists:
  - `frontend/src/pages/libraryForm.ts:6` `SOURCE_LANGUAGES`, used by Library create, `LibraryList` filter, `DetailsPanel`, `detailsForm` and `DramaPicker`;
  - `frontend/src/pages/workspace/stages/TranscribeStage.tsx:66, 566-570` (hard-coded `<option>`s and a `LANGUAGE_NAMES` map);
  - `frontend/src/pages/workspace/stages/review/reviewDraft.ts:18` `LINE_LANGUAGES`;
  - `frontend/src/api/benchmark.ts:15` `SourceLanguage`;
  - `frontend/src/pages/makeSubtitles/MakeSubtitles.tsx:36, 231`;
  - `frontend/src/pages/sources/urlImportFormat.ts:115-119`;
  - `frontend/src/labels.ts:41-48` `LANGUAGE_LABELS`;
  - `frontend/src/pages/workspace/stages/ExportJellyfin.tsx:28`;
  - `frontend/src/types/jellyfin.ts:29`;
  - `frontend/src/pages/workspace/metadataForm.ts:105`, an ISO3 map. Thai appears only as a display name at `metadataForm.ts:108`.
- Out-of-scope lists: `frontend/src/api/translate.ts:15-16`, `frontend/src/api/live.ts:24-25`, `frontend/src/pages/discover/discoverFormat.ts:16-17`.
- Frontend tests that pin the list:
  - `frontend/src/pages/workspace/stages/review/reviewLogic.test.ts:25` expects `['zh','ja','ko','en']`;
  - `frontend/src/pages/benchmark/benchmarkForm.test.ts:18` (a fixture);
  - `frontend/src/typeScale.test.ts:35` checks `:lang(zh|ja|ko)` exists, so adding `th` there is optional;
  - `frontend/src/components/uiKit.test.ts:86` expects `humanize('language','th')` to be `'Th'`, **which breaks as soon as `LANGUAGE_LABELS` gains `th: 'Thai'`**.
- I found no `source_languages` in `frontend/e2e` (grep).
- Benchmark Lab: options return `"source_languages": list(SOURCE_LANGUAGES)` (`services/benchmark_lab_service.py:408`). Unknown case languages fall back to `"zh"` (`services/benchmark_lab_service.py:386`; `services/benchmark_case_service.py:33`).
- Docs listing zh/ja/ko: `README.md:23`, `docs/frontend-design-system.md:74`, `docs/browser-extension.md:79` (out of scope), `docs/engine-backends.md:332` (dub, out of scope) and `docs/STATUS.md:12`.

### Inferences
- The frontend should import one list (for example from `libraryForm.ts`) in TranscribeStage and MakeSubtitles rather than add a fourth hard-coded copy. That is in-scope cleanup because Thai would otherwise need edits in each copy.
- **Video-only scope:** `drama_service.create_drama` validates language and media type independently (`services/drama_service.py:123-129`). To keep Thai off novels and comics, either:
  - (a) add a service rule "th only with media_type in {video_drama, anime, streamer_vod, audio_drama, asmr}" plus a UI filter; or
  - (b) accept Thai everywhere and let the novel and scanlate paths degrade.

  This is an owner decision (see section 8).
- Jellyfin export of a Thai source track would map to no ISO code. Add `"th": "tha"` in P3.

### Gaps
- I did not open every `.tsx` that renders `languageLabel`, so a few display-only spots may remain. They fall back to `humanize`, which shows "Th".

## 6. Tests and dependency registration

### Takeaway
There are about six Python and two frontend assertions to update, plus about 10 to 12 new focused tests. PyThaiNLP must be registered in `diagnostics.OPTIONAL_DEPENDENCIES`, `APPROX_DOWNLOAD_MB`, an `INSTALL_TASKS` entry, `requirements-optional.txt` and probably `constraints.txt`, all in the same change. The import name and dist name are both `pythainlp`.

### Cited Findings
- Registration pattern:
  - `OPTIONAL_DEPENDENCIES` is `name -> (import name, feature, tier)` (`diagnostics.py:22-24`), for example `"kiwipiepy": ("kiwipiepy", "Korean word segmentation (Reader)", "feature")` (`diagnostics.py:48`);
  - sizes go in `APPROX_DOWNLOAD_MB` (`diagnostics.py:148-163`);
  - task presets go in `INSTALL_TASKS`: `reader_ko` holds `["kiwipiepy"]` and `alt_asr` holds nagisa and soynlp (`diagnostics.py:213-249`);
  - pip pins go in `requirements-optional.txt:96-99`.
- `install_registry.py:1-9` makes registry keys the only names that ever reach pip.
- Tests covering these: `tests/test_install_presets.py`, `tests/test_diagnostics_and_export.py`, `tests/test_qwen3_native.py` (they reference `OPTIONAL_DEPENDENCIES`).
- Size guard: `MAX_MODULE_BYTES = 40*1024`, and allowlisted files "may shrink but never grow" (`tests/test_static_analysis.py:738-792`). Current sizes against their caps:
  - `services/restructure_service.py`: 52,458 bytes, cap 52,468 (**10 bytes of headroom**);
  - `core.py`: 46,913, cap 84,133;
  - `services/transcribe_service.py`: 58,665, cap 102,565;
  - `diagnostics.py`: 55,463, cap 109,290;
  - `db/__init__.py`: no edit needed.
- Files under 40 KB with their headroom:
  - `translation_guide.py`: 36,718 (about 4.2 KB);
  - `services/reader_service.py`: 37,921;
  - `asr_backend.py`: 26,972;
  - `segment_splitting.py`: 25,353;
  - `subtitle_formats.py`: 21,771;
  - `segment.py`: 6,883.
- PyThaiNLP: 21.9 MB pure-Python wheel, Apache-2.0 (data CC0/CC-BY-4.0). — [PyPI](https://pypi.org/project/pythainlp/)

### Inferences
- **Existing tests to change when `th` is added:**
  - `test_shared_constants.py:20-21`;
  - `test_forced_align.py:32, 38` and `test_qwen3_native.py:438` (rewritten to pin "aligner languages exclude th");
  - `test_mixed_language.py:207-208` (QWEN names gain Thai);
  - `test_shared_constants.py:26`, if `forced_align.LANGUAGE_NAMES` stops aliasing core;
  - frontend `reviewLogic.test.ts:25` and `uiKit.test.ts:86` (switch the fallback example to an unlisted code such as `'xx'`).

  These rewrites are allowed under CLAUDE.md because the behaviour they pinned changed. The PR summary must list them.
- **New tests:**
  - `drama_service` accepts `th` (and refuses it for novel/comic media types if option (a) is chosen);
  - `transcribe_service` accepts `th` with Whisper and refuses `qwen3_forced_align`, `qwen3_asr_long` and refine-timing for `th` with a plain message;
  - `retime_service` refuses `th`;
  - `mixed_language.text_matches_language("สวัสดี", "th")` is True;
  - `segment.segment_words(..., "th")` round-trips (via `pytest.importorskip("pythainlp")`);
  - a no-segmenter fallback test;
  - `segment_splitting` cuts a Thai line at spaces and pauses and doesn't stub-merge it;
  - no split lands before a combining mark;
  - `LINE_CHAR_LIMITS["th"]`;
  - Thai merge-short joins with a space;
  - `translation_guide` glossary matches a Thai term inside Thai text;
  - the prompt contains "Thai".
- **Package risk:** PyThaiNLP should be imported lazily inside `segment.py`, like jieba and sudachi, and optional, with a no-segmenter fallback such as `tests/test_reader_no_segmenter.py`. It adds about 22 MB to a Windows install if chosen. Whether it pulls heavier dependencies needs a `pyproject.toml` check (gap).
- **Any edit to `restructure_service.py` must be byte-neutral or move code out.** That matters if the merge-separator fix lands there (`services/restructure_service.py:261`). Put shared join logic in `core.py` or `sentence_groups.py` and call it from restructure with a net size reduction.

### Gaps
- I didn't check whether `constraints.txt` is a full lock that requires every new optional package; `grep` found no kiwi, sudachi or jieba lines in it. Confirm the convention in `make_lock.bat` and `docs/runbook.md`.

## 7. Inventory, phased plan, the db/ question and open questions

### Takeaway
Thai can ship in four phases without touching `db/`. P0 (about 1.5-2 days) makes a Thai video transcribe with Whisper large-v3 and translate with an LLM. P1 (about 2-3 days) makes splitting, merging, wrapping, glossary matching and fonts behave. P2 (about 2-4 days, mostly research and benchmarking) brings a better Thai ASR model and alignment. P3 (about 1-2 days) covers polish, Jellyfin, docs and parity.

### Cited Findings
Inventory. Each row's evidence is the cited line; effort and risk are my estimates.

| # | File (line) | What assumes zh/ja/ko | Change for Thai | Phase | Risk |
|---|---|---|---|---|---|
| 1 | `core.py:21-25` | `SOURCE_LANGUAGES`, `LANGUAGE_NAMES` | add `"th"`, `"Thai"`; `LINE_LANGUAGES` follows | P0 | Medium: fans out to every importer (Live, sources, epub, discover, benchmark) |
| 2 | `forced_align.py:55,66-68`; `asr_backend.py:48` | aligner set derived from core names | define aligner names explicitly (`qwen3_native.ALIGNER_LANGUAGE_NAMES` + en); keep the ASR gate on core names | P0 | High if missed: Thai reaches an aligner that doesn't support it |
| 3 | `services/transcribe_service.py:422-436` | no language gate for aligner choices | refuse `qwen3_forced_align`, `qwen3_asr_long` and refine-timing for languages the aligner lacks | P0 | Low |
| 4 | `services/retime_service.py:82`; `services/compare_transcription_service.py:76,312` | gated on name dicts | follow from row 2; add tests | P0 | Low |
| 5 | `services/drama_service.py:123-125`; `services/discover_catalog_service.py:51` | hard-coded "zh, ja, ko" messages | build the message from the tuple; optional media-type rule for `th` | P0 | Low |
| 6 | `mixed_language.py:25-38` | no Thai script, so Thai spans "disagree" | add a `thai` script and `_EXPECTED_SCRIPTS["th"]` | P0 | Low |
| 7 | `segment.py:107-121,179-184`; `word_align.py:216-225` | non-ja/ko text goes to jieba | `th` branch using PyThaiNLP newmm (keep_whitespace); fallback to space split, or return unsplit | P0 (gate) / P1 (segmenter) | Medium: optional dependency |
| 8 | `core.py:362-373` | stock outro phrases have no Thai | add Thai outro regexes | P1 | Low |
| 9 | `whisper_models.py:16-22`; `frontend/src/pages/workspace/sourceForm.ts:161-170` | turbo is the default and warnings cover ko/zh | turbo warning for `th` (or a per-language default of large-v3) | P0 warning / P2 default | Low |
| 10 | `segment_splitting.py:16-25,64-67,171,269-272` | CJK-only counting, CJK-only space breaks, word-count stubs | no-space-script regex including Thai; base-character count; Thai spaces as phrase breaks | P1 | Medium: shared by every language, regression risk |
| 11 | `long_line_split.py:27-29,97` | `_CJK_RE` count | follow row 10 | P1 | Low |
| 12 | `services/restructure_service.py:741-752,261` | CJK presets; join without a space | `line_char_limit("th")`; separator from a shared helper | P1 | High for the size guard: 10 bytes of headroom |
| 13 | `resegment.py:43-58,66-93,175-185` | CJK punctuation and connectives; even split by codepoint | Thai connectives (optional); TCC-safe even split | P1 | Medium |
| 14 | `subtitle_formats.py:31-32,48-63,333-334` | `LINE_CHAR_LIMITS`; wrap; fonts | `"th"` limit; cluster-safe break; add Leelawadee UI / Noto Sans Thai | P1 | Low |
| 15 | `core.py:759-794` | merge-short joins source text with no space | join with a space for space-separating scripts (th, also ko) | P1 | Low-medium: also changes Korean (pre-existing bug) |
| 16 | `sentence_groups.py:19-38` | sentence-final marks | accept grouping, or treat a pause or space as final for `th` | P1 | Medium: changes batching |
| 17 | `translation_guide.py:482-497,736-737` | glossary word boundaries; romanisation rules | Thai in the no-boundary set; RTGS rule | P1 | Low (about 4 KB headroom) |
| 18 | `en_cleanup.py:40,75-79`; `auto_qc.py:77,331-338,428` | CJK letter detection | add the Thai range | P3 | Low |
| 19 | `engine_backends/prompts.py:115-122` and other name users | name lookup | automatic via row 1 | P0 | Low |
| 20 | `api/jellyfin_schemas.py:48,75`; `services/jellyfin_service.py:69-71` | Literal and ISO maps | add `th`/`tha` | P3 | Low |
| 21 | `subtitle_sidecar.py:20-25` | language tokens | add `th`, `tha` | P3 | Low |
| 22 | Frontend lists (section 5) and `index.css:192-194` | hard-coded lists, CSS | add Thai, de-duplicate, `:lang(th)` font and leading | P0 (pickers) / P1 (CSS) | Low |
| 23 | Tests (section 6) | pinned lists | update and add | P0-P3 | Low |
| 24 | `diagnostics.py:24,148-163,213-249`; `requirements-optional.txt` | no Thai segmenter | register pythainlp (same PR as row 7) | P1 | Low-medium: install footprint |
| 25 | Docs: `README.md:23`, `docs/STATUS.md:12`, `docs/asr-experiments.md` | language lists | update | P3 | Low |
| 26 | `db/__init__.py:336` | comment only | none (leave it) | none | none |

**Phases** (effort in builder-session days):

- **P0, minimal end-to-end (S-M, about 1.5-2 days).**
  - Files: `core.py`, `forced_align.py` (and possibly `asr_backend.py` import), `services/transcribe_service.py`, `services/drama_service.py`, `services/discover_catalog_service.py` (message), `mixed_language.py`, `segment.py` (Thai gate or fallback), `word_align.py` (if gated there), `frontend/src/pages/libraryForm.ts`, `TranscribeStage.tsx`, `reviewDraft.ts`, `labels.ts`, `api/benchmark.ts`, `MakeSubtitles.tsx`, `sourceForm.ts`, plus tests.
  - That is more than AGENTS.md's four-file brief limit (`AGENTS.md:12`), so split it into backend (core, aligner decoupling, services, mixed_language, segment gate, tests) and frontend (pickers and tests) briefs.
  - Tests: section 6's rewrites, plus new service-gate tests. Run `tests/test_shared_constants.py`, `test_forced_align.py`, `test_qwen3_native.py`, `test_mixed_language.py`, `test_transcribe_service.py`, `test_drama_service.py`, `test_word_align*`, the quick guards, and `npx tsc -b && npx vitest run`.
  - Risks:
    - the fan-out of `SOURCE_LANGUAGES` silently enables Thai in Live, sources, epub, discover and benchmark;
    - the aligner decoupling is easy to forget;
    - Whisper turbo quality on Thai.
- **P1, Thai-aware lines (M, about 2-3 days).**
  - Files: `segment.py` (PyThaiNLP), `diagnostics.py`, `requirements-optional.txt`, `segment_splitting.py`, `long_line_split.py`, `subtitle_formats.py`, `resegment.py`, `core.py` (merge join, stock phrases), `sentence_groups.py`, `translation_guide.py`, `services/restructure_service.py` (byte-neutral), `frontend/src/index.css`, `ResplitLines.tsx` help text.
  - Tests: `test_segment*`, `test_resplit_lines.py`, `test_resegment*`, `test_long_line_split*`, `test_subtitle_formats*`, `test_sentence_groups*`, `test_translation_guide*`, `test_install_presets.py`, `test_diagnostics_and_export.py`, `typeScale.test.ts`.
  - Risks: regressions in shared splitting for zh/ja/ko; picking the CPL number; the restructure size cap.
- **P2, better ASR and alignment (M-L, about 2-4 days, gated on the benchmark).**
  - Options:
    - (a) document using a CTranslate2-converted Thai fine-tune through the existing `whisper_model_path` setting. [Typhoon Whisper large-v3](https://huggingface.co/typhoon-ai/typhoon-whisper-large-v3) is MIT and fine-tuned from large-v3, but ships Transformers safetensors, not CTranslate2, so it needs conversion along the lines of `tools/ja_whisper_models.py`;
    - (b) allow Qwen3-ASR `qwen3_asr` and `qwen3_asr_vad` (without refine) for Thai, since Qwen3-ASR lists Thai;
    - (c) word timing from MMS (`word_align`, uroman) once `segment.py` has a Thai branch.
  - Benchmark Lab cases need Thai clips. Files: `whisper_models.py` and/or `tools/`, `asr_backend.py`, `services/asr_options_service.py`, `docs/asr-experiments.md`.
  - Risks: no Thai baseline in the repo; aligner gaps; licensing of fine-tune training data (unverified).
- **P3, polish, parity and docs (S, about 1-2 days).**
  - Jellyfin and ISO codes, sidecar tokens, `en_cleanup` and `auto_qc` ranges, romanisation rule, Thai stock phrases if not done, README/STATUS/docs, and the `asr_benchmark` CLI (automatic).
  - The CLI needs no flag changes (`cli.py:1052-1053` validates through services). Check `python cli.py transcribe --language th` against a mocked run.

### Inferences
- **The db/ question:** Thai needs **no `db/` edit**. Nothing in `db/` whitelists languages; the filter imports `core.LINE_LANGUAGES` at runtime (`db/__init__.py:2434`), and the columns are plain TEXT. All phases can proceed during the db split. The stale schema comment (`db/__init__.py:336`) should be left alone.
- The biggest hidden coupling is row 2 (aligner gates riding on `core.LANGUAGE_NAMES`). The biggest process trap is row 12 (restructure has 10 bytes of headroom).
- A pre-existing problem noticed along the way: `core.merge_adjacent_short_lines` and restructure's merge glue Korean source lines without a space (`core.py:794`, `services/restructure_service.py:261`). I have not verified this in a run.
- **Open questions for the owner:**
  1. Should Thai be accepted only for video and audio media types (needs a service rule), or everywhere, with novels and comics left degraded?
  2. Should Live accept Thai? It does as soon as `SOURCE_LANGUAGES` gains it (`services/live_service.py:298`).
  3. Should Thai default to Whisper large-v3 rather than turbo, as a per-language default or only a warning?
  4. Is PyThaiNLP (about 22 MB) acceptable as an optional install, and under which Diagnostics task: a new "Thai word splitting" task, or attached to transcription?
  5. What Thai characters-per-line limit should apply, and should counts exclude combining marks?
  6. Should Thai spaces be treated as sentence boundaries for translate-by-sentence grouping?
  7. Should the Korean no-space merge bug be fixed in the same PR as Thai?
  8. Are there real Thai clips with reference transcripts for Benchmark Lab before P2?

### Gaps
- Effort sizes are judgement, not measured.
- I did not read the full Live, epub, discover and sources flows to confirm they behave sensibly with `th`. I confirmed only that they would accept it.
