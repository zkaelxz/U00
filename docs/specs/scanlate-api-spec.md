# Scanlate API spec (proposal)

Status: proposal from a read-only architecture pass (2026-09-29), for the planning session and the user to confirm.
No code was written. Slice ids S0-S8 are local to this doc; the master index will assign real step numbers.
Evidence is `file:line` on `baihe-subtitler` at that date. "HYP" marks a hypothesis that was not verified.
Goal: expose Scanlate (page import, bubble detection, OCR, translate, cleanup, typeset, export) through FastAPI so a React canvas editor (frontend phase 7) can drive it.

## 1. What Scanlate does today

Pipeline, all in `scanlate.py` unless noted:
- **Detect:** `detect_bubbles_cv` :37 (OpenCV); `detect_bubbles_ml` :149 (RT-DETR via transformers, global model cache :193-199); dispatcher `detect_bubbles` :343 (auto = ML only if weights cached :227), raising `BubbleModelUnavailable` :333 with a CV fallback; `dedupe_overlapping_boxes` :279 (Step 35 fix).
- **Structure:** `analyze_page_regions` :1696 builds `TextRegion` :1659 using `classify_text_regions` :1254 (bubble/narration/sign/sfx/thought), `detect_panels` :1124, `estimate_text_orientation` :1625.
- **OCR:** `ocr_box_region` :1421 (`auto_ocr_backend` :1394; backends in `ocr.py:23`); style guess `sample_text_style` :1323.
- **Per page:** `detect_and_ocr_page` :1776 returns (bubbles, notes) with empty `translated_text`.
- **Translate:** `translate_page_with_context` :960 (LLM JSON; pure-MT engines skip context) and `translate_page_bubbles` :1839 (mutates in place, returns rolling context).
- **Batch:** `batch_process_pages` :1876 (`save_fn`, `progress_cb`, per-page isolation, no cancel hook).
- **Cleanup:** `inpaint_region` :613 (dark-pixel threshold 150, LaMa or Telea); `inpaint_mask_region` :681 (brush mask).
- **Typeset:** `process_page` :1059, `render_text_in_box` :882, `bubble_shape_mask` :1734, `bulk_render_pages` :1024 (zip), `pages_to_pdf` :1500, `export_font_style_report` :1368.
- **Import:** `pdf_to_page_images` :1461, `split_webtoon_strip` / `slice_webtoon_to_files` :1163 / :1224. UI-free upload is `add_uploaded_pages` (`tabs/scanlate_tab.py:598`); a second copy is `sources/pipeline.add_page_images` (`sources/pipeline.py:39`, no PDF or slicing).
- **Bulk find/replace:** `bulk_find_replace_preview` :1521 (pure; also imported by `services/review_lines_service.py:25`).
- **UI orchestration:** `tabs/scanlate_tab.py` (detect :146, batch :172, review form :226-297, retranslate :299, fonts :320, brush :347, render :427, manual add/OCR :467, bulk render :501, find/replace :548).
- **Second writer:** the browser-extension bridge `page_server.py:268-363` stores pages and bubbles via `save_bubbles`, serialised by `_PIPELINE_LOCK` because model caches are unlocked (:62-69). No CLI equivalent (`cli.py` has no scanlate references).

Data model:
- `pages(id, drama_id, idx, filename, rendered_filename, width, height)` `db.py:265`; CRUD `create_page` :1691, `update_page` :1701 (interpolates kwarg keys: whitelist), `list_pages` :1710. No `get_page`, no page delete or reorder.
- `bubbles(id, page_id, idx, x,y,w,h, source_text, translated_text, font_size, skip, font_category, kind, kind_confidence, confidence, language, orientation, panel_id, include_sfx)` `db.py:276` plus ALTERs :945-967. Reading order is `idx` (list position).
- Files under `library/dramas/<id>/`: `pages/page_NNNN.ext` (original), `pages/typeset_NNNN.png` (render output, overwritten in place; the brush also writes it, tab :379-393), `pages/font_styles_NNNN.json`, `pages/typeset_pages.zip|pdf`, `fonts/<category>.ttf` (tab :327-345).
- **Not stored anywhere:** cleanup/brush masks (applied straight to the typeset PNG); per-page rolling translation context (`st.session_state` only, tab :140-144; page_server keeps an in-memory dict); per-bubble text colour/align/font path (`render_text_in_box` accepts them, `process_page` never passes them); batch run notes; region history.
- `JobRecord` has only status/progress/message/error (`api/schemas.py:156`, `db.py:733`), so job outputs must land in the DB or files.

## 2. Proposed slices (serial; one implementer session each)

Service modules: `scanlate_pages_service` (S1, S2, S6 fonts), `scanlate_regions_service` (S3, S4), `scanlate_run_service` (S5, S8), `scanlate_render_service` (S6, S7). Routers under `/api/scanlate/dramas/{drama_id}`. Errors: 404 NotFound, 422 InvalidInput, 400 Unsupported, 409 Conflict, 503 DependencyUnavailable.

- **S0, stable bubble ids (db-only, sole owner of `db.py`).** `save_bubbles` deletes and re-inserts so ids change every save (`db.py:1726-1737`; the tab admits it at :314). Add without changing `save_bubbles`: `get_page`, `update_bubble_fields(id, fields, expected)` as one conditional UPDATE (mirrors `db.update_line_fields_if`), `insert_bubble`, `delete_bubble`, `reorder_bubbles(page_id, ordered_ids)`, `pages.rev` (bumped on each region write), `pages.context_summary`, `pages.run_notes` (JSON, redacted), via `_safe_alter` in `init_db`.
- **S1, read + serve (sync GET).** `GET .../config` (engines with `key_configured` booleans, `ml_weights_cached`, `lama_weights_cached`, OCR backends, kinds, font categories, upload caps); `GET .../pages`; `GET .../pages/{pid}` (page plus regions keyed by `id`, and `rev`); `GET .../pages/{pid}/image?variant=original|rendered|thumb`. 404 for unknown drama, page, or a page of another drama (`db.load_bubbles` :1747 is not drama-scoped).
- **S2, import (sync multipart POST).** `POST .../pages` with `files[]`, `slice_strips`. Whitelist png/jpg/jpeg/pdf; stream to a temp file; cap per file, per request and pixels. 409 while a `scanlate_` job runs. Allocate idx as MAX(idx)+1 under a per-drama lock (both existing copies use `len(list_pages)`: tab :605, `pipeline.py:45`). 422 for type, size, empty or corrupt; 503 if pypdf is missing.
- **S3, region writes (POST, by region id).** Add region (an INSERT, not a list rebuild); patch fields with `expected` per field (409 on stale, as in `lines_service.patch_line`); delete; reorder (`ordered_ids`, 409 if the id set differs); find/replace preview (read-only) and apply (`[{region_id, expected_old_text}]`, one conditional UPDATE each, returns applied and stale counts). Bounds: inside the page, minimum size, text cap, enums. Responses include the new page `rev`.
- **S4, manual-region OCR (sync POST).** `POST .../pages/{pid}/ocr-region {x,y,w,h,backend?}` returns `{text, language}`, writes nothing. 422 out of bounds; 503 backend not installed. First model load may take minutes (HYP): hold the pipeline lock; "job instead" is an open item.
- **S5, detect + translate jobs (the only slice that edits `scanlate.py` and `background_jobs.py`).** `POST .../pages/{pid}/detect`, `.../pages/{pid}/translate`, `.../batch`, each returning `{job_id}`. Job id `scanlate_{drama_id}` (one per drama); add `"scanlate_"` to `DRAMA_JOB_PREFIXES` (`background_jobs.py:730`), which also guards drama delete and upload. The job does everything: engine and key resolved server-side (pattern: `narration_service._api_key`), `db.log_usage(... "scanlate_translate")` as tab :125, per-page context persisted to `pages.context_summary` and read from the predecessor page (Step 25d-12), redacted notes to `pages.run_notes`, progress per page, cancel check between pages (`batch_process_pages` has none at :1902, so loop per page in the service). `gpu_touching=True` like `start_ocr_chapter` (`novel_attach_service.py:287`). 409 if a job is running or the page has regions without `confirm`; 503 no key or dependency; 422 unknown backend.
- **S6, render (job) + fonts.** `POST .../pages/{pid}/render` reads regions from the DB (never the request body) and writes `rendered_filename`; parity: blank text untouched (:1091-1095), SFX skipped (:1587). `GET|POST .../fonts` (multipart .ttf/.otf, small cap, validated by loading with Pillow; booleans only, never paths); `POST .../fonts/{category}/delete`. Explicit-save semantics, per-drama scoping (tab :337-342).
- **S7, brush/cleanup masks (hardest; needs decision Q3).** `POST .../pages/{pid}/erase` (multipart PNG mask, same size as the image, size-capped), persisted as a layer file, then re-render. Compose order: original, auto per-region inpaint, stored brush masks, text. Needs `process_page(..., extra_mask=)`; today render restarts from the original (:1098) and discards brush work applied to the typeset file (tab :384). 422 on mask shape mismatch (:702).
- **S8, bulk render/export job.** `POST .../export {pages?, formats:[zip,pdf]}` returns `{job_id}`. Outputs via `artifact_service.output_path` (`artifact_service.py:49`); add kinds (`scanlate_zip`, `scanlate_pdf`) to `ARTIFACT_KINDS` (:22); download via the existing `GET /api/artifacts/dramas/{id}/{kind}`. Per-page failures go to run notes. Today's bulk path writes fixed names into `pages/` (:1052; tab :542) and does not update `rendered_filename`.

## 3. Image and binary serving

- Page-image endpoint: `FileResponse` precedents `artifact_routes.py:35`, `dub_routes.py:49`. Starlette normally supplies ETag, Last-Modified and Range (HYP: verify the installed version).
- Never accept a path. Resolve `pages.filename` / `rendered_filename` relative to the drama dir, apply the `artifact_service._inside` check (private: copy or expose it), reject symlinks, whitelist the extension for Content-Type, add `X-Content-Type-Options: nosniff`.
- The rendered file is overwritten at the same name, so return `image_version` (mtime) in JSON and use `Cache-Control: no-cache` plus ETag.
- Thumbnails generated on demand and cached under `pages/thumbs/`. Webtoon strips can be 10k+ px tall (:1163) and there is no tiling; whether a tile or crop endpoint is needed is UNKNOWN.
- Brush masks served only as a PNG variant; never expose their path.
- Upload limits: per-file bytes, per-request count and bytes, a pixel cap (Pillow's default is only a warning, HYP), PDF page and image caps (PDF bombs plausible, HYP). Reuse `BAIHE_MAX_UPLOAD_MB` (`media_upload_service.py:37`) or add a smaller image cap; temp file plus atomic rename as `media_upload_service.py:68-83`; never store the client filename.
- Error messages must not echo paths: `ValueError(f"Could not read image: {image_path}")` at `scanlate.py:62, :1138, :1182, :1231` leaks an absolute path.

## 4. Invariants that apply, with the specific risk

- **id-keyed matching.** `translate_page_bubbles` assigns by position, guarded only by a length check (:1871); the prompt is a numbered list and the response a positional `translations` array (:990, :1021). A reordered same-length answer silently misassigns. The pure-MT path (:984) is positional too. Fix in S5 with an id-keyed prompt/response (pattern `_parse_id_keyed_json`, `translate_engines.py:670`); keep the old function until Streamlit and page_server retire. Region ids are themselves unstable today, hence S0 first.
- **Field-scoped writes.** `db.save_bubbles` is a full replace, used by `batch_process_pages` (:1915; tab :202), single detect (tab :168) and page_server (:363). "Skip existing" is computed before the run (tab :185-188), so a batch can wipe a page the user edits mid-run. API jobs must write conditionally: replace regions only if the page's region-id set is unchanged; translate via `UPDATE ... SET translated_text WHERE id=? AND source_text=?`. `db.update_bubble_text` (:1767) is field-scoped but unconditional; `db.update_page` (:1706) interpolates keys, so whitelist them. One job id per drama.
- **Timeouts.** `scanlate.py` has no `requests` calls, and `translate_engines` is covered by `tests/test_static_analysis.py:479-512`. Hub downloads (`from_pretrained` :195-197, `hf_hub_download` :553) have no explicit timeout, so a stalled download can hold a job "running": mitigate with a cancel check and a message; do not claim timeout coverage.
- **Secrets and paths.** Exception text is embedded verbatim in user-facing notes (`BubbleModelUnavailable` :385, `InpaintModelUnavailable` :657, batch error `f"{type(exc).__name__}: {exc}"` :1920). Route every returned or stored note through `translate_engines.redact_secrets` (:274) plus path stripping. The HF token is resolved server-side via `settings_service.resolve_key("hf_token")` (`settings_service.py:36`), never a request field (note `os.environ.setdefault("HF_TOKEN")` at :189-191). Do not accept a client `tesseract_cmd` path (`transcribe_service.py:257` does; that is an arbitrary-executable input).
- **Other risks.** Unlocked model caches (`scanlate.py:193-199, :541-569`; `ocr.py:96, :138, :163`) race between threadpool requests and jobs: the API needs its own module-level pipeline lock (page_server's does not cover the API process). `process_page` uses a shared `out_path + ".tmp.png"` with no try/finally (:1097-1116). `inpaint_region` never checks `imread` for None (:629-630). Whole-image re-read and re-write per region (`sample_text_style` :1341, `bubble_shape_mask` :1745, `inpaint_region` :629/:666/:1102) is O(regions x image) on strips. `max_tokens=1500` (:1010) can truncate JSON on busy pages (safe but silent). No spend cap on Scanlate translation (contrast `translate_run_service.py:58-66`; Q4). HYP: `cv2.imread` honours EXIF orientation but PIL `im.size` (tab :614, `pipeline.py:60`) does not, so stored size and detected coordinates may disagree on rotated JPEGs. HYP: the ML detector and LaMa run on CPU (:204, :583-599), so `gpu_touching` may be over-conservative; neither has been verified end to end (:171-178, :410-420).

## 5. What the React canvas editor needs beyond these endpoints

- Coordinates are pixels in original image space; the client scales and sends integers.
- Undo: the server keeps no bubble history. Recommend a client-side undo stack where each inverse is a PATCH carrying `expected`; the server returns `rev` per page and an optional `expected_rev` guards multi-region ops.
- Missing server-side and needing a decision: merge and split regions, polygon or mask shapes (only boxes are stored; shape recomputed at render :1734), per-region text colour, align, stroke and rotation.
- WYSIWYG: browser text layout will not match Pillow's `_layout_in_mask` (:836). Options: a server `render-region-preview` PNG endpoint (hard, new) or approximate on-canvas text with a server-rendered final. UNKNOWN.
- Brush: how the mask travels (full-res PNG vs strokes), size cap and composition semantics (S7). UNKNOWN.
- Job polling (`GET /api/jobs/{id}`, `useJob`) is enough for S1-S8; refetch pages after done. SSE is not needed here.
- Large strips: zoom, tiling and thumbnails UNKNOWN; render latency unmeasured.
- Coexistence: Streamlit's `save_bubbles` and page_server keep changing ids while the editor is open, so the editor must treat 404 and 409 as "reload the page".
- `frontend/package.json` has no canvas library yet.

## 6. Test plan (mocked; no GPU or network; `isolated_db`; synthetic pages as `tests/test_scanlate.py:22`)

- S0: id stability across insert/update/delete/reorder; conditional update false on stale; ALTER idempotent.
- S1: ownership 404; image 200/304/Range; traversal and symlink rejected; no path in any response.
- S2: type, size and pixel-cap rejection; PDF via `pytest.importorskip("pypdf")`; slice_strips; concurrent adds get unique idx; 409 while a job runs.
- S3: patch with stale `expected` gives 409 and writes nothing; delete leaves other ids unchanged; find/replace apply skips stale rows (Class S 25o).
- S4: bounds 422; backend missing 503; nothing written.
- S5: reordered, short or unparseable LLM answers apply nothing (Class S 25n, `tests/test_scanlate_regions.py:205-247`); mid-batch user edit survives; context from the predecessor page (Class S 25d-12; `tests/test_scanlate_tab.py:306`); SFX not sent by default (`tests/test_scanlate_regions.py:421`); token-bearing exception redacted; cancel between pages; a `scanlate_` job blocks drama delete.
- S6: blank text untouched (`tests/test_scanlate.py:304`); shape mask applied (`tests/test_scanlate_regions.py:249`); concurrent render does not collide on the tmp file; font cap and invalid font rejected; explicit save only (Class U+S 25p; `tests/test_scanlate_tab.py:217`).
- S7: brush survives re-render; mask shape mismatch 422. S8: one failing page does not stop the rest; artifact download works; kinds whitelist.
- Class U (React/e2e): drama-switch isolation (4j/25j), refetch after find/replace or job done, per-drama upload only on explicit submit.
- `docs/migration-review.md` §4 has no Scanlate-specific rows beyond the §3.4 mentions of 25n/25o/25p. Missing rows to add: SFX default, dedupe, blank-text, per-page context.
- Real-model, real-OCR and real-LaMa runs stay owed to the user.

## 7. Open questions for the user (ranked)

1. Coexistence: keep Streamlit's `save_bubbles` full replace (ids change) and page_server writing the same tables while the editor is live, or freeze Scanlate writes there once the editor ships? This decides S0's shape.
2. Text preview: server-rendered per-region preview (exact, slower, new endpoint) vs approximate client-side text?
3. Persist brush and cleanup masks as durable layers? This changes render behaviour (today render discards them).
4. Apply the monthly spend cap or a per-run cost cap to Scanlate translate and batch (none today)?
5. Detect overwrite policy when a page already has regions: refuse, or replace with a typed confirm? Should batch `skip_existing` default to true?
6. Page delete and reorder (unsupported today; idx comes from `len()`): in scope?
7. New persistence (`pages.rev`, `pages.context_summary`, `pages.run_notes`): acceptable?
8. One `scanlate_{drama_id}` job at a time, or per-page jobs? Mark OCR/ML jobs `gpu_touching` given they may be CPU-only?
9. Upload caps (per file, megapixels, PDF) and whether to accept webp (sources convert it; the tab does not).
10. Should the drama's `content_mode`/`media_type` (manhua, `drama_service.py:51`) gate Scanlate endpoints? Today any drama works (tab :17-22).
11. Tesseract command and default OCR backend live only in Streamlit session settings: need a server-side home, or the API documents "auto" and no tesseract path.
12. EXIF rotation handling and maximum strip height (HYP items above).

## 8. Decisions (user, 2026-09-29)

- **Q1 Coexistence:** Streamlit and the browser-extension bridge (`page_server.py`) may be frozen from writing bubbles once the editor ships (the user is not using Streamlit now). S0 can therefore assume the API is the only bubble writer at that point: keep S0 additive as specced (no change to `save_bubbles` until the freeze), and make the freeze itself an explicit later step that turns those two write paths off or read-only.
- Still open: Q2 to Q12 (text preview approach, durable brush masks, Scanlate spend cap, detect overwrite policy, page delete/reorder, new columns, job semantics, upload caps, content-type gating, tesseract/OCR defaults, EXIF/strip height).
