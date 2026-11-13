# Release checklist

The one list to run before a release is called good. It replaces the old owner test checklist, the "Known limits" prose in each release note and anything kept only in memory. Tick a flow only after you ran it on a real PC; if you skip one, write "skipped" and say why in the release note.

Run order is cheapest and most likely to fail first. Flows 1 to 4 are the installer and update path and must pass for every release. Flows 5 to 12 only need a re-run when the release touched that area (the release note says so).

## Before the tag (not a flow, a gate)
- CI is green at the exact commit to be tagged (the Tests workflow on that commit, not an older one).
- `release/vX.Y.Z-notes.md` exists. Its "Known limits" lists only what this release changed and was not run for real; older limits live here in the flows, not in the note.
- If anything under `extension/` changed, `extension/manifest.json` has a new version.
- The tag needs the owner's typed yes.

## The flows

1. **Install on a clean PC.** Run `BaiheStudio-Setup-<version>.exe`. First check its SHA-256 against the release's `.sha256` (`Get-FileHash <file> -Algorithm SHA256`). SmartScreen warns (not code-signed): expected. Good: Setup finishes, Baihe opens in its own window, Diagnostics shows no red row. Bad: Setup errors, or the window stays blank.
2. **Update from the previous version.** First Library > Library tools > Backup tab > "Back up library". Then Settings > App updates: "Check for updates", "Download", "Install and restart…". Good: your titles are all there, the version reads the new number, and the signature line says the update was checked (not "Not signature-checked"). Bad: the library will not open, or the card says the update was undone.
3. **Update app only, on the service install.** Same card, "Download app update", then "Update app only…". Run it on the PC with the background service. Good: version changes, titles open. Before you press it, wait until Jobs shows nothing running (an update cancels running jobs).
4. **Uninstall.** On a test copy with a library: uninstall from Windows Settings. Good: it names the data folders it leaves behind. "Remove everything" asks a second time and names the data folder before deleting. Never run it on the real library.
5. **Diagnostics and ffmpeg.** Diagnostics > Packages and installs > ffmpeg > "Install ffmpeg" (about 110 MB) on a PC without it. Good: the card turns green. Optionally `python -m tools.self_test` on a source checkout and read the PASS, FAIL, SKIPPED lines (docs/runbook.md 2c); only FAIL matters.
6. **Transcribe one clip.** New title from a short audio or video file, Media stage (Make subtitles in the side menu is the other way in). Good: lines with times appear and the GPU is used. Copy back the model name and run time.
7. **Translate one title.** Translate stage with the local engine (Ollama) on the lines from flow 6. Good: every line has a translation, none shifted onto the wrong line, Stop works within seconds. If the release touched paid engines, repeat with one hosted engine on a tiny title (costs money; set a spend cap first).
8. **Review and export.** Review stage: edit a line, then Export stage: write an SRT (and a video with burned subtitles if exports changed). Good: the file plays with correct timing in your player.
9. **Novel.** One real novel: import, add chapters, translate, rename a chapter, glossary scan, Apply. Good: no chapter repeated or lost; an interrupted add keeps its marker. Resume pending batches on the Translate stage if a bulk batch was used.
10. **Comic.** One real page set: import, translate, "Open in Scanlate", redo a page, press Open folder on the PC itself. Look at balloon text in the real page, not only a mock.
11. **Browser extension.** Unzip the new `extension/` copy over the folder, press Reload at `chrome://extensions`, and check the card reads the version in `extension/manifest.json`. Capture one real page: it lands under the right title with a chapter label.
12. **Phone and sign-in.** From a phone: sign in (real Google sign-in if the release touched it), open a title, and check the app shows one Baihe Studio icon (Android).

## What to copy back
- A tick or "skipped" per flow, the Windows version, the GPU, and for any failure the text of the error and the time it happened. Never paste API keys, tokens or the data folder path.

## Keeping this list short
When a flow stops being able to fail (a test or the release job now catches it), delete it here. Add a flow only when a real release bug got past this list.
