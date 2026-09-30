# Releases

## Windows installer (end users)

Installed users get the React screens inside the installer, so they don't
need the zip below (user decision, 2026-09-30). To build
`BaiheStudio-Setup-<version>.exe`, go to Actions → **Windows Installer** →
Run workflow, or push a tag `installer-v<version>`. The workflow builds the
installer, smoke-tests a silent install and uninstall, and uploads the
`.exe` as a run artifact. Attach it to a GitHub release by hand. To build
locally on Windows (Python 3.12, Node.js 22 and Inno Setup 6 needed), run
`python installer/build_installer.py --version <version>`. Design and
behaviour: `docs/windows-installer-design.md`.

# Frontend release zip (source checkouts)

End users run the app without Node.js. The React frontend is built once, on
a developer's PC, and published as a zip on a GitHub release. GitHub Actions
minutes are exhausted, so CI does not build it.

## Build it (developer PC, needs Node.js 22)

From the repo root:

```
python scripts/build_release.py --build
```

This runs `npm ci` and `npm run build` in `frontend/`, then writes
`dist/baihe-frontend-<version>.zip`. The version comes from
`frontend/package.json`, so bump `"version"` there before a new release.
If `frontend/dist` is already built, leave out `--build` and the script
just zips it. Every entry in the zip is under `frontend/dist/`.

## Publish it

On GitHub, go to Releases, then "Draft a new release". Tag it (for example
`frontend-v<version>`), attach `dist/baihe-frontend-<version>.zip`, and
publish. Or, with the GitHub CLI:

```
gh release create frontend-v<version> dist/baihe-frontend-<version>.zip --title "Frontend <version>"
```

## Install it (user PC)

Download the zip and extract it into the Baihe folder (the folder that
contains `start.bat`). Afterwards this file should exist:

```
<Baihe folder>\frontend\dist\index.html
```

`start.bat` checks for that file. If it's missing, the launcher stops and
says how to get it. `python -m api` serves whatever is in `frontend/dist`
at `/` (`api/static_frontend.py`). To update, delete `frontend\dist` and
extract the newer zip.

Developers can skip the zip. `start.bat --build-frontend` (or
`.\start.ps1 -BuildFrontend`) builds `frontend\dist` with npm if it's
missing.
