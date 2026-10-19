# Releases

## Beta until the owner ends it (owner decision, 2026-10-11)

Baihe is in beta. Tag versions `0.x.y-beta.N` (for example `v0.1.0-beta.1`): a
version with a `-` is published as a GitHub pre-release and sorts below the
final release (`0.1.0-beta.2` is newer than `0.1.0-beta.1`, and `0.1.0` is
newer than any `0.1.0-beta.N`). Settings → App updates ignores pre-releases by
default. A beta install gets the next beta by turning on Developer Mode and
then "Include pre-releases in app updates" (System → Developer Mode): checks
then offer a pre-release only if it sorts above the installed version, and the
download is still checked against its `.sha256`. Drafts are never offered.
Otherwise update by running the newer Setup `.exe` by hand and checking it
against its `.sha256`.

Stable (a plain `v0.x.y` tag) is released only when the owner says so. There is no checklist or date that triggers it.

Beta limits, not promised for stable: the browser-tier sources listed under "Known limits" in `docs/STATUS.md` are unverified against the live sites, and word timings are not restored by undo.

## Windows installer (end users)

Installed users get the React screens inside the installer, so they don't
need the zip below (user decision, 2026-09-30). To build
`BaiheStudio-Setup-<version>.exe`, go to Actions → **Windows Installer** →
Run workflow, or push a tag `installer-v<version>`. The workflow builds the
installer, smoke-tests a silent install and uninstall, and uploads the
`.exe` as a run artifact. A `v<version>` tag also publishes it: once the
smoke tests pass, the workflow attaches the `.exe` and its `.sha256` to a
GitHub release named after the tag, which the app's Settings → App updates
finds (docs/runbook.md §1). Keep `frontend-v*` releases separate; the app
skips any release without the installer. To build
locally on Windows (Python 3.12, Node.js 22 and Inno Setup 6 needed), run
`python installer/build_installer.py --version <version>`. Design and
behaviour: `docs/windows-installer-design.md`.
