# Content sources: credential handling audit (Step 110)

Audited 2026-09-30 against `baihe-subtitler` @ f09a592. Scope: the content-source layer (`sources/`, `page_fetch.py`'s profile functions, `services/sources_*.py`, `api/routers/sources_*.py`) and the yt-dlp cookie setting. The check was whether any adapter stores an account name, password, session cookie or token anywhere other than the browser profile the person opted into, and whether anything logs them.

**Result:** no adapter stores or logs an account name, password or cookie outside the opted-in browser profile. There are three follow-ups, none about cookies or passwords (see "Gaps" below). The guard is `tests/test_sources_credential_audit.py`.

## What holds

| Area | Finding | Evidence |
|---|---|---|
| Signed-in sessions | The person signs in themselves in a visible window on a persistent Chromium profile (`profiles/<source or host>/`). The app never types a password and never reads cookies or storage state; only the rendered page comes back. | `sources/auth_browser.py` module doc and `manual_login`; `page_fetch.fetch_with_profile` / `open_login_window`; `sources/base.py` `login` / `refresh_session` (no credentials in, no session refresh) |
| Sign-in routes | `local_only()`. Job results are scrubbed, and a result reaches only a request from this PC. | `api/routers/sources_local_routes.py`; `services/sources_signin_service.py` |
| Profiles vs backups | Profiles are never in a backup, and a restore keeps the current ones. | `services/library_admin_service._backup_excluded_top_level` → `workspace_job_service._restore_kept_names` |
| Response cookies | `Response.cookies` is read in two places only. `_requests_transport` builds it from each hop's own jar. `mangaz._fetch_ticket` read `virgo!__ticket` and returned it without storing it (the Mangaz adapter was removed after this audit). The per-thread `requests` session jar lives only in memory. | `sources/http.py` |
| Fixed cookie headers | mangaz `_LANG_=ja`, manhuagui `isAdult=1` (only when the person turned the adult toggle on), xbanxia `jieqiUserCharset=utf-8`. These are fixed site preferences, not credentials. | the adapters (Mangaz's was removed after this audit) |
| Attempt records | Request headers never enter an `AttemptRecord`. `detect.evidence` keeps an allowlist of response headers (server, cf-mitigated, cf-ray, content-type, location, retry-after, x-cache, via), with no `Set-Cookie`. Cookie and Authorization are stripped on a cross-host redirect. | `sources/detect.py` `evidence`; `sources/http.py` `_should_strip_auth` |
| Raw cache | Stores the body only, keyed by URL. | `sources/cache.py` |
| yt-dlp cookies | Settings store a browser name or a cookies.txt path, never cookie contents. The path is blanked for any caller that is not the PC, and writes are `local_only()`. A Live capture started from another device never gets them. The URL-media download always passes them, but its route is `local_only()`. | `services/settings_service.py` (`cookies_browser`, `cookies_file`, `get_cookie_settings`); `api/routers/settings_routes._with_path_flags`; `services/url_media_service._download` + `api/routers/media_routes.py`; `docs/remote-access-decision.md` |
| API reads | Health, attempts, capabilities and tracked series are scrubbed (`redact_secrets`, URLs reduced to scheme+host+path) before they leave the service. | `services/sources_registry_service._scrub` / `_scrub_any` |

## Gaps (follow-ups for the planning session, not fixed here)

- **G1: URL tokens in sources.db (low).** A signed URL's query string (`?token=`, `?sig=`) is stored unredacted in these places:
  - `source_health.last_error`, from `http.py`'s "Challenge at {url}", `attempt.describe()` and the mirror-failure text;
  - `access_attempts`, via `store.log_attempt`;
  - `source_capabilities`, where a tier's `detail` holds the challenge message with the URL;
  - `tracked_series.url` and `last_check_error`;
  - `cache_index.url`;
  - `extraction_cache.url`;
  - `seen_images.chapter_url`.
  - `chapter_poll_validators.url` (Step 106, once it lands): the chapter-list URL, query included.

  `translate_engines.redact_secrets` would not catch these parameter names anyway. The stronger `_SENSITIVE_PARAM` exists only in `sources/ai_extract.py`. These tokens are not account credentials and never reach a remote client, because API reads are scrubbed. They are still on disk and in library backups. The `xfail(strict=True)` test `test_url_tokens_are_not_stored` pins health, attempts, capabilities and the cache index. Fix idea: one shared URL scrubber at the write sites, and the cache keyed by a hash of the URL.

  Related, and latent: a transport exception's text is stored as `f"{type(exc).__name__}: {exc}"` without `redact_secrets` (`http.py`, unlike `ladder._browser_outcome`). No adapter sends a variable credential header today.
- **G2: proxy credentials stored in plain text (low, by design today).** `set_proxy_url` accepts `http://user:pass@host:port`, and the SO18 test does this on purpose. The value is kept in the sources.db `settings` table, which is included in backups. It is PC-only to set and never echoed. Whether to reject userinfo, or move it to `.env`, is a product decision.
- **G3: yt-dlp `raw_metadata` (latent).** `BilibiliSource.download` returns yt-dlp's whole info dict as `raw_metadata`. Recent yt-dlp versions can put a `cookies` field in it. `front_door.import_video`, its only caller, was removed unused, so nothing reads it today. A future caller that persists it should strip `cookies` and `http_headers` first.
- **Also noted:** yt-dlp writes its cookie jar back to the person's own cookies.txt when it runs with `cookiefile`. That is the person's own file. `video_download` error text can include that file's path, but never its contents. Not checked: yt-dlp may print a malformed cookies.txt line to stderr (unverified hypothesis). Where stderr is logged, that line could appear in the log.

## Tests (`tests/test_sources_credential_audit.py`)

- **Static:** these checks cover the sources layer, `page_fetch.py` and the API/service consumers of the yt-dlp cookie setting (`video_download`, `live_translate`, the URL-media, Live and settings services and routes):
  - no `.storage_state()`, `.add_cookies()`, `.cookies()` or `storage_state=`, no `document.cookie`/`localStorage`/`sessionStorage` script, and no `open`/`connect`/`copytree` of a profile folder;
  - `.cookies` is read only through the two exact expressions above (a new read fails until it is listed, with a reason, in the test and this note);
  - no store, health, job-result, drama, JSON, file-write or log call is passed headers, cookies, a cookie-derived `ticket`, a yt-dlp info dict (`raw_metadata`, `result_info`), or `asdict()`/`vars()` of an object.
- **Behavioural:** a client sends Cookie and Authorization headers. The fake server answers with Set-Cookie and cookies, as a 200 (cached), a challenge, a 403 and a 5xx. The requests also run through the ladder and the capability record, and a chapter check fails. None of the values ends up in any sources.db table, the raw-cache files, the client's attempts or stats, the log or stdout/stderr. The sources layer does no logging today, so the log check guards against future logging only.
- **Limits of the static checks:** the sink check matches names, not data flow. A cookie value renamed to an unlisted variable, or passed through `logging.log`, `traceback.print_exc`, `warnings.warn` or an exception's text, is not caught.
- **Not covered:** the real `requests` transport's cookie-jar handling, `get_with_mirrors`, the signed-in browser tier, and the yt-dlp path at run time. The static checks guard these instead.
