# File organization

Every filename in this project is unique — no two files share a name,
even across folders. If you're downloading files individually, a file's
name tells you unambiguously where it belongs: anything ending `_tab.py`
goes in `tabs/`, anything starting `test_` goes in `tests/`, everything
else sits at the top level. The one exception is `extension/`, which is
browser-side JavaScript loaded by Chrome rather than anything Python
imports.

```
baihe-subtitler/
│
├── app.py                     ← START HERE:  streamlit run app.py
├── common.py                     shared imports every tab pulls in
├── cli.py                        headless batch runner
├── asr_benchmark.py               Whisper vs Qwen3-ASR/ForcedAligner, one clip at a time
├── video_download.py              yt-dlp wrapper: fetch audio/video from a URL
├── run_tests.py                  test runner wrapper
│
├── requirements.txt              everything (simplest install)
├── requirements-core.txt         minimum to launch + translate text
├── requirements-media.txt        audio/video: align, dub, burn subtitles
├── requirements-optional.txt     per-feature extras
├── pytest.ini                    test config
├── .gitignore                    excludes library/ and .env
├── .env.example                  copy to .env for persistent API keys
├── README.md
├── FILE_ORGANIZATION.md          this file
│
├── .streamlit/
│   └── config.toml               visual theme
│
├── tabs/                      ← UI ONLY. One file per tab.
│   ├── __init__.py               (empty, marks the package)
│   ├── library_tab.py            dashboard, filters, backup, storage
│   ├── workspace_tab.py          the main pipeline: align → translate → dub → export
│   ├── reader_tab.py             reading, wiki, story tools, line tools
│   ├── scanlate_tab.py           manhua/webtoon typesetting
│   ├── discover_tab.py           title discovery, bulk import, site navigation help
│   ├── settings_tab.py           sidebar: API keys, appearance, defaults
│   └── diagnostics_tab.py        "check my setup"
│
├── extension/                 ← BROWSER SIDE. Loaded unpacked, not a Python package.
│   ├── manifest.json             MV3; loopback host permission only
│   ├── background.js             service worker: holds the token, calls the app
│   ├── content.js                injected on a click: reads pages, draws overlays
│   ├── popup.html / popup.js     pick a drama, send, toggle
│   └── options.html / options.js paste the token
│
├── tests/                     ← 860+ tests. Run: python run_tests.py
│   ├── __init__.py
│   ├── conftest.py               fixtures (isolated temp database)
│   ├── test_core.py
│   ├── test_db.py
│   ├── test_translate_engines.py
│   ├── test_scanlate.py
│   ├── test_translation_guide.py
│   ├── test_library_features.py
│   ├── test_wiki_adaptive.py
│   ├── test_emotion_manhua_ui.py
│   ├── test_page_fetch.py
│   ├── test_vocab_export.py
│   ├── test_bulk_import.py
│   ├── test_title_library.py
│   └── test_diagnostics_and_export.py
│
└── library/                   ← YOUR DATA. Created automatically. Gitignored.
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

## Top-level modules, by role

**Pipeline** — the core work
| File | Does |
|---|---|
| `core.py` | timing, alignment, SRT formatting, line merging |
| `db.py` | all database access (19 tables) |
| `translate_engines.py` | Claude / DeepSeek / DeepL / Google / Ollama / LibreTranslate |
| `translation_guide.py` | style presets, term policies, translation notes |
| `translation_memory.py` | suggests a translation you already approved for an exact or near-identical source line (never auto-applied) |
| `auto_qc.py` | Auto QC: flags a translation that drops or invents a number, date, name, amount or unit |
| `emotion.py` | emotional register detection and preservation |

**Audio & video**
| File | Does |
|---|---|
| `diarize.py` | who's speaking when |
| `asr_backend.py` | pluggable transcription: Whisper (default) vs Qwen3-ASR |
| `forced_align.py` | Qwen3-ForcedAligner timing (alternative to core.py's Whisper-diff alignment) |
| `dub.py` | TTS, voice cloning, track mixing |
| `video_export.py` | subtitle burn-in, softsub mux, dub muxing |

**Text & images**
| File | Does |
|---|---|
| `ocr.py` | Tesseract / PaddleOCR / manga-ocr |
| `scanlate.py` | bubble detection, inpainting, panels, webtoon strips |
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
| `adaptive_style.py` | learns your preferences from your edits |
| `vocab_export.py` | Anki decks |

**Discovery & I/O**
| File | Does |
|---|---|
| `page_fetch.py` | fetching, JS-shell detection, render fallback |
| `page_server.py` | localhost-only endpoint the browser extension sends pages to |
| `metadata_lookup.py` | extract metadata from a listing page |
| `bulk_import.py` | many titles from one tag/ranking page |
| `title_library.py` | known-titles catalog + seed data |
| `known_sites.py` | directory of official platforms |
| `navigator.py` | translate a foreign site's labels + navigation steps |
| `epub_io.py` | EPUB import/export |
| `export_package.py` | per-drama archive bundle |

**Infrastructure**
| File | Does |
|---|---|
| `storage.py` | disk usage, cache cleanup |
| `diagnostics.py` | environment self-check |
| `ui_theme.py` | design system (CSS, layout primitives) |

## Rules of thumb

- **UI code goes in `tabs/`**, named `*_tab.py`. Logic lives at the top
  level so it stays testable without Streamlit.
- **Nothing writes outside `library/`** except exports you explicitly download.
- **Optional dependencies are imported inside functions**, never at module
  top level — a missing package disables its own feature instead of
  stopping the app from starting.
- **`library/` is yours.** Back it up. It's gitignored for a reason.
