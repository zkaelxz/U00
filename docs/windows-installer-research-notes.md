# Windows installer — further research notes (follow-up to Step 80)

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

**User decisions (2026-09-28), resolving two of this document's open
questions directly:**

- **Heavier components must be tiered/opt-in, prompted, and kept separate
  from Core** — not silently pulled into a default install. This resolves
  the merged doc's own §3 open call between "(a) Recommended = core +
  media only" and "(b) Recommended = core + media + a curated optional
  subset": the user's direction picks (a) in spirit — anything beyond
  Core/Media (GPU-enabled torch, diarization, OCR backends, voice-cloning
  models, etc.) should require an explicit prompt/opt-in rather than
  arriving by default at any tier. See the updated §3-adjacent note below.
- **No code signing needed** — this is a small, private distribution (the
  user states ~3 users), not a public release building SmartScreen
  reputation over time. Ship unsigned and accept the "Windows protected
  your PC" click-through. This resolves §8 below; see its update.

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

**Recommendation for the eventual implementation step:** don't ship a
single naive `nvidia-smi`-output-parsing check for the Recommended tier's
GPU/CPU torch choice — budget for at least a two-tier fallback (a
reachable check, then a conservative default), and make the failure mode
"falls back to the CPU wheel with a visible note in Diagnostics" rather
than "silently installs a CUDA wheel that doesn't match the actual driver
and `torch.cuda.is_available()` quietly returns `False` at runtime with no
clear error anywhere." This is a small, real design decision the merged
doc left open by not addressing detection mechanics at all — flagging it
here rather than resolving it, since it needs to be sized against how much
engineering effort Baihe actually wants to spend on GPU detection versus
just asking the user (a "do you have an NVIDIA GPU?" installer checkbox
defaulting to an auto-detect best-guess is also a legitimate, much
cheaper answer).

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

**Recommendation:** add an explicit disk-space preflight check (with a
size estimate per tier/component, derived from the same `requirements-*.txt`
+ known model-weight sizes the manifest in §5 item 5 of the merged doc
already needs to track) to the implementation step's scope — this is a
small addition once that manifest exists, not new infrastructure.

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

**Resolved by the user (2026-09-28): ship unsigned.** Given this is a
small, private distribution (~3 users, not a public release that needs to
build SmartScreen reputation over time), the cost/reputation-building
tradeoff above doesn't apply — the installer ships unsigned, and the small
user base clicks through "More info" → "Run anyway" once. No code-signing
certificate or Trusted Signing subscription needed. This closes the
question this section originally left open; kept the research above for
the record in case distribution scale ever changes.

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
three-tier/four-category structure in §3-§4 of the merged doc. They're
additions/sharpenings to fold into §5's "what needs to change" list:

1. **§5 item 4** — the post-install `pip` step needs an explicit
   `get-pip.py`-bootstrap (or vendored pip wheel) + `pythonXX._pth` edit
   sub-step before any `pip install` will work at all, plus `-s` on every
   invocation to keep the bundled interpreter isolated from a
   contributor/tester machine's own system Python (§2).
2. **New item** — GPU/CUDA detection for the Recommended tier needs its
   own small design decision: a naive `nvidia-smi`-parsing check is a
   documented-fragile approach in comparable installers; budget for a
   fallback chain or accept a manual user checkbox instead (§4).
3. **New item** — an explicit disk-space preflight check with a
   per-tier/component size estimate, using the same manifest §5 item 5
   already calls for (§5 of this doc).
4. **Resolved by the user (2026-09-28) — ship unsigned, no code signing.**
   Small private distribution (~3 users) doesn't need SmartScreen
   reputation-building; skip the cost entirely (§8 of this doc).
5. **Resolved by the user (2026-09-28) — heavier components stay
   tiered/opt-in, prompted, separate from Core, never bundled in by
   default at any tier.** This decides the merged doc's own §3 open call
   in favor of option (a) ("Recommended = core + media only," or leaner):
   GPU-enabled torch, diarization, OCR backends, voice-cloning models, and
   anything else beyond Core/Media requires an explicit prompt/opt-in
   rather than arriving automatically. Combined with item 3 below (a
   disk-space estimate shown before the user opts in), this gives the
   installer's tier/component picker a concrete job: show the size cost of
   each optional piece and require a checkbox before it's added to the
   install plan.
6. **Reinforcement, not new** — §5 item 5's manifest and §4's four-way
   category split are load-bearing, not optional polish: without both,
   Inno Setup's lack of any built-in delta-update mechanism (§9 of this
   doc) means a naive script would redownload/reinstall everything,
   models included, on every update.

## Open questions for the user / planning session

Two of the three questions this document originally raised were resolved
directly by the user on 2026-09-28 (code signing → ship unsigned, given
~3 users; tiering → heavier components must be prompted/opt-in, separate
from Core — see the note at the top of this document and items 4-5 above).
One remains open:

- **Conda/Miniforge vs. embeddable-Python + pip (§3):** this document's
  read favors keeping the merged recommendation (embeddable Python + pip)
  specifically because it reuses the existing tiered requirements files
  without modification — but it's a real alternative real prior art
  (oobabooga) chose for a very similar problem shape, and the call
  depends partly on how much the team wants `ffmpeg`-on-PATH folded into
  the installer itself vs. left as today's separate system check. Not
  force-closed here.
- **GPU detection depth (§4):** how much engineering effort is worth
  spending on robust auto-detection (a real fallback chain) vs. a cheap
  manual "I have an NVIDIA GPU" checkbox — a genuine cost/robustness
  tradeoff, not a right-answer question. Unaffected by the two decisions
  above.
