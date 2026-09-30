# Windows installer — further research notes (follow-up to Step 80)

> **Status (2026-09-30):** the installer was built in Step 80b. What was built
> from these decisions, and what was deferred, is in
> [`windows-installer-design.md`](windows-installer-design.md) (sections 1, 6 and 10).

**Discussion document, not a build step.** Spawned as a dedicated
research/discussion session per the user's request (2026-09-28: "Let's
discuss and research this, it can be done in another session"), after the
user separately confirmed `docs/windows-installer-design.md`'s core
recommendation (Inno Setup + bundled Python over PyInstaller/Nuitka) is
correct. This document doesn't change that recommendation. It stress-tests
it against real prior art and names concrete gaps the merged doc didn't
cover, some of which need a decision before an implementation step is
scoped. No code, no roadmap step id — this is exploratory, same spirit as
the merged doc's own "design only" framing.

Read `docs/windows-installer-design.md` first; this document assumes its
§1-§7 and refers back to them by number rather than restating them.

**User decisions (2026-09-28), closing out this document's open
questions:**

1. **Keep Inno Setup + bundled Python for now.** The conda/Miniforge
   alternative (§3) stays closed unless a small prototype installer
   can't reliably install Baihe's actual dependencies with the
   embeddable-Python + pip approach — at which point it's the fallback to
   revisit, not a parallel track to build now.
2. **Add disk-space checks and a CPU fallback to the design** — both
   address real user-facing failure modes (§4, §5) without requiring any
   architecture change, so they're folded into the design as concrete
   requirements rather than left as "a decision to make later."
3. **Run a clean-Windows prototype** to verify the Python/pip bootstrap
   (§2) and one real optional AI backend end-to-end, before committing
   further design effort — this is the biggest unverified technical
   assumption in the whole approach, and evidence beats more research on
   it. See "Next steps" at the end of this document — this research
   session has no Windows environment to run it in itself.
4. **Defer code-signing spend until a public release is actually being
   prepared** — not "never," as this document's first pass over-stated it
   (§8's earlier framing). It matters for distribution, but doesn't need
   to block the installer design now, while distribution is small/private.
5. **Keep the update manifest and the four-way component split** (§9,
   §5 item 5 of the merged doc) — Inno Setup's lack of built-in delta
   updates is something the updater design has to account for by keeping
   these, not a reason to reconsider the framework choice.

Heavier-components tiering (opt-in, prompted, separate from Core — the
earlier 2026-09-28 decision, still standing) is unchanged by this pass and
is folded into item 2 above and the summary below.

---

## 1. Real prior art surveyed

The merged doc's Option A vs. B comparison (§2) reasoned from first
principles about freezing vs. bundled-interpreter tradeoffs. Here's how
actual Python apps with a similar shape — large, sometimes
mutually-exclusive optional ML backends — have shipped on Windows in
practice.

| Project | Packaging | How deps are actually installed | Relevance to Baihe |
|---|---|---|---|
| **GPT-SoVITS** (one of Baihe's *own* optional TTS backends, `requirements-optional.txt`) | 7z archive containing a bundled Python + pre-installed `site-packages` + pretrained models + `go-webui.bat` launcher | Dependencies are pre-installed into the bundled interpreter's `site-packages` at build time by the maintainer, not resolved by the end user's machine; the user downloads a `.7z` from Hugging Face and double-clicks a batch file. No installer framework at all — just an archive. | Directly relevant: this is the exact upstream project Baihe already treats as a "can't share one environment" case in `requirements-optional.txt`'s own comments. Confirms the embeddable-Python-plus-pip shape is a real, working pattern for exactly this kind of app — but also shows a simpler alternative end state (ship pre-populated `site-packages`, skip the post-install `pip install` step entirely for a given tier) that Baihe's installer could adopt for its **Basic** tier specifically, trading a larger download for zero install-time network dependency. |
| **ComfyUI Windows Portable** | `.7z`/zip containing `python_embeded/` (the official Windows embeddable package) + `ComfyUI/` source | Confirms the embeddable package needs explicit `pip`/`site-packages` wiring (§2 below) — its own docs tell users to run `.\python_embeded\python.exe -s -m pip install <pkg>`, with `-s` deliberately skipping the *user* site-packages directory so a package already on the host's real Python installation can't leak in and silently satisfy an import the embedded interpreter should have installed itself. | Same `-s` isolation concern applies to Baihe: an installed copy's bundled interpreter must not pick up whatever the developer's own system-wide Python happens to have, or Diagnostics could report a component as satisfied when it only works on the machine that happened to already have it globally. Worth carrying into §5 item 4 as an explicit flag on every post-install `pip` invocation. |
| **oobabooga/text-generation-webui one-click-installers** | Self-contained **Miniforge/conda** environment created inside `installer_files/`, not embeddable-Python + pip | A bootstrap script downloads Miniforge if absent, creates a conda env in the app's own folder, then `pip`/`conda install`s the actual deps inside it — including hardware-specific variants (CUDA/ROCm) picked per-machine. | The closest real analogue to Baihe's own "conflicting optional backends" problem, solved differently: conda's package solver and binary-package format let it install **non-Python system binaries as first-class packages** (e.g. `ffmpeg`, CUDA runtime libraries) inside the isolated environment, not just Python wheels — see §3. |
| **AUTOMATIC1111/stable-diffusion-webui** | No embedded Python at all (an open feature request, unimplemented as of this research: `AUTOMATIC1111/stable-diffusion-webui#15513`) | Requires the user to install a specific Python version (3.10.6) system-wide and have `git` on PATH; `webui-user.bat` then does a `venv`-based install against whatever `python` resolves to on PATH. | Useful negative example: this is close to what Baihe's *current* `start.bat` does, and is exactly the "resolves `python` from PATH" shape that Step 79 had to patch around the Windows-Store-alias bug. Confirms the merged doc's claim that a bundled interpreter is what actually closes off that whole bug class — a large, well-known project in the same space still hasn't solved it because it never bundled a runtime. |

Sources: [GPT-SoVITS-windows-package (Hugging Face)](https://huggingface.co/lj1995/GPT-SoVITS-windows-package), [RVC-Boss/GPT-SoVITS README](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/README.md?plain=1), [ComfyUI-Windows-Portable](https://github.com/YanWenKun/ComfyUI-Windows-Portable), [ComfyUI portable docs](https://docs.comfy.org/installation/comfyui_portable_windows), [ComfyUI forum: installing pip](https://forum.comfy.org/t/installing-pip-in-comfy-ui/3332), [oobabooga/one-click-installers](https://github.com/oobabooga/one-click-installers), [oobabooga installer_files conda issue](https://github.com/oobabooga/text-generation-webui/issues/1109), [AUTOMATIC1111 embedded-Python feature request](https://github.com/AUTOMATIC1111/stable-diffusion-webui/issues/15513).

---

## 2. Concrete gap in the merged design: the embeddable package doesn't ship pip

The merged doc's §2 Option A description says the installer "runs `pip
install` against that bundled interpreter." In practice this needs one
more step the doc doesn't mention: the official
`python-3.x.y-embed-amd64.zip` **does not include `pip`, and its default
`pythonXX._pth` file actively disables `site-packages` resolution** (it
ships with the `import site` line commented out specifically to keep the
embeddable package minimal). Both ComfyUI's and other embeddable-Python
writeups confirm the same two-step bootstrap is required before any
`pip install` will work at all:

1. Uncomment `import site` in `pythonXX._pth` (and add `Lib\site-packages`
   to it) so the interpreter will look in a site-packages directory in the
   first place.
2. Bootstrap `pip` itself — either by running `get-pip.py` against the
   bundled interpreter once, or by shipping a pre-vendored `pip` wheel in
   the installer payload so no network call is needed for this one step
   (worth doing anyway, given §5's existing plan to run `check_setup.py`
   during install — no reason to make the very first `pip install` itself
   depend on network reachability to `bootstrap.pypa.io`).

This isn't a reason to reconsider Option A — it's a solved, well-documented
one-time setup cost (every embeddable-Python-based Windows tool does this
exact dance) — but it belongs explicitly in the merged doc's §5 item 4
("the installer's post-install step needs to shell out to the bundled
interpreter's own pip") as a prerequisite sub-step, not left implicit.
Recommend also adopting ComfyUI's `-s` flag (skip user site-packages) on
every post-install `pip` invocation, per §1's table — same isolation
argument as `python`-on-PATH bugs, applied to `pip`/site-packages instead.

Sources: [pypa/pip#12495 — embeddable package pip guidance](https://github.com/pypa/pip/issues/12495), [Setting up Python's Windows embeddable distribution properly](https://fpim.github.io/posts/setting-up-python-windows-embeddable-environment-properly/), [B. Nikolic — embedded Python install without privileges](https://bnikolic.co.uk/blog/python/2022/03/14/python-embedwin.html).

---

## 3. Alternative considered: conda/Miniforge instead of embeddable-Python + pip

oobabooga's one-click-installers (§1) are the closest real prior art to
Baihe's actual shape — large, sometimes-conflicting optional ML
backends — and they chose a bundled **conda** environment over
embeddable-Python + pip specifically because conda's package format can
install non-Python system binaries (`ffmpeg`, CUDA runtime libraries) as
ordinary packages inside the same isolated environment, where pip cannot
install anything that isn't a Python wheel.

This is a genuine alternative worth naming, not a reason to reverse the
merged recommendation:

- **In favor of conda:** could fold Baihe's separate "ffmpeg must be on
  PATH" `check_setup.py` check into the environment itself instead of a
  system-level prerequisite check — closer to true one-click.
- **Against, for Baihe specifically:**
  - It doesn't map onto the existing `requirements-core/media/optional.txt`
    files at all — those are pip requirement lists; adopting conda would
    mean re-deriving (and maintaining, in parallel) conda-compatible
    environment specs, which cuts directly against the merged doc's own
    §7 exit condition ("reuses, doesn't replace, Step 75's tiered
    requirements").
  - Conda environment creation is its own well-documented source of
    Windows-specific flakiness in the wild (`oobabooga/text-generation-webui#1109`,
    "One click installer fails conda environment creation"; a `#894`
    discussion titled "Conda environment is empty") — trading one class of
    install-time fragility (pip resolution/conflicts) for another
    (conda solver/environment-creation failures), not eliminating fragility.
  - Doesn't map as cleanly onto Inno Setup's own `[Components]` model
    (§3 of the merged doc) — conda env creation is an opaque external
    process outside Inno Setup's file/component bookkeeping, weakening the
    "installer components ↔ requirements-file tiers" story that's the
    main reason the merged doc gave for preferring Option A in the first
    place.

**Read:** stick with the merged recommendation (embeddable Python + pip,
with §2's bootstrap fix). Conda is a legitimate alternative that a
different app might reasonably choose, but for Baihe it would mean giving
up the exact "reuses existing tiered requirements files" property the
merged doc leaned on to prefer Inno Setup + bundled Python over a frozen
executable in the first place. This is a judgment call, not a certainty —
flagged as an open question below rather than force-closed here.

---

## 4. Stress test: GPU/CUDA detection during install

The merged doc's §3 Recommended tier says torch installs as "CUDA wheel
where a supported GPU is detected, CPU wheel otherwise," without saying
how detection happens. This turns out to be a real, documented weak point
in the wild, not a solved problem:

- A naive approach (parse `nvidia-smi`'s text output for a CUDA version
  string) is fragile across driver versions — a live bug report from a
  comparable installer (`unslothai/unsloth#5812`) shows a newer
  `nvidia-smi` output format ("CUDA UMD Version" instead of the older
  field name) breaking the installer's regex and silently falling back to
  an older, wrong CUDA target.
- The same project's fix (`unslothai/unsloth#11166`) is a **tiered
  detection chain**: try `nvidia-smi` first, fall back to a driver-library
  check, and only as a last resort fall back to a WMI PCI-bus scan
  filtered strictly on NVIDIA's vendor ID (`VEN_10DE`) rather than
  matching on GPU marketing names (which are "localized, OEM rebranded
  and shared across vendors" — not a reliable string to match). It also
  explicitly guards against hybrid-GPU laptops: the NVIDIA-specific path
  only activates when no *other* GPU vendor already resolved cleanly, so
  it can't accidentally suppress correct AMD/Intel detection.
- The same fix floors to a known-safe, older CUDA target when the driver
  version can't be mapped confidently, rather than guessing a newer one
  that might not exist — i.e., **fail toward a wheel that's more likely to
  work, never toward one that's newer but a guess.**

**Decided (2026-09-28): a CPU fallback is a firm design requirement,
regardless of how much detection effort is eventually built.** Whatever
the detection mechanism (a single `nvidia-smi` check, a fuller fallback
chain, or a manual "I have an NVIDIA GPU" checkbox — that depth question
is still open, see below), the failure mode when detection is uncertain
or fails must be "falls back to the CPU wheel with a visible note in
Diagnostics," never "silently installs a CUDA wheel that doesn't match
the actual driver and `torch.cuda.is_available()` quietly returns `False`
at runtime with no clear error anywhere." This is now a concrete
requirement for the implementation step, not just a flagged risk.

Still open, and worth resolving with the clean-Windows prototype (see
"Next steps" below) rather than in the abstract: how much detection depth
beyond the CPU-fallback floor is worth building — a single `nvidia-smi`
check, the fuller tiered chain above, or just asking the user directly.

Sources: [unslothai/unsloth#5812](https://github.com/unslothai/unsloth/issues/5812), [unslothai/unsloth#11166](https://github.com/unslothai/unsloth/pull/11166).

---

## 5. Stress test: disk space during a Recommended/Custom install

The merged doc's §2 already acknowledges Recommended-tier size is
"multiple GB, same as today's real `pip install` footprint already is,"
but doesn't quantify it or say anything about checking for it up front.
Concretely: a GPU-enabled `torch` install alone is commonly cited at
roughly 5GB combined with its own CUDA-runtime dependencies (real-world
reports vary, but multi-GB for `torch` alone is the consistent finding,
not an edge case) — before adding pyannote's diarization weights,
Whisper model weights, an OCR backend, and any voice-cloning models
Custom-tier selects. A realistic Recommended-tier install is plausibly
8-15GB+ once weights are counted, not just the pip packages.

Inno Setup itself doesn't preflight-check this by default — a script has
to add its own `[Code]` section calling Windows' free-disk-space API and
comparing it against an estimate of the selected components' size, then
warn or block before committing to the install. Left unaddressed, the
realistic failure mode is a multi-GB pip install (or a large model
download) failing partway through with an out-of-space error the user
has to diagnose themselves, rather than being told up front "Recommended
needs ~10GB free, you have 4GB."

**Decided (2026-09-28): an explicit disk-space preflight check is now a
concrete design requirement**, not just a flagged recommendation — with a
size estimate per tier/component, derived from the same
`requirements-*.txt` + known model-weight sizes the manifest in §5 item 5
of the merged doc already needs to track. This is a small addition once
that manifest exists, not new infrastructure, and pairs directly with the
tiering decision above: the per-component size estimate is exactly what
lets a prompted opt-in checkbox show its real cost before the user
commits to it.

---

## 6. Silent/unattended install — ruled in, not a gap

The prompt asked whether "silent/unattended install support for power
users" needs consideration. Verified: Inno Setup supports this natively
and it's a solved, standard feature — `/VERYSILENT /SUPPRESSMSGBOXES /SP-`
suppresses the UI and progress window entirely, with documented default
answers for every prompt that could arise (overwrite/keep-newer/disk-space
warnings all resolve to sensible non-interactive defaults), and component
selection can be passed on the same command line. This needs no extra
engineering — it's inherent to choosing Inno Setup, not something to
budget separately for. Noted here so it's explicitly ruled in (works,
free) rather than silently unconsidered.

Source: [Inno Setup command line parameters](https://jrsoftware.org/ishelp/topic_setupcmdline.htm).

---

## 7. MSI vs. non-MSI — ruled out, as expected

The prompt asked to at least rule this out explicitly. Confirmed: MSI's
real value proposition (Group Policy software deployment, SCCM/Intune
managed rollout, Windows Installer's transactional per-component
rollback guarantees) targets enterprise-managed fleets of machines under
central IT control. Baihe is a personal-use app distributed by a solo
maintainer directly to individual end users — none of MSI's actual
selling points apply, which is exactly why the merged doc's §2 already
passed over WiX (the MSI-native option in that comparison) in favor of
Inno Setup. Nothing found in this research changes that call; recorded
here explicitly so "considered and ruled out" is on record rather than
implicit.

---

## 8. Gap not addressed at all by the merged doc: code signing / SmartScreen

The merged design doc doesn't mention code signing anywhere, and this is
a real first-run UX question independent of which installer framework is
chosen:

- An **unsigned** installer from a publisher with no prior reputation
  triggers Windows Defender SmartScreen's full "Windows protected your
  PC" block on first downloads (not a soft dismissible warning) —
  independent of Inno Setup vs. NSIS vs. WiX, since this is a
  Windows/SmartScreen behavior, not an installer-framework one. Users can
  still click through ("More info" → "Run anyway"), and this is what many
  small FOSS Windows tools ship as-is.
- A standard OV code-signing certificate ($100-500/year, available to
  individuals) reduces the warning to a softer "unrecognized app" prompt
  and builds SmartScreen reputation gradually as real downloads
  accumulate — it does **not** clear the warning instantly.
- EV certificates ($250-700/year, business-only) historically bypassed
  SmartScreen immediately on first release, but **that automatic-bypass
  behavior was removed in 2024** — EV now goes through the same
  reputation-building process as OV, just at higher cost and with a
  business-registration requirement Baihe's maintainer may not have.
- Microsoft's current lower-cost recommended path for non-Store
  distribution is **Trusted Signing** (formerly "Azure Code Signing"),
  starting around $9.99/month, available to individual developers via a
  Microsoft Entra/Azure account rather than requiring business
  registration.

**Decided (2026-09-28): defer, don't decide "never."** Ship unsigned for
now — this is a small, private distribution (~3 users), not a public
release that needs to build SmartScreen reputation over time, so the
cost/reputation-building tradeoff above doesn't apply today. But this
isn't a permanent architectural decision the way the tiering/manifest
choices above are: revisit it specifically if and when a public release is
being prepared, at which point the research above (OV vs. Trusted Signing)
is what to act on. Nothing about the installer design needs to block on
this now.

Sources: [Code signing options for Windows app developers (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options), [SmartScreen reputation for Windows app developers (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation), [How to use individual code signing certificates to get rid of SmartScreen warnings](https://engy.us/blog/2021/05/25/how-to-use-individual-code-signing-certificates-to-get-rid-of-smartscreen-warnings/).

---

## 9. Update-delta mechanics — confirms, rather than weakens, the merged doc

Verified directly against Inno Setup's own documentation: Inno Setup has
**no built-in binary-delta or hash-based skip-unchanged-file mechanism**.
Every `[Run]`/`[Files]` entry you list gets reinstalled/recopied on an
update unless your own script adds version-comparison logic; the
framework's own update guidance is "make your application detect a new
version and re-run Setup.exe," not an automatic diffing system.

This doesn't weaken anything in the merged doc — it directly confirms
§5 item 5's own conclusion that "a manifest... is new bookkeeping, not a
reuse of an existing mechanism." Worth foregrounding rather than treating
as a detail to discover mid-build: a naive Inno Setup script, without the
manifest and without following §4's category separation (app files vs.
optional components vs. models vs. user data as four genuinely
independent installer components/steps), would re-copy and
re-install *everything* — multi-GB models included — on every single app
update. The manifest and the four-way component split aren't
nice-to-haves; without both, "app update ≠ redownload everything" simply
doesn't happen by default with this framework.

Source: [Inno Setup Help — Technical Notes](https://jrsoftware.org/ishelp/topic_technotes.htm).

---

## Summary of refinements to carry into the implementation step

None of these overturn Option A (Inno Setup + bundled Python) or the
three-tier/four-category structure in §3-§4 of the merged doc. Every item
below is now a **decided design requirement**, not just a flagged risk —
the 2026-09-28 decisions closed out what was previously open:

1. **§5 item 4 of the merged doc** — the post-install `pip` step needs an
   explicit `get-pip.py`-bootstrap (or vendored pip wheel) + `pythonXX._pth`
   edit sub-step before any `pip install` will work at all, plus `-s` on
   every invocation to keep the bundled interpreter isolated from a
   contributor/tester machine's own system Python (§2). To be verified by
   the clean-Windows prototype below, not just designed on paper.
2. **A CPU fallback is a firm requirement** whenever GPU detection is
   uncertain or fails — visible in Diagnostics, never a silent broken CUDA
   install (§4). Detection *depth* beyond that floor is still open — see
   below.
3. **An explicit disk-space preflight check is a firm requirement**, with
   a per-tier/component size estimate derived from the same manifest §5
   item 5 of the merged doc already needs (§5 of this doc).
4. **Heavier components stay tiered/opt-in, prompted, separate from
   Core, never bundled in by default at any tier.** Decides the merged
   doc's own §3 open call in favor of a lean default: GPU-enabled torch,
   diarization, OCR backends, voice-cloning models, and anything else
   beyond Core/Media requires an explicit prompt/opt-in. Item 3's
   per-component size estimate is what makes that opt-in prompt concrete
   (show the real cost, require a checkbox).
5. **Code signing is deferred, not decided against** — ship unsigned now
   (small private distribution), revisit specifically when a public
   release is being prepared (§8).
6. **Keep the update manifest and the four-way component split** — Inno
   Setup's lack of any built-in delta-update mechanism (§9) is something
   the updater design must account for by keeping both, not a reason to
   reconsider Option A.

## Next steps: clean-Windows prototype

The user's top recommendation is to validate the biggest unverified
assumption with evidence rather than more research: **a small prototype
installer, run on a clean Windows machine/VM, that does no more than**:

1. Extracts app files + the embeddable Python package.
2. Runs the §2 pip-bootstrap sequence (`get-pip.py`/vendored wheel,
   `._pth` edit).
3. Installs `requirements-core.txt`, then one real optional AI backend
   end-to-end (e.g. `faster-whisper` or `pyannote.audio` from
   `requirements-media.txt`/`requirements-optional.txt` — pick whichever
   is cheapest to verify a real model download + inference call against,
   not just an import) — to prove the bootstrap doesn't just `import`
   cleanly but actually runs a real workload.
4. **Runs the GPU-detection test matrix decided on 2026-09-28** — see
   below. Deliberately no broad GPU-detection system is designed yet; the
   prototype's job is to produce evidence on whether one is even worth
   building, not to implement one.

**This research session has no Windows environment to run this in** — it
executed entirely from a Linux cloud container, so this step needs either
the user's own Windows machine, a Windows VM, or a future session with
Windows access. This is the natural first task for the eventual
implementation step to start with, before building out the full
installer script, tier picker, or manifest.

### GPU-detection test matrix (2026-09-28 decision)

Test whatever detection mechanism the prototype uses (even a single naive
`nvidia-smi` check is fine for this pass — the point is observing its
behavior, not building the fallback chain from §4 yet) against three
machine states, and for each, **record**: (a) whether Baihe picked GPU or
CPU mode, (b) whether the user can override that automatic choice, and
(c) how clear the failure message is if something goes wrong.

| Machine state | What to record |
|---|---|
| No supported GPU (CPU-only machine/VM) | Picks CPU mode cleanly? Any spurious GPU-path attempt? |
| A supported NVIDIA setup (working driver, CUDA-capable) | Picks GPU mode correctly? Does it actually exercise CUDA (not just detect it)? |
| An NVIDIA setup where the installed runtime/driver can't actually use CUDA (present GPU, but e.g. a driver too old for the selected wheel, or CUDA libraries missing) | **This is the case that matters most.** Treat uncertain detection as CPU mode — never let an ambiguous read result in a broken "looks like GPU mode but CUDA doesn't actually work" install. Record whether it does fall back to CPU, and whether the failure (if any) is surfaced clearly enough for a user to understand, versus a silent `torch.cuda.is_available() == False` at runtime with no explanation anywhere. |

This matrix is what decides the open question below — not a design
document, a measurement.

## Open questions for the user / planning session

Genuinely still open after the 2026-09-28 decisions:

- **Conda/Miniforge vs. embeddable-Python + pip (§3):** closed for now
  per decision 1 above — embeddable-Python + pip stays the approach
  unless the clean-Windows prototype shows it can't reliably install
  Baihe's actual dependencies. If the prototype fails on this specifically
  (not just needs debugging, but hits a real embeddable-Python limitation
  pip can't work around), that's the trigger to revisit conda, not before.
- **GPU detection depth beyond the CPU-fallback floor (§4):** deliberately
  kept as a discussion item, not designed yet — how much engineering
  effort is worth spending on robust auto-detection (a real fallback
  chain, per `unslothai/unsloth`'s tiered approach) vs. a cheap manual "I
  have an NVIDIA GPU" checkbox depends entirely on what the test matrix
  above shows. If a naive check already handles all three states
  acceptably (correct GPU/CPU pick, or a clean fallback + clear message on
  the broken-CUDA case), there's no case for building more. If it
  misbehaves specifically on the broken-driver/broken-runtime case, that's
  the evidence that justifies the fuller detection chain.
