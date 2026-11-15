# Releases

## Stable releases (owner decision, 2026-11-10)

Baihe no longer ships betas. Tag plain versions `v0.x.y` (for example
`v0.1.1`): the workflow publishes them as a normal GitHub release, and
Settings → App updates offers them without "Include pre-releases". A tag with
a `-` (`v0.1.1-beta.2`) is still published as a pre-release and sorts below
its final version, so installs that ran a beta (the V100 PC was on
`0.1.1-beta.2`) update to `0.1.1` through the normal check. Drafts are never
offered. The "Include pre-releases" switch stays for anyone who wants a
pre-release by hand.

A release is tagged when the owner says so; no checklist or date triggers it.

Known limits that still apply:
- The installer is not Authenticode code-signed, so Windows SmartScreen warns
  on first run. The `.sig` files are release signatures for the in-app update
  check only.
- Browser-tier sources are unverified against the live sites, and the
  Bilibili Manga reader is untested.

## Windows installer (end users)

Installed users get the React screens inside the installer (user decision,
2026-09-30). To build
`BaiheStudio-Setup-<version>.exe`, go to Actions → **Windows Installer** →
Run workflow, or push a tag `installer-v<version>`. The workflow builds the
installer, smoke-tests a silent install and uninstall, and uploads the
`.exe` as a run artifact. A `v<version>` tag also publishes it: once the
smoke tests pass, the workflow attaches the `.exe` and its `.sha256` to a
GitHub release named after the tag, which the app's Settings → App updates
finds (docs/runbook.md §1). Keep `frontend-v*` releases separate; the app
skips any release without the installer. To build
locally on Windows (Python 3.13, Node.js 22 and Inno Setup 6 needed), run
`python installer/build_installer.py --version <version>`. Design and
behaviour: `docs/windows-installer-design.md`.
