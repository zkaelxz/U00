# Windows installer design: cut history

Text removed from [`../windows-installer-design.md`](../windows-installer-design.md) because it was dated narrative, provenance or a stale status rather than current behaviour. Each block is the original text, verbatim, under the part of the doc it came from. Some of it is also wrong now (noted per block).

## Header (dates and provenance)

> # Windows installer/uninstaller — design and as-built reference
>
> Design and as-built reference (the installer was built 2026-09-30).
> `python -m api` serves the API and the prebuilt React screens from one
> process on `http://127.0.0.1:8600/`. Read it with
> [`archive/windows-installer-research-notes.md`](archive/windows-installer-research-notes.md),
> whose 2026-09-28 decisions are folded in below.

## Header (verification status)

> **Not verified on a real Windows PC yet.** The script compiles with Inno
> Setup 6.7.3, and the payload and runtime scripts are tested on Linux. The
> Windows CI smoke test (silent install, start, `/api/health`, silent
> uninstall) runs only on demand, and the user's first real install is still
> owed (see §9).

## §1 decision date

> - **Inno Setup + bundled Python, not a frozen executable** (user-confirmed
>   2026-09-28).

## §1 decision date

> - **The React screens ship inside the installer** (user, 2026-09-30). `frontend/dist` is

## §1 cross-reference

> - **Heavier components stay opt-in** (research notes, decision 2): the installer

## §6 stale package list

> | **Basic**: `requirements-core.txt` (FastAPI/uvicorn, requests, anthropic, pandas, …; `installer/wheels.lock.txt` still pins `streamlit==1.64.0`, which `requirements-core.txt` no longer lists) | The installer, offline, from bundled wheels |

## §8 removed engine

> `extension_token.txt`, Piper voices), `.env`

## §9 'still owed' narrative

> - Still owed, from a person: a real install on the user's PC (steps in the PR),
>   the interactive wizard and uninstall dialog, SmartScreen, Edge app window, and
>   the research notes' clean-Windows GPU matrix once a GPU tier is added through
>   Diagnostics.

## §10 planning narrative

> - `start.bat`/`start.ps1` still tell a source-checkout user without
>   `frontend\dist` to download the frontend zip from Releases. Now that installed
>   users get the screens inside the installer (2026-09-30), whether that zip
>   keeps being published for source checkouts is a planning decision (the
>   alternative is `start.bat --build-frontend`).

## §11 owner-decision date

> Owner decision (2026-09-30): the app runs as a **Windows service started at
> boot**, installed and removed by this installer, tested by the Windows
> Installer workflow. Code:

## §11 owner-decision date

> somewhere only administrators can write. **Owner decision (2026-09-30):
>   accepted for now; revisit if the PC gets other users.**

## §11 owner-decision date

> by default). Per-account sign-in for this listener is not done. **Owner
>   decision (2026-09-30): accepted on the condition that only the owner uses
>   this PC. If it has other Windows accounts, untick the service task (it is
>   ticked by default for now).**
