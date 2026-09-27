# File organization

Almost every filename in this project is unique, even across folders —
if you're downloading files individually, a file's name tells you
unambiguously where it belongs: anything ending `_tab.py` goes in
`tabs/`, anything starting `test_` goes in `tests/`, everything else
sits at the top level or in one of the subsystem packages below. The
one expected exception is `__init__.py` (an empty marker in every
package). The other exception to "Python" is `extension/`, which is
browser-side JavaScript loaded by Chrome rather than anything Python
imports.

```
baihe-subtitler/
│
├── app.py                     ← START HERE:  streamlit run app.py
├── common.py                     shared imports every tab pulls in
├── cli.py                        headless batch runner
├── run_tests.py                  test runner wrapper
│
├── requirements.txt              everything (simplest install)
├── requirements-core.txt         minimum to launch + translate text
├── requirements-media.txt        audio/video: align, dub, burn subtitles
├── requirements-optional.txt     per-feature extras
├── requirements-install.bat      Windows: installs the right requirements file(s)
├── constraints.txt               upper bounds for packages that have broken this app before
├── start.bat                     one-click Windows launcher
├── start.ps1                     PowerShell version of the launcher (start.bat is primary)
├── make_lock.bat                 snapshots installed package versions to constraints.lock.txt
├── make_shortcut.bat             creates a desktop shortcut to start.bat
├── uninstall.bat                 this app has no registry/Program Files footprint to clean up
├── pytest.ini                    test config
├── .gitignore                    excludes library/ and .env
├── .env.example                  copy to .env for persistent API keys
├── README.md
├── FILE_ORGANIZATION.md          this file
├── CLAUDE.md                     rules for AI sessions working in this repo
│
├── .streamlit/
│   └── config.toml               visual theme
│
├── assets/
│   └── app_icon.ico              used by make_shortcut.bat / packaging
│
├── docs/
│   ├── adding-source.md          how to write a new sources/adapters/*.py adapter
│   ├── browser-extension.md      the Translate-the-page-you're-reading feature
│   ├── content-sources.md        every source the Sources tab can reach, and its status
│   ├── handoff-browser-extension.md   handoff notes for the browser extension work
│   ├── known-working-sources.md  quick "can I point the app at this site" status board
│   └── technical-notes.md        engineering changelog: real bugs found + how they were fixed
│
├── tabs/                      ← UI ONLY. One file per tab, 10 tabs total.
│   ├── __init__.py               (empty, marks the package)
│   ├── library_tab.py            dashboard, filters, backup, storage, quick-filter tags
│   ├── workspace_tab.py           the main pipeline: align → translate → dub → export
│   ├── reader_tab.py             reading, wiki, story tools, line tools
│   ├── scanlate_tab.py           manhua/webtoon typesetting
│   ├── discover_tab.py           title discovery, bulk import, site navigation help
│   ├── sources_tab.py            paste any URL, run it through the sources/ adapter pipeline
│   ├── translate_tab.py          standalone translate tool: paste or upload text, translate it
│   ├── live_tab.py               near-live translation of an ongoing stream
│   ├── settings_tab.py           sidebar: API keys, appearance, defaults
│   └── diagnostics_tab.py        "check my setup"
│
├── sources/                   ← the site-adapter system (roadmap Step 23 and its sub-steps).
│   ├── __init__.py               (empty, marks the package)
│   ├── base.py                    the adapter interface every site implements
│   ├── models.py                  shared vocabulary (result/chapter/etc. types) for the system
│   ├── registry.py                which adapters exist and which are switched on
│   ├── pipeline.py                hands fetched content to the rest of the app
│   ├── detect.py                  names what happened when a fetch didn't go as expected
│   ├── ladder.py                  the access-method ladder (try the cheap method, then the next)
│   ├── adaptive.py                the order methods are tried in for a pasted URL, learned over time
│   ├── ai_extract.py              LLM-based extraction, used only as a fallback
│   ├── auth_browser.py            signed-in browser sessions for sites that need a login
│   ├── cache.py                   the configurable raw-content cache
│   ├── chapter_check.py           scheduled checks for new chapters on tracked titles
│   ├── chapter_order.py           sorts chapter lists the way a reader would expect
│   ├── front_door.py              the single "paste any URL" entry point
│   ├── generic_import.py          one-off imports for sites with no dedicated adapter
│   ├── health.py                  per-source health status (🟢/🟡/🔴, last success/failure)
│   ├── http.py                    the one paced HTTP client every adapter shares
│   ├── lzstring.py                LZString decoding (some sites compress embedded JSON with it)
│   ├── preflight.py               "will this site work?", answered before committing to import
│   ├── profiles.py                per-domain extraction profiles
│   ├── site_terms.py              terms-of-service findings for sites with no adapter
│   ├── store.py                   persistence for the source-adapter system
│   └── adapters/                  one file per supported site (13 sites)
│       ├── __init__.py
│       ├── 52shuku.py, baozimh.py, bilibili.py, bilibili_manga.py, guazimanhua.py,
│       └── kuaikan.py, mangaz.py, manhuagui.py, manhuaku.py, miaoqumh.py,
│           toonkor.py, xbanxia.py, zerosumonline.py
│
├── ui/                         ← small shared UI building blocks used across tabs (Step 13).
│   ├── __init__.py               (empty, marks the package)
│   ├── project_header.py         the compact, always-visible project header
│   ├── project_state.py          the unified project-state model
│   ├── status.py                 the shared background-job status block
│   └── workflow.py               the pipeline-stage stepper
│
├── extension/                  ← BROWSER SIDE. Loaded unpacked, not a Python package.
│   ├── manifest.json              MV3; loopback host permission only
│   ├── background.js              service worker: holds the token, calls the app
│   ├── content.js                 injected on a click: reads pages, draws overlays
│   ├── popup.html / popup.js      pick a drama, send, toggle
│   ├── options.html / options.js  paste the token
│   └── verify_end_to_end.py       standalone script that checks the extension ↔ app handshake
│
├── tests/                      ← 94 test files, 2,800+ test functions. Run: python run_tests.py
│   ├── __init__.py
│   ├── conftest.py                fixtures (isolated temp database, etc.)
│   └── test_*.py                  one or more files per module above, named to match
│
└── library/                    ← YOUR DATA. Created automatically. Gitignored.
    ├── library.db                everything: dramas, lines, glossaries, progress
    ├── cedict.txt                Chinese dictionary (downloaded once)
    └── dramas/<id>/              per-drama files
        ├── source.mp3|mp4        original media
        ├── audio.wav             extracted audio (video sources)
        ├── transcript.txt
        ├── novel_reference.txt
        ├── cover.jpg
        ├── dub_track.wav
        ├── dub_clips/            per-line TTS (regenerable — safe to clean)
        ├── voice_refs/           voice-cloning samples (NOT regenerable)
        └── pages/                manhua pages + typeset output
```

## Top-level modules, by subsystem

**App entry & shared infrastructure**
| File | Does |
|---|---|
| `app.py` | Streamlit entry point |
| `common.py` | shared imports every tab pulls in |
| `cli.py` | headless batch runner (kept in parity with the Workspace tab) |
| `run_tests.py` | test runner wrapper |
| `core.py` | timing, alignment, SRT formatting, line merging |
| `db.py` | all database access (plain `sqlite3`, no ORM) |
| `background_jobs.py` | in-memory background-job tracker (thread + dict) |
| `applog.py` | a single rotating log file for the whole app |
| `diagnostics.py` | environment self-check: which optional dependencies/models are available |
| `check_setup.py` | `start.bat`'s "print anything missing in plain words" check |
| `portable.py` | lets the whole app folder be copied/moved and still work |
| `ui_theme.py` | design system (CSS, layout primitives) |
| `app_help.py` | "App Assistant": ask "where is X" or "is this a bug" |
| `storage.py` | disk usage, cache cleanup |
| `benchmark.py` | the Benchmark Lab: regression tracking against your own reference cases, across every content type (audio drama, streamer VOD, novel, manhua) |
| `action_tiers.py` | 🟢/🟡/🔴 action-permission-tier classification an AI-driven feature checks before acting |

**ASR / transcription & alignment**
| File | Does |
|---|---|
| `asr_backend.py` | pluggable transcription: Whisper (default) vs Qwen3-ASR |
| `asr_benchmark.py` | Whisper vs Qwen3-ASR/ForcedAligner, one clip at a time |
| `audio_preprocess.py` | optional audio preprocessing before transcription |
| `forced_align.py` | Qwen3-ForcedAligner timing (alternative to `core.py`'s Whisper-diff alignment) |
| `word_align.py` | word-level forced alignment of Whisper's own transcribed text |
| `raw_transcript.py` | the untouched output of each transcription run, kept for reference |
| `resegment.py` | meaning-based subtitle re-segmentation |
| `sensevoice_tags.py` | optional audio-derived emotion and sound-event tags |
| `diarize.py` | who's speaking when (pyannote) |
| `voice_id.py` | recurring-voice suggestions ("SPEAKER_01 sounds like...") |

**Translation & quality**
| File | Does |
|---|---|
| `translate_engines.py` | Claude / DeepSeek / DeepL / Google / Ollama / LibreTranslate |
| `translation_guide.py` | style presets, term policies, translation notes |
| `translation_memory.py` | suggests a translation you already approved for an exact/near-identical line (never auto-applied) |
| `auto_qc.py` | flags a translation that drops or invents a number, date, name, amount or unit |
| `emotion.py` | emotional register detection and preservation |
| `bulk_translate.py` | the "Bulk (cheaper, slower)" translation mode |
| `live_translate.py` | near-live translation of an ongoing live stream |

**Dubbing, subtitles & video**
| File | Does |
|---|---|
| `dub.py` | TTS, voice cloning, track mixing |
| `video_export.py` | subtitle burn-in, softsub mux, dub muxing |
| `media_inspect.py` | probes a dropped file (duration/resolution/tracks) and suggests a pipeline, before a drama exists |
| `subtitle_formats.py` | WebVTT and ASS subtitle export, plus format checks |
| `video_download.py` | yt-dlp wrapper: fetch audio/video from a URL |

**OCR & scanlation**
| File | Does |
|---|---|
| `ocr.py` | Tesseract / PaddleOCR / manga-ocr |
| `scanlate.py` | bubble detection, inpainting, panels, webtoon strips |
| `hardsub_ocr.py` | extracts subtitle text burned directly into a video |
| `segment.py` | word segmentation (zh/ja/ko) |
| `dictionary.py` | CC-CEDICT + LLM definitions |
| `reader.py` | builds the interactive reader HTML |

**Story & learning**
| File | Does |
|---|---|
| `universe_wiki.py` | spoiler-bounded encyclopedia |
| `story_context.py` | character lookup, recaps, relationship maps |
| `qa.py` | ask questions about a drama |
| `line_tools.py` | explain / alternatives / improve / pronounce |
| `debug_view.py` | "what happened here?" per-line/per-job debugging view, bug record-and-replay |
| `adaptive_style.py` | learns your preferences from your edits |
| `vocab_export.py` | Anki decks |

**Discovery, sources & I/O**
| File | Does |
|---|---|
| `page_fetch.py` | fetching, JS-shell detection, render fallback |
| `metadata_lookup.py` | extract metadata from a listing page |
| `bulk_import.py` | many titles from one tag/ranking page |
| `title_library.py` | known-titles catalog + seed data |
| `known_sites.py` | directory of official platforms |
| `navigator.py` | translate a foreign site's labels + navigation steps |
| `epub_io.py` | EPUB import/export |
| `export_package.py` | per-drama archive bundle |

The `sources/` package (the adapter system proper, one file per supported
site under `sources/adapters/`) and the browser extension bridge
(`page_server.py` plus everything in `extension/`) are broken out in the
tree above rather than repeated here, since each is really its own
subsystem rather than a handful of top-level modules.

**Shared UI components (`ui/`)**
| File | Does |
|---|---|
| `ui/project_header.py` | the compact, always-visible project header |
| `ui/project_state.py` | the unified project-state model |
| `ui/status.py` | the shared background-job status block |
| `ui/workflow.py` | the pipeline-stage stepper |

## Rules of thumb

- **UI code goes in `tabs/`**, named `*_tab.py`. Logic lives at the top
  level (or in `sources/`/`ui/`) so it stays testable without Streamlit.
- **Nothing writes outside `library/`** except exports you explicitly download.
- **Optional dependencies are imported inside functions**, never at module
  top level — a missing package disables its own feature instead of
  stopping the app from starting.
- **`library/` is yours.** Back it up. It's gitignored for a reason.
- **Adding a new top-level module or `tabs/*.py` file? Update this file
  in the same step/PR.** See `CLAUDE.md`'s "Rules learned from real
  bugs" section — this drifted badly once already, which is why that
  rule exists.
