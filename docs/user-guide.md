# User guide

The feature walkthroughs, setup details and troubleshooting that used to sit in the root `README.md`. The README keeps install and start; this page keeps everything else a user needs. Menu paths and button labels were checked against `frontend/src` when this page was split out; the UI changes often, so if a label here is off, the UI wins.

Related docs: [`STATUS.md`](STATUS.md) (what is built and what is next), [`engine-backends.md`](engine-backends.md) (translation, transcription and dubbing engines), [`asr-experiments.md`](asr-experiments.md) (Whisper model choice), [`browser-extension.md`](browser-extension.md), [`household-access.md`](household-access.md), [`runbook.md`](runbook.md) (restore, certificate), [`technical-notes.md`](technical-notes.md) (bugs found and fixed).

## Start here

Baihe Studio turns Chinese, Japanese and Korean audio dramas, novels, comics and videos into reviewed English subtitles, and keeps every title in one library on your PC.

1. **Add a key or pick a local model.** Open Settings and add a translation key, or choose a free local model (Ollama).
2. **Add a title.** Use New title in the Library, or Make subtitles for a single file (it transcribes with Whisper, translates and gives you the SRT; if a step fails it stops there and says why; a file of the wrong type, or an empty one, is refused before any title is made).
3. **Transcribe.** Open the title and run Transcribe on its audio or video.
4. **Translate.** Run Translate, then fix lines in Review.
5. **Export.** Download SRT, VTT, ASS or LRC from Export.

Quick search is Ctrl+K (or ⌘+K), and inside a title it also finds that title's lines and opens Review on them; "/" searches the page you are on and "?" lists every key. Backups are on Library tools > Backup, and settings are under Settings. If something fails, Diagnostics checks your setup. Use Report a problem in the app if you get stuck, and read on for the details.

## What the app is

- **Folds.** A heading with a small triangle before it opens and closes when clicked; a closed one shows its current value in one line.
- **Pages.** Library, Library tools (with Saved manga), Make subtitles, Quick translate, Sources, Discover, Live, Jobs, Settings, People and access, Diagnostics, and Benchmark Lab and Assistant (both Developer Mode) in the nav, plus each drama's Workspace stages, the Reader and Comic (`frontend/src/pages/`). In a title's stage tabs the underlined, bold tab is the open one; a ✓ marks a finished stage and the blue dot the title's next step.
- **Sources that need the browser** (Fanjiao, Manhuaku, Bilibili Manga) can only be read by a real browser render. Until the playwright package and a browser (Chrome, Edge or Playwright's Chromium) are found, their Search and chapter Import stay off and the install notice from Diagnostics > Packages is shown. The browser only runs for requests from the PC: from another device, importing or saving their chapters says to open it at the PC.
- **Access.** `python -m api` serves the built `frontend/dist` at `/`. The PC's own port is loopback-only and needs no login; other household devices can use a separate listener with Google sign-in, which is opt-in ([`household-access.md`](household-access.md)). The **Activity log** and **People** are on the People and access page. In People, each active member account has a **Rights** row with one checkbox per permission (`library.read`, `media.import_url`, `engines.paid` and so on): an admin ticks or clears one to give or take it, and each change goes in the Activity log. Admin accounts hold every right and have no checkboxes. Each right shows a plain name with its permission name under it, and its **i** says what it allows. Signed in as an admin from away (the household address included), People's buttons and Rights work on member accounts; admin accounts, Settings, Diagnostics and Library tools stay at the PC.
- **Background services.** `python -m api` also starts the scheduled chapter check and other schedulers (`api/background.py`), and the browser-extension bridge (`page_server.py`) when the extension setting is on.
- **Three content modes**: audio drama (your audio or video plus a transcript, aligned to real timing), novel narration (paste the text; the app chunks it, tags speakers with the LLM, translates, and can generate a narration/dub) and streamer VOD (see [Streamer VODs and series](user-guide-subtitles.md#streamer-vods-and-series)).
- **Video input**: upload `.mp4`/`.mov`/`.mkv`/`.webm` or download from a URL via yt-dlp (a download in another container, such as `.ogv`, is copied into `.mkv` without re-encoding); the audio is extracted for alignment and the video kept for export.
- **Several downloads from links**: up to 3 run at once, each starting at least 20 seconds after the last. Press Download on more titles and they wait in line ("Queued: 2nd in line" on that title and in Jobs); Cancel works while waiting. You can leave the page. If Baihe restarts, waiting downloads are gone and the Jobs page shows them as interrupted.
- **Why a link was refused**: the message says which rule applied: still live, an upcoming stream or premiere, a playlist or channel, or "This video is 7 h 42 m; the limit is 6 h".
- **Longer than 6 hours**: **Download anyway** raises the limit to 12 hours for that one download. The message shows the disk space it needs, and the free space is checked before it starts. It is not a setting.
- **Only this part**: fill **From** and **To** (h:mm:ss) to download just that section. The default is a fast copy that starts at the nearest keyframe; **Exact cuts** re-encodes the part so it starts exactly where you chose, which takes about as long as the part itself. Subtitle times for a part start at 0:00 for the clip, so add the offset yourself if you need the original times. Not for direct file links, and not while cookie login is on in Settings (the part is fetched by ffmpeg, which would see the cookies on its command line).
- **Why a download failed**: a private video names itself as private (not as a sign-in prompt), and so do age-restricted and members-only videos. A direct file link names a missing file (404), a refusal or rate limit, and a link that opens a web page instead of a file (a share page). A missing ffmpeg is named, for a link and for an uploaded video.
- **"Your network or a DNS filter (for example Pi-hole) is blocking YouTube's video servers"**: the download could not look up `googlevideo.com`. Allow that domain in your DNS filter or network and try again.
- **Phones and large text.** Pages fit a 320 px wide screen and a phone with its text size turned up without scrolling sideways; a stage tab name too long for its tab ends in "…". With a phone held sideways the title and stage tabs scroll away with the page instead of staying pinned. The Reader and Comic name the title as the page heading, so the browser tab shows it.
- **Home screen and Share (Android Chrome).** Open Baihe at your household address and choose Install app or Add to Home screen; it then opens full screen. In the installed app, Share on a comic or novel page in Chrome offers Baihe, which opens Sources > Find with the link already in the Paste a link box; nothing is fetched until you press Preview. The shared link is never imported by itself and no file is uploaded. Sign in first if the app asks; the shared link is not kept through sign-in.
- **Screen readers.** Each page's headings go one level at a time (page title, then its sections, then their parts), so jumping by heading level never skips a level.
- **Line tools.** A line's actions sheet in Review has "Alternatives (AI)" and "Grammar breakdown (AI)" for study.

## Running the app

Double-click `start.bat`, or from the repo root inside the venv (once `frontend/dist` exists):
```bash
python -m api     # then open http://127.0.0.1:8600/
```
Set `BAIHE_API_SERVE_FRONTEND=0` to run API-only.

## The rest of the guide

- [Setup](user-guide-setup.md): installing from source, portable mode, ffmpeg and updates, developer setup and ports, resetting for testing.
- [Subtitles](user-guide-subtitles.md): translation, transcription and OCR, dubbing and voice cloning.
- [Reader and library](user-guide-library.md): the Reader, the Library, Discover, Manhua and Scanlate, metadata and site tools.
- [Running and fixing it](user-guide-operations.md): the job queue, reliability, troubleshooting, Diagnostics, costs and the Benchmark Lab.
