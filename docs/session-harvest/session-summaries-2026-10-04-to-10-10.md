# Last-turn summaries of every session seen today (live + archived), merged from 4 listings

Only the final summary survives; transcripts are not readable from the lead session.

- 10-04T18:12 4Zh9E49p [ARCHIVED] Estimates: record diarization time, use stage medians 
    PR #731 merged into baihe-subtitler (2026-10-04 19:02 UTC) PR #731 merged into baihe-subtitler by zkaelxz
- 10-04T18:23 cpWduzja [ARCHIVED] Rework #728 merge speakers: server-side undo 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-04T18:24 BTSeGAcS [ARCHIVED] Rework #728 merge speakers: server-side undo **NOTE**
    PR #728 ready: CI green, no conflicts, head 5dbd04a PR #728 CI green, mergeable; awaiting lead session merge per CLAUDE.md lead session to merge PR #728 (or confirm you want me to take it out of draft and merge)
- 10-04T19:07 oo3K5PXD [ARCHIVED] Multi-language part 2: detect language per speech span 
    PR #734 merged into baihe-subtitler; parts 2 complete; parts 1 & 3 remain PR #734 merged; Mixed languages + per-span detection in
- 10-04T19:07 ZEGMgy2R [ARCHIVED] Comments: remove Step, Slice and PR ids 
    PR #733 merged into baihe-subtitler; comments-only cleanup complete PR #733 merged; comments-only cleanup landed
- 10-04T19:07 jdJ87Yti [ARCHIVED] Remove shots specs and stale fetch_static note 
    PR #732 merged; screenshot specs removed, fetch_static note updated PR #732 merged into baihe-subtitler; specs updated
- 10-04T19:08 DtLEDaPq [ARCHIVED] #728 round 2: clip deletion policy, confirm, error handling 
    PR #728 ready to merge: 10228 backend tests pass, frontend clean, new merge logic tested PR #728 pushed (5dbd04a), mergeable, awaiting CI
- 10-04T19:10 MzDsyi6n [ARCHIVED] Japanese clip: Whisper vs Qwen accuracy 
    Japanese speech-recognition accuracy comparison (Whisper/Noto/Google) scored against hardsub subtitles, merged to docs/asr-experiments.md in PR #736 PR #736 merged; Japanese ASR comparison in docs/asr-experiments.md
- 10-04T19:10 vNVngBJ3 [ARCHIVED] Korean/Japanese/English clip: language accuracy 
    PR #738 merged into baihe-subtitler; docs/asr-experiments.md updated in base branch PR #738 merged; docs/asr-experiments.md updated
- 10-04T19:11 XxAcd4wZ [ARCHIVED] Disk usage: list and remove unused voice clips 
    PR #735 merged into baihe-subtitler after CI re-run cleared timing flake PR #735 merged; flake re-run green
- 10-04T19:12 qc1DzEgP [ARCHIVED] Public test: Korean accuracy, models and settings 
    Korean benchmark moved above Japanese section in docs/asr-experiments.md Korean benchmark section moved to docs/asr-experiments.md; PR #740 merged
- 10-04T19:12 aRDxfZ1e [ARCHIVED] Public test: Japanese accuracy, models and settings 
    PR #743 merged; benchmark section added to docs/asr-experiments.md PR #743 merged; benchmark section added to docs/asr-experiments.md
- 10-04T19:12 U6w3mQmL [ARCHIVED] Public test: Chinese accuracy, models and settings 
    PR #742 merged into baihe-subtitler; Chinese benchmark section in docs/asr-experiments.md PR #742 merged: Chinese benchmark (2026-10-04) added to docs
- 10-04T19:12 aU1dp5dy [ARCHIVED] Public test: mixed-language switching with known truth 
    Measured transcription performance across 6 languages on public mixed-language audio; results in docs/asr-experiments.md (PR #751 merged). Key finding: multilingual=True gives 33–70% wrong-script segments; fixed language rewrites other languages into it (CER ~100%). Guard idea ineffective on real data. mixed-language ASR benchmark complete; PR #751 merged
- 10-04T21:20 sFJuCxbD [ARCHIVED] #728 round 4: remove file deletion from merge/undo **NOTE**
    PR #728: merge conflict resolved, tests 10K+ pass, 1 known failure unrelated
- 10-04T21:24 zsE7PfDg [ARCHIVED] #735 rework: close pick race, fix all review items 
    PR #735: disk-usage refactor with race tests, 80 passed; speaker-merge undo hook marked; decision needed on clip-lock ordering PR #735 ready for review; awaiting lock-order decision confirm: take all clip locks before library hold, or keep current order (disk usage holds library hold first)?
- 10-04T21:25 nUuSMRVz [ARCHIVED] Remove Step ids from user-visible strings 
    PR #739 merged into baihe-subtitler; roadmap step/slice/B-row/PR IDs removed from user-visible text PR #739 merged; roadmap IDs removed from user text
- 10-04T23:21 47csQAcb [ARCHIVED] Public noisy-audio test: improving Whisper accuracy 
    PR #812 (asr-experiments benchmark) merged to baihe-subtitler; docs/asr-experiments.md updated with clean-speech rows PR #812 merged; asr-experiments docs updated
- 10-05T00:00 WKkYki2e [ARCHIVED] Docs page: database and schema **NOTE**
    docs/database.md merged into baihe-subtitler via PR #748 docs/database.md drafted, reviewed, merged to PR #748
- 10-05T00:00 55kqJRGz [ARCHIVED] Docs page: background jobs 
    docs/background-jobs.md merged into baihe-subtitler; index updated in docs/README.md PR #747 merged; docs/background-jobs.md live
- 10-05T00:00 wtCLahwG [ARCHIVED] Docs page: engine backends 
    docs/engine-backends.md + docs/README.md update merged to baihe-subtitler docs/engine-backends.md merged in PR #745
- 10-05T00:00 KPjVynQi [ARCHIVED] Docs page: API and services 
    docs/api-and-services.md + docs/README.md index entry merged to baihe-subtitler docs/api-and-services.md merged in PR #744
- 10-05T00:00 zVCwbjGj [ARCHIVED] Docs page: frontend and design system 
    docs/frontend-design-system.md + docs/README.md index entry merged to baihe-subtitler PR #746 merged; docs/frontend-design-system.md live
- 10-05T00:00 yrTZuRym [ARCHIVED] Sweep docs/specs for stale statements **NOTE**
    PR #750 merged into baihe-subtitler: fixed stale claims in discover-sources-live-api-spec.md, gpu-worker-plan.md, step-141-pc-shell-and-connect.md, ux-workspace-shell-and-review.md, scanlate-api-spec.md; unsure items documented for follow-up spec sweep complete; PR #750 merged, 5 docs updated
- 10-05T00:00 rVYAmrfi [ARCHIVED] Dependency leftovers: canary and constraints 
    PR #752 merged into baihe-subtitler: dependency constraints + canary audit workflow live post-branch-switch PR #752 merged; dependency housekeeping complete
- 10-05T00:00 cF1v2niF [ARCHIVED] Navigation audit: left menu, Ctrl+K, Jobs table **NOTE**
    navigation proposal (left menu, Ctrl+K palette, Jobs table) merged in PR #749; awaiting owner decisions D1–D12 + original audit text (S1–S17, PR labels C–J) navigation proposal drafted & merged to docs/design/navigation-proposal.md
- 10-05T00:01 pQp6bKp8 [ARCHIVED] Qwen speech-detection backend: auto language and accuracy gap 
    PR #766 merged with auto-language detection and accuracy fixes (Korean −33%, Chinese −22%, Japanese −19% error reduction); docs updated PR #766 merged; error rates: Korean −33%, Chinese −22%, Japanese −19%
- 10-05T00:12 EBjaktNh [ARCHIVED] Browser extension: run end-to-end check and apply fixes **NOTE**
    PR #756 merged into baihe-subtitler; extension e2e verified on latest app; 4 known issues flagged in PR body for follow-up PR #756 merged; extension verified; open items noted
- 10-05T00:20 CbwhALTJ [ARCHIVED] Whisper model labels, warnings and CPU fallback from benchmarks 
    PR #758 merged; missing Japanese clean-speech ASR numbers in docs provide the Japanese clean-speech ASR run (turbo 6.61, large-v3 6.54, medium 8.47) or confirm it doesn't exist
- 10-05T00:20 DaVb1Unz [ARCHIVED] Check qwen-asr install path and sox build failure 
    PR #759 merged into baihe-subtitler with sox fallback; Windows install steps in docs/asr-experiments.md PR #759 merged; sox fallback in app; Windows steps documented
- 10-05T00:20 K94JMrUX [ARCHIVED] Merge confirm: say where leftover clips go 
    Merge confirm shows voice-clip message when source clip stays behind (PR #754 merged into baihe-subtitler) PR #754 merged; merge-confirm sentence added
- 10-05T00:20 sqa5vz1J [ARCHIVED] CLI command to set a line's language **NOTE**
    CLI set-language wired (python cli.py set-language --id N --lines/--speaker --lang CODE); app & CLI aligned per CLAUDE.md; test failures pre-existing PR #757 merged; CLI set-language & inspect-line complete
- 10-05T00:20 yC468oXM [ARCHIVED] Make the real-ffprobe test fixture portable 
    PR #755 merged to baihe-subtitler; ffprobe test fixture fixed PR #755 merged; ffprobe test fixture fix live on baihe-subtitler
- 10-05T02:40 8WiCsEud [ARCHIVED] Nav N1: navigation registry (no visible change) **NOTE**
    Navigation N1 (registry) merged into baihe-subtitler; D6 permission mismatches and docs merge conflict noted for follow-up N1 navigation registry complete; PR #769 merged
- 10-05T02:40 NKwnVcL8 [ARCHIVED] Nav N4: drama_id and kind on JobRecord (API) 
    N4 navigation job fields: PR #770 merged into baihe-subtitler; kind assignments and sources handling flagged for review PR #770 merged; N4 task complete
- 10-05T03:14 vGUW5mZK [ARCHIVED] Nav N2: left menu on wide screens **NOTE**
    PR #774 merged into baihe-subtitler; left-menu nav (N2) complete. Screenshots in scratchpad; narrow-desktop default (rail collapse <1280px) flagged as design follow-up PR #774 merged; N2 nav task complete
- 10-05T03:14 6xwe8v8R [ARCHIVED] Nav N5: Jobs page 
    PR #776 merged into baihe-subtitler; Jobs page + shared list + Diagnostics summary complete PR #776 merged; Jobs page, shared list, Diagnostics summary shipped
- 10-05T03:14 n9ieQ9Tv [ARCHIVED] Job pill in the title's sticky bar 
    Job pill workspace complete: pill, popover, finish flash, tests merged into baihe-subtitler PR #773 merged; job pill complete
- 10-05T03:25 D6vf2MFr [ARCHIVED] Live page: fullscreen, bigger video, make Video delay verifiable 
    PR #775 merged; live-video fullscreen delay implemented. Real-stream testing (fullscreen button, delay note, duration reporting) and screenshot attachment remain manual tasks. PR #775 merged to baihe-subtitler; live-video fullscreen delay fix shipped
- 10-05T03:41 nyAhCHP8 [ARCHIVED] Glossary: one \ 
    PR #779 merged; glossary term extraction and suggest-terms bar now live on baihe-subtitler PR #779 merged into baihe-subtitler; suggest terms bar live
- 10-05T03:43 WZ7ZR4Kj [ARCHIVED] Reflect-mode translation: report progress per pass and batch 
    PR #780 merged: fixed misleading progress reporting in Reflect-mode translation PR #780 merged, CI green; reflect-progress fix shipped
- 10-05T03:48 cZKc2AwA [ARCHIVED] Balance page links for AI providers 
    balance links added to Settings (PR #778 merged); Claude & Gemini URLs need manual verification PR #778 merged; balance links live — need to click Claude & Gemini URLs open platform.claude.com and aistudio.google.com/usage to verify links work
- 10-05T04:07 pyZpweWD [ARCHIVED] Translate: report empty results, fit batch size **NOTE**
    PR #781 merged; bug fix (translate empty results) complete PR #781 merged into baihe-subtitler
- 10-05T04:44 UzAKzz6u [ARCHIVED] Split long merged lines: progress and cancel 
    PR #784 merged into baihe-subtitler; transcription progress stuck-at-99% bug resolved PR #784 merged; \
- 10-05T04:45 jZ7PRh2K [ARCHIVED] Cost estimate: stop pricing unknown Claude models at Opus rates 
    PR #783 merged; tier-aware cost fallback deployed. Owner should verify Sonnet 5.5 pricing and optionally recompute historical spend rows. PR #783 merged; cost fallback live on baihe-subtitler
- 10-05T04:51 7uULVq5P [ARCHIVED] Review: Re-transcribe item in the line menu 
    PR #785 merged into baihe-subtitler; Re-transcribe… menu item now in LineActionsSheet PR #785 merged; Re-transcribe added to Review menu
- 10-05T05:25 VJm1WaWE [ARCHIVED] Re-split: cut long lines that use spaces, not punctuation 
    PR #786 merged; Structure re-segment fix in place. Check drama source_language if still failing. PR #786 merged into baihe-subtitler; fix deployed
- 10-05T05:45 VotxpnvL [ARCHIVED] Resolve N5 Jobs page merge conflict 
    Merge commit merged baihe-subtitler into nav-n5-jobs-page; resolved NavItem + App.tsx conflicts; all checks pass (tsc, vitest 1717/1717, playwright 41/41) PR #776 merged into nav-n5-jobs-page; 1717 tests pass
- 10-05T06:04 X8PANw86 [ARCHIVED] Left rail: collapsed by default below 1280 px **NOTE**
    PR #787 merged into baihe-subtitler. Left rail now defaults collapsed below 1280px, expanded at 1280px+, respecting saved user preference at all widths. All CI checks passed before merge. PR #787 merged; rail defaults collapsed <1280px, expanded ≥1280px
- 10-05T06:04 1KuemF2C [ARCHIVED] Glossary tidy: rename NovelGlossary, fix lint warning 
    Fixing it would have changed behaviour, as explained earlier in the PR.
- 10-05T06:05 gvWE5DC7 [ARCHIVED] Re-cost past usage rows (opt-in, preview first) 
    PR #789 merged; usage re-costing (explicit model list, batch halving, stale-preview 409) now in baihe-subtitler PR #789 merged to baihe-subtitler; usage re-costing live
- 10-05T06:31 Dw8uCBge [ARCHIVED] Jobs: page field for title-less jobs, retire job-history route 
    PR #791 merged into baihe-subtitler; titleless jobs linked via new page field PR #791 merged; titleless jobs now carry page field
- 10-05T06:31 oTMxk6dk [ARCHIVED] Nav: hide Diagnostics and Benchmark Lab without permission 
    PR #795 merged to baihe-subtitler: hidden Diagnostics/Lab for non-admins, Jobs under System, phone spec added PR #795 merged; nav items hidden, Jobs in rail, specs green
- 10-05T06:31 obVBn4PB [ARCHIVED] Settings regroup: closed by default, spacing, groups 
    PR #801 merged into baihe-subtitler; settings regroup complete PR #801 merged; settings regroup landed
- 10-05T06:31 8tjwK5Ws [ARCHIVED] Wide desktop layout for list and card pages 
    PR #793 merged: introduced --desktop-content-max CSS var, widened desktop layout from 1200px to accommodate expanded sidebar PR #793 merged into baihe-subtitler; layout widened
- 10-05T06:35 8t2jdFXU [ARCHIVED] Nav N3: menu drawer on phones and narrow screens **NOTE**
    N3 (drawer <1024px) complete in PR #797 merged to baihe-subtitler; N5+ awaiting #795 (nav-hide-diagnostics-jobs-group) to land before follow-up gear-menu cleanup N3 drawer implemented, PR #797 merged; N5+ blocked on #795 landing
- 10-05T06:35 neSr1y5y [ARCHIVED] Audit cleanups: Library header and Library tools grouping 
    PR #794 merged into baihe-subtitler; S11 Library header and S12 tools cleanup done. Pending: delete .rail-duplicate buttons when N3 phone drawer ships. PR #794 merged; S11/S12 Library cleanups complete
- 10-05T06:35 RdaL44XC [ARCHIVED] Audit cleanups: Assistant developer-mode link, Reader edit links 
    PR #796 merged into baihe-subtitler; S15/S17 duplicate links consolidated PR #796 merged; S15/S17 links fixed on baihe-subtitler
- 10-05T06:35 Z3VQm8S5 [ARCHIVED] Multi-language video: next unmerged part 
    PR #792 merged; multi-language video support in progress (export, alignment, glossary, bulk paths remain) PR #792 merged; multilang-next-part ready for continued work
- 10-05T06:45 G8z7cssm [ARCHIVED] Resolve rail-default PR conflict 
    PR #787 rebased on baihe-subtitler: App.tsx conflict resolved, navItems clean merge, tsc + vitest + playwright all green PR #787 merged with baihe-subtitler; conflicts resolved; all tests pass (1722 vitest + 35 playwright)
- 10-05T06:50 W6wrMHTa [ARCHIVED] Re-cost rework: explicit corrections list, batch and cache-write aware **NOTE**
    selection rule corrected for #790 models; 352 pytest passed; needs vitest + Playwright verification rule fix + tests ready; vitest + Playwright spec need user run run `npx vitest run` and Playwright past-costs spec, or allow the command
- 10-05T07:16 VnUyUo94 [ARCHIVED] Resolve N3 drawer PR conflict 
    Resolved conflicts in assistant.spec.ts and assistant.mobile.spec.ts; Library.tsx header buttons removed; 213/215 Playwright tests pass PR #797 conflicts resolved; tsc/vitest/Playwright clean
- 10-05T07:20 j6TYsNwe [ARCHIVED] Multi-language: bulk, stronger-engine and single-line re-translate rea 
    PR #799 merged into baihe-subtitler; per-line language tags (spoken in X) now active PR #799 merged; lang-per-line tags live on baihe-subtitler
- 10-05T07:20 TKPR4mii [ARCHIVED] Multi-language: export and forced alignment use the line's language **NOTE**
    PR #798 merged: per-line language for align & export; docs updated. Follow-ups: English aligner gap, resegment.max_line_chars docstring, transcript alignment in app/CLI PR #798 merged; multilang align+export complete
- 10-05T07:28 yVSD6vjS [ARCHIVED] Re-cost follow-up: required preview count, docstrings, 409 UI **NOTE**
    PR #800 merged into baihe-subtitler; usage re-cost follow-up fixes complete (stale-preview handling, row double-count fix, new tests, 352+1723 tests green) PR #800 merged; re-cost follow-up complete
- 10-05T07:44 9YSnGxoq [ARCHIVED] Admin: reset the monthly spend counter without raising the cap 
    PR #803 merged into baihe-subtitler on 2026-10-05 11:29 UTC by zkaelxz; all CI green on final head (cc64381) PR #803 merged 2026-10-05; final head cc64381 all CI green
- 10-05T08:17 cWUByLxK [ARCHIVED] Audit cleanups: one engine picker label and shared help text **NOTE**
    PR #804 merged: S10/S14 engine labels unified, shared help text added; follow-ups noted in PR PR #804 merged into baihe-subtitler; labels unified, help text synced
- 10-05T08:17 NsgcJZya [ARCHIVED] Audit cleanup: Diagnostics model engines shown once 
    PR #805 merged; S16 model engine deduplication in diagnostics frontend complete PR #805 merged to baihe-subtitler; S16 diagnostics dedup complete
- 10-05T08:22 jdqCqdD9 [ARCHIVED] Resolve Settings regroup PR conflict 
    PR #801 merged: Settings sections closed by default, Jobs card split, card spacing updated; 1726 vitest + 36 Playwright tests green PR #801 merged into baihe-subtitler; conflicts resolved, 36 e2e tests pass
- 10-05T08:22 pyJQanrQ [ARCHIVED] Resolve rail-default PR conflict (again) 
    PR #787 rebased and pushed; working tree clean PR #787 rebased; conflicts resolved; branch pushed
- 10-05T08:30 QBm6XS8C [ARCHIVED] Compare transcription: try another model on chosen lines, with transla 
    PR #810 merged into baihe-subtitler with all CI green PR #810 merged; Compare transcription shipped
- 10-05T08:34 A3QXceVY [ARCHIVED] Review: tick boxes to select individual lines 
    PR #807 merged to baihe-subtitler; line selection tick boxes shipped. Use useLineSelectionContext() to read selectedIds; add bulk actions via SELECTION_ACTIONS in review/selectionActions.ts PR #807 merged; selection API ready for `compare-transcription`
- 10-05T09:30 zFH3fMPe [ARCHIVED] PR 803: review fixes (future reset marker, display figure) 
    PR #803 merged with both review findings fixed PR #803 merged to baihe-subtitler; review fixes applied
- 10-05T09:31 8WgQLJFb [ARCHIVED] PR 810: review fixes (path leak, snapshot, job guard) 
    PR #810: snapshot reuse UX, undo history, job-overlap guard; all tests green (pytest 562, tsc clean, vitest 1738, Playwright 4) PR #810 fixes complete: snapshot UX, undo points, job guard; 2562 tests pass
- 10-05T09:56 tWi4Scwn [ARCHIVED] PR 798: resolve merge conflict with baihe-subtitler 
    merge commit 3b4dcc4 pushed; docs conflicts resolved (STATUS.md + technical-notes.md); 600+ tests pass merged origin/baihe-subtitler into multilang-export-align; conflicts resolved & tests green
- 10-05T10:03 ZQbTeTDV [ARCHIVED] Nav: Customize menu (hide items per person) 
    Customize menu (D13) implemented & merged in PR #813; persists per-browser; visibleNavItemsFor ready for palette PR #813 merged: Customize menu feature shipped
- 10-05T11:29 NzEh2E2h [ARCHIVED] PR 810: resolve merge conflict with baihe-subtitler 
    Merge commit pushed to compare-transcription branch; 564 pytest, 1764 vitest, 69+ Playwright tests passing; flaky compare-transcription.spec.ts fixed with expect.poll merged origin/baihe-subtitler into compare-transcription; conflicts resolved; tests pass
- 10-05T11:30 ajGHc1Z4 [ARCHIVED] Nav: Ctrl+K quick search (palette) 
    Ctrl+K quick search palette implemented and merged to baihe-subtitler Ctrl+K palette implemented; PR #814 merged to baihe-subtitler
- 10-05T13:41 7rx1FCxf [ARCHIVED] Forced alignment for English lines (Qwen3 aligner) 
    PR #816 merged; English forced-alignment via Qwen3 ready; recommend testing on real audio PR #816 merged; English alignment ready for audio test
- 10-05T13:46 GZGAAqHh [ARCHIVED] Review: \ 
    PR #818 merged into baihe-subtitler; Review tick-box selection now drives Compare transcription PR #818 merged; Review selection wired to Compare transcription
- 10-05T14:21 urmSLsQb [ARCHIVED] Nav: back button / breadcrumb **NOTE**
    PR #819 merged: breadcrumb nav wired. Follow-ups: render on Settings/Benchmark Lab, Back button (N8), copy-link (N8) PR #819 merged; breadcrumb core done, 3 follow-ups listed
- 10-05T14:23 HKZH47v5 [ARCHIVED] Show which device Qwen3-ASR / aligner / VAD run on, and why 
    PR #823 merged into baihe-subtitler with Qwen3 GPU device and fallback visibility fix PR #823 merged; Qwen3 device visibility fix deployed
- 10-05T14:29 hH9PQ9NE [ARCHIVED] Docs refresh after gap review (STATUS, nav proposal, README, CI commen 
    PR #824 merged: STATUS.md, CONTRIBUTING.md, package.json, lockfile updates, and CHANGELOG entry gap review: 5 docs/hygiene fixes landed in PR #824
- 10-05T14:29 g34AQMbV [ARCHIVED] CI hardening: failure evidence, forbidOnly, OpenCV, faster Python job 
    PR #826 merged with forbidOnly, timeout env var, and review-stage gating. Remove stray /dur.txt; 15 waitForTimeout calls remain for manual review. PR #826 merged; CI hardening complete
- 10-05T14:29 HEPEKw6d [ARCHIVED] CLI and app parity: bulk style note, dub/translate/align validation 
    PR #825 merged into baihe-subtitler with CLI/app drift fixes PR #825 merged; CLI/app drift fixes complete
- 10-05T14:29 sH9PrW78 [ARCHIVED] Frontend drift cleanups: dead nav surface, jobs pcOnlyFetch, settings  
    Fixing it means wrapping the call and removing it from that list.
- 10-05T14:29 VLm4it6h [ARCHIVED] Compare transcription and re-transcribe line: honour each line's langu 
    PR #820 merged into baihe-subtitler; per-line language fix for compare_transcription_service deployed PR #820 merged; per-line language fix live
- 10-05T14:31 N5dZvnuB [ARCHIVED] Security hardening: redaction, page_server, url_guard **NOTE**
    PR #822 merged into baihe-subtitler; 4 security lows fixed; 2 pre-existing issues noted (IPv6 loopback, space-in-filename redaction) PR #822 merged; 4 security fixes landed in baihe-subtitler
- 10-06T02:29 qHAd2qV2 [ARCHIVED] Re-time existing lines with the Qwen3 aligner (Review) 
- 10-06T02:56 bdH8tixH [ARCHIVED] Finish #589: live-stream SSRF egress proxy 
    PR #589: all CI green, no conflicts, awaiting your Opus security review and merge PR #589 CI green, ready for security review & merge
- 10-06T02:57 RnxHB1PF [ARCHIVED] Path scrubber: redact filenames with spaces 
    permission denied attaching zkaelxz/u00 with push access approve the add_repo call for zkaelxz/u00 with push access
- 10-06T02:57 FnSK2XJF [ARCHIVED] Frontend: updatePreferences through pcOnlyFetch 
    repo zkaelxz/u00 access denied; add-repo permission refused approve attaching zkaelxz/u00 with push access, or start session with repo pre-attached
- 10-06T02:57 KUST6pjF [ARCHIVED] e2e: replace waitForTimeout with real waits 
    zkaelxz/u00 repo access denied; cannot clone approve attaching zkaelxz/u00 with push access, or start new session with repo pre-added
- 10-06T02:57 hooEGsMK [ARCHIVED] Phone tests: month-counter reset and past costs 
    repo zkaelxz/u00 not accessible; push access needed approve repo attach with push access, or add zkaelxz/u00 to session sources
- 10-06T02:57 2sZzfXKh [ARCHIVED] Review: preview a history snapshot before restoring 
    zkaelxz/u00 push access denied by permission classifier approve add_repo push access or attach repo yourself
- 10-06T02:57 ELbJVRhQ [ARCHIVED] Compare transcription: initial prompt and names UI 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-06T02:57 ii4UE8vq [ARCHIVED] CLI: transcribe, QC and glossary commands 
    repo zkaelxz/u00 not attached; GH_TOKEN invalid approve attaching zkaelxz/u00 with push access, or fix GitHub access in environment settings
- 10-06T02:59 Q6r6hamZ [ARCHIVED] Re-time existing lines with the Qwen3 aligner (Review) **NOTE**
    PR #836 draft open; e2e specs fixed, awaiting CI run
- 10-06T02:59 7SZ1srzA [ARCHIVED] Path scrubber: redact filenames with spaces **NOTE**
    PR #827 merged; filenames with spaces now fully redacted (with noted edge cases) PR #827 merged into baihe-subtitler; redaction fix deployed
- 10-06T02:59 mFSV5LMd [ARCHIVED] Frontend: updatePreferences through pcOnlyFetch 
    PR #828 merged; updatePreferences now wrapped in pcOnlyFetch guard PR #828 merged into baihe-subtitler; updatePreferences fix in
- 10-06T02:59 tG4tXMJE [ARCHIVED] e2e: replace waitForTimeout with real waits 
    PR #844 merged into baihe-subtitler; 15 waitForTimeout sites analyzed (3 converted to conditions, 12 kept as windows with WHY comments); 20× run and CI clean PR #844 merged; 15 waitForTimeout analyzed, 3→conditions, 12→windows
- 10-06T02:59 8uWvS9Rc [ARCHIVED] Phone tests: month-counter reset and past costs 
    PR #829 merged; phone Playwright specs for month-reset and past-costs flows on baihe-subtitler PR #829 merged into baihe-subtitler; phone specs + Undo fix landed
- 10-06T03:00 jTH1zANS [ARCHIVED] Review: preview a history snapshot before restoring 
    Line-history snapshots now have Preview action (frontend-only, shows line count + before/after list) PR #831 merged; history snapshot preview shipped
- 10-06T03:00 bCnz7Ds8 [ARCHIVED] Compare transcription: initial prompt and names UI 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-06T03:00 ZjqUVdKZ [ARCHIVED] CLI: transcribe, QC and glossary commands **NOTE**
    PR #837 merged; glossary/AI-review/series-level features remain for future; defusedxml requirements gap noted PR #837 merged to baihe-subtitler; follow-ups tracked
- 10-06T03:00 yKdN9T6J [ARCHIVED] Timing hotkeys and playback speed in Review (Step 159) 
    PR #834 merged into baihe-subtitler with playback speed control and timing hotkeys PR #834 merged; playback speed + timing hotkeys shipped
- 10-06T03:00 k495V8rt [ARCHIVED] Whisper hallucination guards (Step 161) 
    PR #832 merged; hallucination guards + stock-phrase filter + tests with fakes + CLI/app parity PR #832 merged into baihe-subtitler; hallucination guards + tests
- 10-06T03:00 heaqRGFx [ARCHIVED] Keep originals on re-upload and failed extraction (Step 144a) 
    PR #842 CI all green; awaiting security review + lead merge
- 10-06T03:00 ffk9fTQF [ARCHIVED] Glossary proposals: counts, confidence, ignore list (Step 148) **NOTE**
    Draft PR #841 (Step 148: glossary proposals) merged into baihe-subtitler with CI green; includes service tests, API permission checks, backup/import coverage PR #841 merged into baihe-subtitler; glossary proposals complete
- 10-06T03:01 zXVS698z [ARCHIVED] Compare transcription: initial prompt and names UI 
    PR #835 merged; Compare transcription hint + extra-names fields now in baihe-subtitler PR #835 merged into baihe-subtitler; Compare fields live
- 10-06T03:03 icu3G1gC [ARCHIVED] Waveform timeline with draggable cues (Step 164) 
    PR #843 CI in progress (shard 4 re-running); phone-spacing flake under investigation
- 10-06T03:03 t6gRyPJa [ARCHIVED] Translation batches start at scene breaks (Step 176) 
    PR #833 merged into baihe-subtitler. Translate batches now start at scene breaks via saved preference. Settings toggle, bulk path, and cut signals remain open. PR #833 merged; Step 176 complete. Scene-aware batches live
- 10-06T03:03 bAHSfA2A [ARCHIVED] English \ 
    PR #838 green (all 7 checks); awaiting merge into baihe-subtitler
- 10-06T03:03 7qZaZLFW [ARCHIVED] chrF scoring for translation benchmarks (Step 150) **NOTE**
    Step 150 complete: chrF translation scoring via optional sacrebleu dependency with difflib fallback; PR #840 merged into baihe-subtitler. Pass marks: 0.5 (chrF), 0.8 (difflib). Two follow-ups noted: recalibrate 0.5 threshold post-benchmark, wire CLI path to chrF. PR #840 merged; chrF scoring + sacrebleu wired, tests green
- 10-06T03:03 KeHxM1t8 [ARCHIVED] Docs: roadmap backlog status in STATUS.md 
    PR #830 merged; docs/STATUS.md backlog section added to baihe-subtitler PR #830 merged into baihe-subtitler; backlog docs landed
- 10-06T03:17 RoC22gAe [ARCHIVED] Review pop-out: scale subtitle text with the window 
    PR #839 merged into baihe-subtitler; caption scales with pop-out width, subtitle size control added PR #839 merged; caption scaling + subtitle size control in
- 10-06T05:17 7ahbJq8e [ARCHIVED] Fix flaky phone-spacing \ 
    PR #846 merged into baihe-subtitler; dense-button test now stable (120/120 passes after fix) PR #846 merged; phone-spacing flake fixed (120/120 pass)
- 10-06T05:23 AikFniNc [ARCHIVED] Glossary ignore list: cap size per series 
    PR #845 merged into baihe-subtitler with ignore-list cap, status lookup narrowing, profiles.created_at revert, renderings fix PR #845 merged; ignore-list cap + fixes shipped
- 10-06T05:24 LxQTCDPL [ARCHIVED] Fix review findings on #838 (English cleanup) 
    merged baihe-subtitler, fixed 4 findings, pushed commits; CI running
- 10-06T05:29 c1vJCaoj [ARCHIVED] Fix security review findings on #589 
    CI running on PR #589 head 3d955d0; next check ~10:46 UTC
- 10-06T05:53 qQXfrQJg [ARCHIVED] Live capture: pipe the stream into ffmpeg (#589) 
    PR #589 merged; live capture now reads only piped stream, egress via proxy PR #589 merged to baihe-subtitler; live capture ffmpeg egress guarded
- 10-06T05:54 UZQ9srKg [ARCHIVED] Fix flaky breadcrumbs e2e spec (shard 1) **NOTE**
    PR #847 merged into baihe-subtitler; breadcrumb spec flake fixed; test_update_service.py race flagged for follow-up breadcrumb spec fix merged to PR #847; race condition identified
- 10-06T06:07 AharGGQ3 [ARCHIVED] Resolve conflicts on #843 (waveform) **NOTE**
    PR #843: merged baihe-subtitler (conflicts resolved in LinesPanel.tsx and Player.tsx; 2078 tests pass). Note: waveform drags now blocked during open drafts (follows hotkey rule). merged baihe-subtitler into step-164-waveform; PR #843 updated; all tests pass
- 10-06T06:07 nFtt9SMf [ARCHIVED] Resolve conflicts on #839 (pop-out subtitle size) 
    Merged branch with both conflict files resolved (Player.tsx + playerLogic.test.ts); tsc clean, 1816 vitest pass, 62 Playwright specs pass (review-*, phone variants) merged origin/baihe-subtitler into popout-subtitle-size; conflicts resolved; tests pass
- 10-06T06:07 5m44HVF6 [ARCHIVED] Resolve conflicts on #836 (re-time) 
    merged baihe-subtitler into step-retime-lines (PR #836); conflict resolved; 715 pytest + 1816 vitest + 11 Playwright pass merged origin/baihe-subtitler into PR #836; all tests pass
- 10-06T06:19 sZmcWFc8 [ARCHIVED] Frontend: resolve the npm audit high-severity finding 
    PR #848 merged into baihe-subtitler; npm audit vulnerability fixed PR #848 merged; source-map-js upgraded to 1.2.2
- 10-06T06:24 nDXhqYmx [ARCHIVED] Settings: upload size limit control 
    PR #850 merged after all seven CI checks passed PR #850 merged; CI all green
- 10-06T06:25 ZP8bhoko [ARCHIVED] Fix review findings on #836 (re-time) **NOTE**
    Merged origin/baihe-subtitler into step-retime-lines; fixes pushed; PR #836 updated 10709 tests pass (1 pre-existing fail); pushed to step-retime-lines, PR #836 updated
- 10-06T06:25 iJu7iqyn [ARCHIVED] Fix review findings on #842 (keep originals) **NOTE**
    PR #842 merged. Open: front_door.py:211-216 needs install_media routing; defusedxml missing from requirements-core.txt on some machines PR #842 merged to baihe-subtitler; front-door media path remains
- 10-06T06:30 4JckPCnw [ARCHIVED] Fix: cancelling a forced-alignment job does nothing **NOTE**
    Draft PR #849 merged into baihe-subtitler: added cancel checks to align job loop; all CI green (test, frontend, e2e shards); tested with fakes PR #849 merged; cancel on align job now stops at next check
- 10-06T06:36 a4JZrcqd [ARCHIVED] Re-split lines: split sensitivity control 
    PR #851 merged at 13:42 UTC by zkaelxz; full backend CI green on merged head PR #851 merged into baihe-subtitler; CI all 7 jobs green
- 10-06T06:54 bN455DKv [ARCHIVED] Split line dialog: visible cut marks and translation suggestion 
    PR #852 merged into baihe-subtitler with cut marker, nudge buttons, suggested English cut, preview times, and Play feature PR #852 merged; split-dialog cut marks complete
- 10-06T07:06 3LVVxxZX [ARCHIVED] Fix review findings on #843 (waveform) 
    PR #843 updated: merged baihe-subtitler, all findings addressed (429 backpressure, 1-decode/caller, 120s cap, ffmpeg hardening, UX save drop), tests passing merged baihe-subtitler, pushed fixes to #843, 232 py + 1831 fe + 47 pw tests green
- 10-06T13:12 ySoNSpiG [ARCHIVED] Split dialog follow-ups: Play spec, dark-mode contrast 
    PR #853 CI queued (7 checks); subscribed to results
- 10-06T13:15 cjQuwtER [ARCHIVED] Fix review findings on #850 (upload limit) 
    PR #850: all 5 findings fixed (RAM cap, env clamp, disk checks, household tests, docs); 483 backend + 1823 frontend tests passing all 5 review findings fixed, tests passing, pushed to #850
- 10-06T13:17 DTEpp8E9 [ARCHIVED] Fix review findings on #851 (re-split sensitivity) 
    pushed perf fixes to resplit-sensitivity; awaiting CI on latest commit
- 10-06T13:19 4pJQNFFG [ARCHIVED] Fix review findings on #843 (waveform), round 2 
    fixes pushed to step-164-waveform; running final checks
- 10-06T13:19 oKYqo6yy [ARCHIVED] Follow-ups from the #842 and #838 reviews **NOTE**
    Should I do it on a new branch off the latest `baihe-subtitler` and open a draft PR? Should I do it on a new branch off the latest `baihe-subtitler` and open a draft PR?
- 10-06T13:19 gEQynzZG [ARCHIVED] Resolve conflicts on #838 (English cleanup) 
    PR #838 merge complete: cli.py (kept both imports), breadcrumbs.spec.ts (comment), all Python tests pass (482), frontend tsc+vitest pass (1827) merged origin/baihe-subtitler, resolved conflicts, pushed to step-173-english-cleanup
- 10-06T13:20 mW4qFZ6P [ARCHIVED] Review: show the full text of a line's flag note 
    PR #854 merged into baihe-subtitler; flag note now readable on Review line chip PR #854 merged; flag note full-text on Review line chip
- 10-06T13:20 pncTudWz [ARCHIVED] Final fixes and base merge on #836 (re-time) 
    merged baihe-sub, all tests pass (570 Python, 1828 vitest, 11 Playwright)
- 10-06T13:22 VStUBfnH [RUNNING] Baihe lead (handoff 2026-10-06 afternoon) 
    #911 review said FIX FIRST; fixing 3 MEDIUM + 2 LOW findings
- 10-06T13:24 KWybngwq [ARCHIVED] Live capture hardening: proxy socket tracking and deadlines 
    PR #855 merged into baihe-subtitler with CI green; live-capture hardening (container format + protocol whitelisting) complete PR #855 merged; live-capture hardening shipped
- 10-06T13:24 f8S7jwVK [ARCHIVED] Refresh roadmap backlog docs on #830 
    waiting for CI
- 10-06T13:25 nDp7aSqa [ARCHIVED] Resolve conflicts on #850 (upload limit) 
    test_oversize_422 updated to assert 'A restore can load at most' message; commit pushed to settings-upload-limit oversize test updated; 92 passed in test_api_library_admin.py
- 10-06T13:46 qe9qzbAb [ARCHIVED] Resolve conflicts on #836 (re-time), round 2 
    Merge commit pushed to step-retime-lines: docs/remote-access-decision.md conflict resolved (kept base rows, re-added retime paths); backend 511 passed, frontend tsc+vitest clean PR #836 merged into baihe-subtitler; conflicts resolved; tests pass
- 10-06T14:05 XegXATck [ARCHIVED] Resolve conflicts on #836 (re-time), round 3 
    PR #836 merged with latest base (ddeb838..bf7ac3e); LinesPanel.tsx conflict resolved (onRetimeSelected + lazy Waveform); 240 Python tests + tsc + 1867 vitest tests pass merged baihe-subtitler into step-retime-lines; LinesPanel conflict resolved; tests green
- 10-06T14:12 HkyLhH2W [ARCHIVED] Live capture follow-ups: https header deadline, test robustness, ffmpe **NOTE**
    PR #857 merged; flagged ffmpeg CI gap, unprotected video exports, and .webm subtitle export failures as follow-ups PR #857 merged into baihe-subtitler; left 3 follow-ups
- 10-06T14:19 6tS7GJQo [ARCHIVED] Resolve conflicts on #856 (keep-originals follow-ups) **NOTE**
    merged followups-keep-originals-en-cleanup into baihe-subtitler (commit 718e083); resolved media_upload_service.py conflicts; 366 backend + 1863 frontend tests pass PR #856 merged into baihe-subtitler; conflicts resolved; tests pass
- 10-06T14:41 PXDAEs4G [ARCHIVED] Keep-originals LOW follow-ups: recovery age gate, case-insensitive nam **NOTE**
    PR #858 merged; keep-originals LOW follow-ups complete PR #858 merged into baihe-subtitler; all 3 LOW follow-ups in
- 10-06T14:46 juz5qPN5 [ARCHIVED] Review: wrap the flag note onto several lines instead of clipping it 
    PR #860 merged into baihe-subtitler; flagged-line chip text now wraps PR #860 merged; flagged-chip wrap fix shipped
- 10-06T14:49 9fhrB8oQ [ARCHIVED] Soft-subtitle export: keep a container that accepts the source's codec 
    PR #859 merged into baihe-subtitler; soft-subtitle export from .webm now outputs .mkv PR #859 merged; .webm soft-sub export now .mkv
- 10-06T14:52 VqXoKFfr [ARCHIVED] Step 165: keep Whisper word timestamps and cut long lines at real paus 
    PR #861 merged into baihe-subtitler. Whisper word timings now preserved; over-long lines cut at real pauses with accurate times. Owner should re-transcribe 7:13 clip with large-v3-turbo to verify. PR #861 merged; word timestamps kept during transcription
- 10-06T15:05 16Qzyih1 [ARCHIVED] Review leftovers: chunked-body deadline, legacy input formats, Windows **NOTE**
    PR #862 merged; follow-ups: add defusedxml to requirements-core.txt; rebase #859 if it lands next PR #862 merged to baihe-subtitler; defusedxml & #859 rebase noted
- 10-06T17:16 mXcK79Fa [ARCHIVED] Resolve conflicts on #859 (soft-subtitle .mkv) 
    PR #859 merged with #857: container choice + format whitelist, 485 tests green merged #857 into softsub-webm-container, tests pass (485)
- 10-06T17:22 u3d7fpde [ARCHIVED] Small leftovers from the #861 and #862 reviews **NOTE**
    PR #863 (review-leftovers-861-862) merged into baihe-subtitler PR #863 merged into baihe-subtitler
- 10-06T17:29 E8TyPeny [ARCHIVED] Allow min silence down to 100 ms 
    PR #864 merged into baihe-subtitler; min_silence_ms range now 100–3000ms (default 300) PR #864 merged: min-silence-floor down to 100ms
- 10-06T17:31 EMXySRJo [ARCHIVED] Save Whisper word timings per line so re-split and AI split cut at rea 
    PR #865 CI green (7/7); awaiting review
- 10-06T17:36 XZ7jPut8 [ARCHIVED] Functionality and UX test of the app (browser, desktop and phone) **NOTE**
    issue #867 updated with recheck findings: Transcribe bug + stale counts confirmed; Upload button hint corrected; un-ticked file disables Transcribe noted issue #867 rechecked; 7 claims confirmed, 2 corrected
- 10-06T18:06 RxJSdjup [ARCHIVED] start.bat: safe auto-update from git before launch (Option A) 
    PR #866 merged into baihe-subtitler; five manual Windows edge cases documented in PR body for owner to verify PR #866 merged; start.bat auto-update live on baihe-subtitler
- 10-06T18:10 17cy8KgS [ARCHIVED] PR #865 review fixes: keep re-time edges, harden word-timing parsing,  
    PR #865 merged with word-timings fixes; 1412 tests passed, CI running at merge PR #865 merged into baihe-subtitler; all review fixes pushed
- 10-06T18:14 k6Kz4MEy [ARCHIVED] Review: one-click Undo after split, merge, delete and re-split 
    PR #868 ready for review; CI green on current head PR #868 green, awaiting reviewer; stale wait cleared
- 10-06T20:38 n3exnmT5 [ARCHIVED] PR #868 review fixes: undo must never delete notes or emotion tags **NOTE**
    PR #868 merged. Flaky retime test fix proposed in comment; defusedxml missing from requirements-core.txt (pre-existing); edit race documented in PR body. PR #868 merged into baihe-subtitler; flaky test + pre-existing failure noted
- 10-06T20:51 7jNnhj3Q [ARCHIVED] Save the transcription settings used with each run (raw transcript fil 
    settings object now saved in raw_transcript per run (PR #869 merged into baihe-subtitler) PR #869 merged; settings saved per run
- 10-06T21:06 GyTY4Z9Q [ARCHIVED] Review player: arrow-key seek, real full screen, subtitles overlaid on 
    PR #871 merged; review-player-controls integrated into baihe-subtitler PR #871 merged to baihe-subtitler; frontend review-player-controls complete
- 10-06T21:06 TZ7fzca9 [ARCHIVED] Fix: a title with no untranslated lines still shows Aligned instead of 
    PR #870 merged into baihe-subtitler: title status now advances from aligned→translated when last blank line filled PR #870 merged; fix deployed (title → translated on last blank fill)
- 10-06T21:20 Rf5Wp4Qu [ARCHIVED] Reading-speed flag: stop flagging fast talkers (floor, setting, bulk c 
    PR #874 merged with short-line floor, per-title Reading speed control (Normal/Relaxed/Off), and Clear+Re-check actions. Owner should test Relaxed limits on 9.5K-line title and report if adjustment needed. PR #874 merged; reading-speed flag tuning shipped to baihe-subtitler
- 10-06T21:20 pzXKdoKu [ARCHIVED] Translation: she/her default and GL/Baihe toggles don't stop he/him (i **NOTE**
    PR #873 merged into baihe-subtitler; genre-notes and she/her toggles working; reuse across all downstream runs (glossary, fix-flagged, bulk, AI improve, CLI); 3 known gaps noted (he/him post-check, genre removal from prompt, blocked-line retry guidance) PR #873 merged; owner can now toggle genre+she/her per title
- 10-06T21:24 VQjre2pP [ARCHIVED] UX fixes: plain messages instead of raw errors (Transcribe, exports, m 
    PR #878 merged to baihe-subtitler with UX fixes: missing-package errors (B1), missing-key heading (C7), disabled Export/Dub buttons (C10), soft-subtitle help text (C11) PR #878 merged; B1, C7, C10, C11 shipped
- 10-06T21:24 WjfP4QEf [ARCHIVED] UX fixes: header counts stay stale after delete/restore; line checkbox **NOTE**
    PR #872 merged; C2 + C12 claims addressed; C5 (screenshots) and optional Tab-stop link deferred PR #872 merged into baihe-subtitler; C2 + C12 shipped
- 10-06T21:24 KU5moEzP [ARCHIVED] Replacing a video with an mp3 must retire the old video (keep it in ke 
    PR #875 merged into baihe-subtitler: video replacement now archives original video, title no longer claims source video PR #875 merged; video→audio replacement sets video aside
- 10-06T21:59 2fhRbVGp [ARCHIVED] Make the pause that splits a long line a setting (default 0.35 s) 
    PR #876 merged; pause setting (0.35s default) now in baihe-subtitler PR #876 merged into baihe-subtitler; pause setting live
- 10-06T22:11 HFGHkKzt [ARCHIVED] Follow-ups to the video-set-aside change: note accuracy, hardsub mode, 
    PR #877 merged; audio-only migration ready. Mode tuning (whisper vs unset) optional. PR #877 merged into baihe-subtitler; CI green
- 10-06T22:58 B8hisjCV [ARCHIVED] Resolve conflicts on #873 (pronoun toggles per title) 
    merged baihe-subtitler→fix-pronoun-toggles (eb251d3); re-merged after base moved; awaiting CI
- 10-06T22:58 o6TFx3hE [ARCHIVED] Resolve conflicts on #878 (plain error messages) 
    merged baihe-subtitler into ux-plain-error-messages, 785 tests pass; awaiting CI
- 10-06T23:03 GbN4g6vW [ARCHIVED] Undo follow-ups: refusal wording, keep word timings in snapshots, guar **NOTE**
    PR #879 merged to baihe-subtitler with undo-followups + fingerprint guard; 2 CI race bugs fixed; 4 follow-ups separated (vitest .test.ts, dangling provenance rows, defusedxml missing, parallel-run races) PR #879 merged; 8 follow-ups fixed; 4 known issues noted
- 10-07T00:30 2XKe9NCf [ARCHIVED] Qwen 3.8 27B server setup **NOTE**
    Gemma 4 31B Q6_K: 27–33 GB VRAM total (weights + KV cache); fits RTX 8000 with headroom for concurrent loads Gemma 4 31B Q6_K needs 27–33 GB VRAM; fits RTX 8000 with ~10 GB spare
- 10-07T01:30 LSakJoCX [ARCHIVED] Resolve #879 conflicts with baihe-subtitler **NOTE**
    Merged origin/baihe-subtitler into undo-followups (0be289e..e2062a9); conflict in services/restructure_service.py resolved; 464 Python + 1898 frontend vitest + tsc clean PR #879 merged into baihe-subtitler; conflict resolved; all checks pass
- 10-07T01:35 89HcBLoJ [ARCHIVED] Context diet step 1: ignore file, slim index, route table, size guard **NOTE**
    PR #880 merged into baihe-subtitler; 4-commit context-diet refactor (docs, test move, guard) complete. Follow-up: resolve_slice.py rebuild script needs updating before next merge-slice. PR #880 merged; context-diet-step-1 complete
- 10-07T06:27 Jq4jXeYh [ARCHIVED] Update #880 size allowlist after #879 merged 
    merged baihe-subtitler, updated guard, waiting on full CI
- 10-07T09:02 8xLtWoKS [ARCHIVED] Transcribe coverage check + opt-in sensitivity preset 
    Shipped speech coverage check and opt-in sensitive VAD preset to baihe-subtitler; gap thresholds may need tuning on real audio PR #881 merged; speech coverage check + sensitive preset on base
- 10-07T09:12 5ZYZrxmm [ARCHIVED] ASR alignment for CJK languages 
    PR #882 merged: hallucination guard off, repeat-guard toggle added, Qwen3 default for Chinese/Japanese; untested on real audio. PR #882 merged to baihe-subtitler; Qwen3 default for zh/ja
- 10-07T13:25 mK47fB4n [IDLE] Translate by sentence, keep short subtitle lines 
    PR #883 conflict fixed (1f37b68); waiting on CI to report
- 10-07T13:26 H7dPUd2F [ARCHIVED] Resolve #882 conflicts after #881 merged 
    conflict resolved; PR #882 rebased and pushed full-suite CI passing; e733e02 pushed to branch
- 10-07T18:09 i5h7C8HM [ARCHIVED] Fix review findings on #882 **NOTE**
    Pushed bdb1ada to PR #882 branch; full suite 11,468 passed, 1 known defusedxml failure unrelated to the change re-transcribe follow-up pushed to PR #882 as bdb1ada
- 10-07T18:30 ujUsVY3a [ARCHIVED] Repo map for small-context models: symbol index, READMEs, task checkli 
    PR #884 merged into baihe-subtitler, adding tools/repo_map.py, package READMEs, and docs/small-model-checklist.md to main; 19:50 UTC check-in cancelled PR #884 merged; repo map, READMEs, checklist on main
- 10-07T18:35 gBBKroSH [ARCHIVED] Add Gemma 4 tags to the Ollama model picker 
- 10-07T18:40 ZonzVRcs [ARCHIVED] Add Gemma 4 tags to the Ollama model picker (12b, 26b, 31b) **NOTE**
    Gemma 4 12b, 26b and 31b added to the Ollama picker in merged PR #885; not yet tested against a real Gemma model. PR #885 merged to baihe-subtitler; Gemma 4 models added
- 10-07T18:44 s6DCkszz [ARCHIVED] Unload Ollama models before GPU transcription 
- 10-07T18:45 N28BiQQy [ARCHIVED] Free Ollama GPU memory before transcribing, with a notice 
    PR #887 CI green on f3808313; awaiting review
- 10-07T19:01 xmZM36mN [ARCHIVED] Settings: show what models are loaded (Ollama, app, GPU memory) **NOTE**
    Draft PR #886 (58c25cd7) for the Loaded now settings panel; CI green, awaiting review PR #886 draft; CI green on 58c25cd7, awaiting review
- 10-07T19:04 6YGVGarU [ARCHIVED] Add a BS-RoFormer vocal separation option 
- 10-07T19:04 Wzg3vFWg [ARCHIVED] Update Claude tier defaults to Sonnet 5.5 and Opus 5.5 **NOTE**
    PR #889 draft: all 7 CI checks green on b36847e, ready to merge into baihe-subtitler; Opus 5.5 Release thinking-token risk still needs a real-key run PR #889 CI green (7/7); draft ready for lead to merge
- 10-07T19:07 6Mjzb2FC [ARCHIVED] Burned-in subtitle OCR: language-aware backends, Tesseract as fallback 
    waiting for you to review. waiting for you to review.
- 10-07T19:20 eECdrBBY [ARCHIVED] Feasibility: move Qwen3-ASR/aligner to native transformers 5.13+ model **NOTE**
    Feasibility report delivered: 5.x migration needs real-audio and GPU parity tests before choosing the default; no repo edits, commits or installs. read-only feasibility report delivered; no repo edits
- 10-07T19:45 pHYDub4Z [ARCHIVED] Remove Edge TTS, Piper and F5-TTS from dubbing and narration **NOTE**
    PR #892 merged into baihe-subtitler, removing Edge TTS, Piper and F5-TTS; OmniVoice on your PC needs transformers 5.3+. PR #892 merged to baihe-subtitler; Edge/Piper/F5 removed
- 10-07T19:52 pSFxfZvW [ARCHIVED] Move Qwen3-ASR and the aligner to native transformers 5.13+ models **NOTE**
    #891 CI green (7/7); waiting on review events; still draft
- 10-07T20:16 Fu2s7VWp [ARCHIVED] Local coder setup: AGENTS.md, opencode.json, launcher script 
    Launcher, web UI, and local model work; start with start-local-coder.bat in E:\\U00. launcher, web UI, and local model confirmed working
- 10-07T20:40 NVbcTK2r [ARCHIVED] Remove NLLB and Qwen from local translation; gemma4:12b default 
- 10-07T20:41 3Tz36wdb [ARCHIVED] Remove NLLB and Qwen from local translation; gemma4:12b default **NOTE**
    NLLB removal draft PR #895 (commit c05936c); 35 of 37 suite failures fixed, remaining 2 unrelated or flaky; CI to confirm PR #895 fixed 35 of 37 test failures; CI to confirm
- 10-07T20:59 RZmkHEop [ARCHIVED] Translate picker: custom Ollama tag field 
- 10-07T21:02 kSbyVptR [RUNNING] Fix review blockers on native Qwen3-ASR (#891) **NOTE**
    Qwen3-ASR native path and forced aligner checked on transformers 5.15.0 and 5.19.0 with random-weight models; real-weight parity run left to the user, and vocabulary hint deferred to a follow-up. PR #891 fixes pushed; qwen3 native checked on tf 5.15/5.19
- 10-07T21:06 YWp9P7o7 [ARCHIVED] Remove TADA and Chatterbox voice engines 
- 10-07T21:10 GNCK4xZr [ARCHIVED] ASR backend: info (i) explaining the options 
- 10-07T21:13 vp43ydhu [ARCHIVED] Remove TADA, Chatterbox and GPT-SoVITS voice engines 
    I'll respond if any review comments or CI changes come in.
- 10-07T21:13 u4BNZHMA [ARCHIVED] Transcribe: info (i) for ASR backend and Groq **NOTE**
    Added (i) info icons to Transcribe stage via the Translate AI-engine component; PR #896 merged. Open follow-up: tap-to-open bug in shared Field. PR #896 merged into baihe-subtitler; CI green on all checks
- 10-07T21:13 zjtTqgrs [ARCHIVED] Export: collapsible video and audio export types 
    PR #894 merged into baihe-subtitler: Export media blocks (Video and audio, etc.) now collapsible, with state remembered; tests green, session unsubscribed PR #894 merged; collapsible export blocks now in baihe-subtitler
- 10-07T21:22 XAxnH5Zu [ARCHIVED] Remove MOSS-Transcribe-Diarize 
    I'll act when CI results or reviews arrive.
- 10-07T21:36 FPyFvSY3 [ARCHIVED] Manga OCR: native PaddleOCR-VL loader for transformers 5.x **NOTE**
    PR #898 merged into baihe-subtitler; extract_text_paddle_vl_manga now uses transformers' native paddleocr_vl. Real-page quality, speed, and VRAM are unverified. PR #898 merged; PaddleOCR-VL loads natively, unverified on manga
- 10-07T21:44 aHuyaVQ4 [ARCHIVED] Benchmark page: collapsible sections and a how-to-use guide 
    I'll keep an eye on the new CI run for PR #900.
- 10-07T21:45 2TZ1yJ5o [IDLE] Diagnostics: add PaddleOCR to the Scanlate install task **NOTE**
    PR #903 CI green on current head, still draft; merge left to lead session per CLAUDE.md PR #903 CI green, still draft; lead session merges
- 10-07T21:50 k3XP4U6E [ARCHIVED] Diagnostics installs must honour constraints.txt **NOTE**
    PR #902 merged into baihe-subtitler: installs pass -c constraints.txt so av<19 applies; flaky update-service test left open PR #902 merged; constraints.txt now applied to pip installs
- 10-07T21:52 EY6PAVCg [ARCHIVED] Live page: model picker for the Ollama engine 
    Live model picker merged via PR #904 into baihe-subtitler; CLI has no live command, so no --model added PR #904 merged into baihe-subtitler; CI green, unsubscribed
- 10-07T21:54 E38Ce51f [ARCHIVED] Live page: video autoplay and initial video delay **NOTE**
    draft PR #901 open; 7 CI checks running, will watch
- 10-07T21:55 pt7Ae1WT [ARCHIVED] Resolve merge conflicts on #895 (NLLB/Qwen removal) 
    PR #895 merged (NLLB removed, gemma4:12b default); no further action PR #895 merged; unsubscribed, nothing further pending
- 10-07T21:56 Y4eReKCb [ARCHIVED] Live job: Cancel/Stop does not stop a stuck step 
    I'll act when one of those events wakes the session.
- 10-07T21:57 s2VxtvGb [ARCHIVED] Fix ffprobe/subprocess output decoding on Windows (stdout None) **NOTE**
    Draft PR #906 into baihe-subtitler fixes subprocess UTF-8 decoding; all six CI checks green, no review comments yet. draft PR #906 open, CI green; ready for lead to merge
- 10-07T21:58 2eNPyBSc [ARCHIVED] Fix (i) help closing right after a phone tap 
    PR #905 merged: (i) tap now opens and stays open, second tap closes. Not verified on a real iOS/Android browser. PR #905 merged to baihe-subtitler; (i) tooltip fix in
- 10-07T22:02 xFTCwL1T [ARCHIVED] Resolve merge conflicts on #897 (TADA/Chatterbox/GPT-SoVITS removal) 
    PR #897 has baihe-subtitler merged and pushed (one conflict in vram_service.py); 3 newer base commits untested, CI on head unchecked merged baihe-subtitler into #897; one conflict; pushed
- 10-07T22:02 UqMvSBjC [ARCHIVED] Resolve merge conflicts on #887 (Ollama unload before transcribe) **NOTE**
    Merged baihe-subtitler into PR #887 (pushed as 1070bab); local tests pass except the known defusedxml failure; CI not yet checked. merged baihe-subtitler into #887 at 1070bab; CI unchecked
- 10-07T22:02 FtUAX2F4 [ARCHIVED] Resolve merge conflicts on #886 (Loaded now panel) 
    Merged baihe-subtitler into settings-loaded-models (4ac53cf) and pushed; backend, vitest, and Playwright loaded-models specs pass. merged baihe-subtitler into #886; tests green, pushed
- 10-07T22:16 PoMgsq2M [ARCHIVED] Resolve merge conflicts on #899 (remove MOSS) 
    remove-moss merged with baihe-subtitler at 4090e1a; CI running
- 10-07T22:26 7hb3gqnd [ARCHIVED] Fix flaky live-video e2e: unMute vs seekTo order 
    I'll pick it up when CI or a review arrives.
- 10-07T22:32 iGrdNv47 [ARCHIVED] Engine descriptions: one short line each 
    I'll act when CI or a review arrives.
- 10-07T22:33 8EBSKPew [ARCHIVED] Resolve merge conflicts on #902 (installs honour constraints) **NOTE**
    Merged latest baihe-subtitler into install-honours-constraints (ac41a83), resolved size-ceiling conflict, tests run; only known defusedxml failure remains. merged baihe-subtitler into #902, pushed ac41a83, commented
- 10-07T22:42 1R9x1pbE [ARCHIVED] Resolve merge conflicts on #897 again (after #895 and #904) **NOTE**
    PR #897 merged (83f05df) after checks passed; nothing left pending PR #897 merge pushed as 83f05df after checks passed
- 10-07T23:25 oC9xfKLB [ARCHIVED] Resolve 3 remaining conflicts on #897 **NOTE**
    PR #897 merged into baihe-subtitler; TADA, Chatterbox and GPT-SoVITS engines removed PR #897 merged into baihe-subtitler; nothing left to do
- 10-08T00:09 oL68D4JG [IDLE] Diagnostics: real smoke test 
    I'll stay quiet on further base-merge CI passes unless something fails or a reviewer comments.
- 10-08T00:09 qhbuBgGb [IDLE] Benchmark: build set from reviewed titles + judge **NOTE**
    PR #910 (draft) is green and mergeable on ae4c180; marking ready and merging is left to the user or lead PR #910 green on ae4c180 with judge hardening; still draft
- 10-08T00:18 NirEKvjh [ARCHIVED] Resolve conflicts: #887 ollama-unload-before-transcribe **NOTE**
    Merged origin/baihe-subtitler (incl. #897) into ollama-unload-before-transcribe and pushed; pytest 11510 pass, 1 env-only failure (defusedxml). merged base into #887 branch, pushed; 1 env-only test fails
- 10-08T00:18 BaVp6hqi [ARCHIVED] Resolve conflicts: #899 remove-moss 
    restoring remove-moss to merge commit, then pushing
- 10-08T00:18 PfWLnWK8 [ARCHIVED] Resolve conflicts: #902 install-honours-constraints **NOTE**
    Merged baihe-subtitler (#897) into install-honours-constraints, resolved 2 conflicts, and pushed. defusedxml missing from requirements-core.txt fails one test. PR #902 merged with base #897; 1 unrelated test fails
- 10-08T00:46 q8vdhCRL [ARCHIVED] Resolve conflicts (2): #899 remove-moss **NOTE**
    Merged origin/baihe-subtitler (#887) into remove-moss and pushed; the one failing Python test also fails on the base branch. merged #887 into remove-moss and pushed; 1 pre-existing fail
- 10-08T01:24 FhjVZ79r [ARCHIVED] Wave 0: split guards (new test file) 
    waiting on CI
- 10-08T01:24 aN6qFc4Y [ARCHIVED] Wave 0: AGENTS.md where-to-look + test commands **NOTE**
    Docs-only PR #914 merged into baihe-subtitler with CI green; 32K vs 64K conflict in opencode.json and start-local-coder.ps1 left for the user. PR #914 merged into baihe-subtitler; CI green
- 10-08T01:24 4CdHrZkV [ARCHIVED] Split: translation_guide.py 
    I'll respond when CI results or reviews come in.
- 10-08T01:25 X2Q3wxYb [ARCHIVED] Split: dub.py (narration) 
    split-dub PR #921 open; waiting on CI before checking
- 10-08T01:25 v1hAy6GB [IDLE] Split: services/glossary_service.py **NOTE**
    PR #919 draft: conflict fixed, pushed; waiting on CI
- 10-08T01:25 wMGVXBhm [ARCHIVED] Split: sources/adaptive.py 
    PR #928 unchanged; still watching it, rerun reported
- 10-08T01:25 9daxYt4N [IDLE] Split: LinesPanel.tsx **NOTE**
    PR #920 all 7 checks green; still draft, awaiting merge Mark PR #920 ready for review and merge it (1 unread PR comment)
- 10-08T01:25 b3aaBqMs [IDLE] Split: reviewLogic.ts + sourcesFormat.ts 
    PR #917 split pushed; awaiting CI, will respond to reviews
- 10-08T01:25 mCWa95Vm [ARCHIVED] Resolve conflicts (3): #902 install-honours-constraints **NOTE**
    Merged baihe-subtitler into install-honours-constraints and pushed; 11500 pytest passed, 1 failed (defusedxml missing from requirements-core.txt). PR #902 merged with base, pushed; defusedxml test fails
- 10-08T01:39 zZ77gyc2 [ARCHIVED] Resolve conflicts: #908 live-cancel-responsive **NOTE**
    Merged baihe-subtitler into live-cancel-responsive (8b2e23e). tsc clean, vitest 1930 pass, desktop Playwright 8 pass. Backend: 2 fails, one pre-existing (defusedxml). PR #908 conflict resolved, pushed 8b2e23e; 2 backend fails
- 10-08T01:42 FFKw2S6Q [ARCHIVED] Remove the Notion export integration **NOTE**
    draft PR #924 open; waiting on CI and reviews
- 10-08T01:53 wECMYJGN [ARCHIVED] Comments-only: remove old roadmap/step ids (one area per PR) 
    PR #927 comments-only cleanup; waiting on CI before PR 2
- 10-08T01:53 LxKNNKA4 [ARCHIVED] Docs: trim asr-experiments.md (verify word for word) 
    I'll act if a check fails or a review comment comes in.
- 10-08T01:53 PF5AJUPk [ARCHIVED] Docs: shorten README.md and windows-installer-design.md 
    I'll act when CI results or reviews arrive.
- 10-08T01:53 TX49oZdh [ARCHIVED] UI text audit: shorten wording, one area per PR **NOTE**
    PR #926 CI queued (7 checks); will act on failure or review
- 10-08T02:01 zfxxuqRt [ARCHIVED] Decode subprocess output as UTF-8 everywhere 
    Set explicit UTF-8 on run_ffprobe and 3 nvidia-smi calls, added an AST guard and decoding tests; focused tests pass (258). Windows decoding fix: 4 sites set encoding; AST guard added
- 10-08T02:12 SQYDPDXv [ARCHIVED] Library PR A: short stats line, usage folded **NOTE**
    draft PR #933 open; next checking CI on current head
- 10-08T02:12 UgtzLGfJ [ARCHIVED] Library PR B: compact Continue shelf on phone 
    PR #931 CI queued (7 checks); subscribed, will act on results
- 10-08T02:12 a8GQE9T9 [ARCHIVED] Library PR C: row cards at every width **NOTE**
    draft PR #932 open; 7 CI checks queued, watching PR
- 10-08T02:15 TDqamZH3 [IDLE] Review flags: tune pacing and gap flags for fast streamer speech **NOTE**
    PR #937 CI green on c2c8755 but still a draft; not merged. Real-VOD before/after counts still need checking. PR #937 CI green at c2c8755; still draft, unmerged
- 10-08T02:15 Kcr8uKpy [IDLE] Hide novel source sections for non-novel titles **NOTE**
    PR #935 CI pending; test failure is on base; watching
- 10-08T02:17 HQYtWoSi [IDLE] Diagnostics: opt-in \ **NOTE**
    PR #938 CI green, still draft; awaiting go to mark ready confirm marking PR #938 ready for review
- 10-08T02:17 qLmgRBSw [IDLE] Resolve conflicts: #903 scanlate-add-paddleocr **NOTE**
    PR #903 CI green, still draft; awaiting go to squash-merge confirm: mark PR #903 ready and squash-merge it?
- 10-08T02:18 Fgkm1MjM [ARCHIVED] Fix flaky test_start_download_thread_reports_verified 
    start_download() snapshots status before the worker thread starts, with a regression test; merged via PR #939 start_download race fixed; PR #939 merged to baihe-subtitler
- 10-08T02:23 H8D6KHt2 [ARCHIVED] Move EXPECTED_TOP_LEVEL_FILES out of diagnostics.py; FILE_ORGANIZATION 
    PR #936 merged into baihe-subtitler: EXPECTED_TOP_LEVEL_FILES moved to expected_files.py; final CI clean. PR #936 merged into baihe-subtitler; final CI clean
- 10-08T02:27 fExpk1WG [ARCHIVED] Docs: AGENTS.md rewrite, checklist brief template, two CLAUDE.md addit 
    PR #934 open; CI running, will act on results
- 10-08T02:45 w1aBUp1m [ARCHIVED] fix-e2e-windows-failures 
    Merged PR #940 (Windows e2e fixes: CRLF, UTF-8, yt-dlp mock, route.fetch GET) into baihe-subtitler PR #940 merged into baihe-subtitler; CI green on final head
- 10-08T02:47 YXgw2ZYs [IDLE] step-asmr-vad **NOTE**
    PR #942 is green on d54abc2... 21788cd and mergeable; still a draft for the lead session to merge PR #942 green, mergeable; still draft, lead to merge
- 10-08T02:49 XDS4KiNd [ARCHIVED] research-oss-gap-report 
    6 stage-1 research agents running; pulling ASR topic pages
- 10-08T02:53 5uJadkMw [ARCHIVED] research-oss-gap-report **NOTE**
    PR #941 CI green; docs-only draft awaiting lead merge
- 10-08T02:56 nQodgYXK [ARCHIVED] fix-library-stats-short-e2e **NOTE**
    Pushed 69e0f15 to library-stats-short making library-b3 order-independent; CI on PR #933 has not run on it yet. pushed 69e0f15 making library-b3 order-independent; CI unverified
- 10-08T03:09 hvmGGRWK [ARCHIVED] fix-ui-text-audit-settings-e2e 
    Pushed f58095f to ui-text-audit-settings widening the .field-item button gap to 8px; phone spec passes 15/15 locally, but the original CI overlap was never reproduced. pushed f58095f: 8px field gap fixes phone spec overlap
- 10-08T03:34 36hXPzfW [IDLE] step-db-0-guards-and-rename 
    waiting on CI
- 10-08T03:34 XHCBnarq [ARCHIVED] resolve-split-dub 
    Merged origin/baihe-subtitler into split-dub; full suite green (11548 passed), PR #921 description rewritten PR #921 merge resolved; full suite passes, description updated
- 10-08T03:34 y1br4KsB [ARCHIVED] resolve-split-translation-guide 
    merged base into split-translation-guide; full suite ~8% done
- 10-08T03:49 dAUVSSs3 [ARCHIVED] fix-docs-trim-asr-experiments-review **NOTE**
    Applied 8 review fixes to docs/asr-experiments.md on docs-trim-asr-experiments after merging baihe-subtitler (7bad622); tests pass. Push not confirmed in tail. applied 8 review fixes to PR #922 docs; 2 optional skipped
- 10-08T03:49 UWr2resy [ARCHIVED] fix-docs-trim-readme-installer-review **NOTE**
    Applied the reviewer's fixes to PR #925 docs (README trim, Windows installer doc); 1103 tests passed, 8 xfailed; posted a PR comment noting the rejected update claim and the unedited playwright and OmniVoice licence items. PR #925 review fixes applied; 1103 tests pass
- 10-08T03:51 qfxUuSop [ARCHIVED] revise-split-sources-adaptive **NOTE**
    Pushed 5273516 to split-sources-adaptive for PR #928 (follow_novel moved to novel_follow.py), description updated; stray /ast_check.py needs manual removal. PR #928 revised and pushed; full suite 11549 passed
- 10-08T03:56 poRdoGfj [IDLE] resolve-benchmark-from-reviewed 
    Merged origin/baihe-subtitler into benchmark-from-reviewed and pushed; pytest 11612 passed, tsc clean, vitest 1953 passed merged baihe-subtitler into PR #910 branch; pushed, checks green
- 10-08T04:08 EgaqdJCK [ARCHIVED] revise-split-translation-guide 
    Pushed split-translation-guide to PR #923 with glossary code moved to glossary_io; importers updated, full suite passed before a final docstring trim, CI not checked. PR #923 revised and pushed; glossary imports moved to glossary_io
- 10-08T04:16 Exa1S6eV [ARCHIVED] resolve-docs-trim-readme-installer 
    Resolved the docs/README.md conflict on PR #925, merged latest baihe-subtitler, pushed c2f87bc..5f54ed1; targeted tests pass (187 + 605). merged baihe-subtitler into PR #925; pushed 5f54ed1; tests pass
- 10-08T04:17 c3yBECS5 [ARCHIVED] revise-split-dub 
    Moved 14 narration defs to dub_narration.py (pure move, except build_track_subprocess_worker signature), merged origin/baihe-subtitler; full suite 11549 passed. narration moved to dub_narration.py; full suite green
- 10-08T04:21 HmCcgVdD [IDLE] step-subtitle-import 
    PR #952 (subtitle import) merged into baihe-subtitler; preview and apply are now local_only, not lines.edit. PR #952 merged; preview/apply now PC-only via local_only()
- 10-08T04:23 i9eXX15F [ARCHIVED] docs-engine-backends-experimental-name 
    PR #944 merged; docs/engine-backends.md fix is now in baihe-subtitler PR #944 merged; engine-backends.md doc fix landed
- 10-08T04:27 AiLP6vcH [ARCHIVED] fix-phone-spacing-spec-wait-for-data 
    PR #958 merged into baihe-subtitler; phone-spacing spec waits for async sections, 150/150 repeat runs pass PR #958 merged; spec now waits for async sections
- 10-08T04:27 gZQnivXy [ARCHIVED] ui-text-audit-2-diagnostics **NOTE**
    Draft PR #949 on ui-text-audit-diagnostics; CI green (7/7); before/after table in description; ready to merge when you choose draft PR #949 up; CI green, 7/7 checks passed
- 10-08T04:29 U2HCsi4X [ARCHIVED] fix-amix-normalize-video-export 
- 10-08T04:29 S1XzKQGW [IDLE] step-stereo-b1-dsp-library **NOTE**
    draft PR #947 open; suite green, watching for CI/reviews
- 10-08T04:30 14y2YJh5 [ARCHIVED] research-oss-addendum-smartsub-voicepro 
    Addendum on SmartSub and Voice-Pro merged via PR #945 into baihe-subtitler; CI was green. PR #945 merged; SmartSub/Voice-Pro addendum landed
- 10-08T04:32 jJsu6fyk [IDLE] fix-benchmark-judge-cost-safety 
    Security-review fixes applied and pushed to PR #910's branch benchmark-from-reviewed after merging origin/baihe-subtitler; no new PR, no force-push. PR #910 security fixes pushed to benchmark-from-reviewed; tests green
- 10-08T04:35 WTbakBQH [ARCHIVED] docs-streamlit-cleanup **NOTE**
    Streamlit docs cleanup merged as PR #946 into baihe-subtitler; four follow-ups remain open. PR #946 merged; Streamlit docs cleanup complete
- 10-08T04:36 7t4H8X2w [ARCHIVED] step-spend-history 
    Spend history card merged into baihe-subtitler via PR #951; session unsubscribed PR #951 merged; Spend history card now on baihe-subtitler
- 10-08T04:37 oZGyqm1X [ARCHIVED] resolve-remove-notion 
    Merged origin/baihe-subtitler into remove-notion, kept the Notion removal, and kept #926's Settings text; full suite, vitest, tsc and Playwright pass. PR #924 conflicts resolved; full suite and Playwright pass
- 10-08T04:43 htsnXDjJ [ARCHIVED] hide-discover-catalogue-tab 
    Merged PR #950 into baihe-subtitler; Catalogue tab hidden via CATALOGUE_TAB_ENABLED in discoverFormat.ts PR #950 merged; Discover Catalogue tab hidden behind flag
- 10-08T04:44 EDgFkGb6 [ARCHIVED] fix-browser-status-wording 
    Merged PR #955 fixing the Sources browser-not-installed wording; when #938 lands, its row should also require the Playwright package. PR #955 merged; browser status wording fix landed
- 10-08T04:44 afqmJ4dL [ARCHIVED] fix-installer-uninstall-env-and-comment 
    PR #953 merged into baihe-subtitler; CI green, local suite passed. Compile installer/baihe.iss in Inno Setup on Windows before next release. PR #953 merged; installer change not compiled (no Windows)
- 10-08T04:50 wxwcL5v9 [IDLE] step-source-pacing-profiles **NOTE**
    PR #956 (step-source-pacing-profiles) merged into baihe-subtitler; the defusedxml test failure predates it. PR #956 merged into baihe-subtitler by zkaelxz
- 10-08T04:56 WRkmKCPq [ARCHIVED] sources-picker-explain-filter 
    PR #954 merged; the 'Import into' picker fix for Bilibili Manga links is now on baihe-subtitler. PR #954 merged; Import-into picker fix on baihe-subtitler
- 10-08T04:57 u8SCMr5K [ARCHIVED] fix-no-pages-error-names-browser-tier **NOTE**
    Draft PR #964 on fix-no-pages-error-cause: Bilibili no-pages error now names the missing browser step; tests added, CI clean, ready for lead merge. draft PR #964 for Bilibili no-pages error; CI clean
- 10-08T04:58 E1yWZ9u4 [ARCHIVED] fix-signed-in-fetch-scrolls 
    PR #960 CI green on re-run; watching for reviews
- 10-08T05:02 rYw9oTub [ARCHIVED] step-source-extension-only-marker 
    PR #967 CI green; awaiting reviewers, still subscribed
- 10-08T05:03 3Mz7bokH [ARCHIVED] vet-twmanga-and-docs-note 
- 10-08T05:04 x2W8AFjw [ARCHIVED] restyle-extension-popup **NOTE**
    Draft PR #959 restyles the browser extension popup; CI (test, frontend, e2e) green, awaiting visual review before merge. popup restyle draft PR; CI green, ready for look review
- 10-08T05:07 7KK35Tk5 [ARCHIVED] fix-extension-batch-12-images 
- 10-08T05:10 caxdZPXQ [ARCHIVED] comic-titles-open-in-scanlate **NOTE**
    Draft PR #961 for comic stage notice in WorkspaceShell; all 7 CI checks green; lead session merges per CLAUDE.md PR #961 draft open; CI 7/7 green, no review comments
- 10-08T05:14 k5Md59JX [ARCHIVED] novel-raw-chapters-panel **NOTE**
    Draft PR #968 for novel-raw-chapters-panel; all 7 CI checks green, no conflicts, awaiting reviewer. draft PR #968 green on a82d29c; awaiting reviewer
- 10-08T05:16 c79LFey5 [ARCHIVED] move-saved-manga-out-of-nav **NOTE**
    Draft PR #966 against baihe-subtitler removes Saved manga from nav; CI green on 14742f4 (7/7 checks). draft PR #966 CI green on 14742f4, all 7 checks pass
- 10-08T05:17 SjpDXbK4 [ARCHIVED] extension-use-app-icon 
- 10-08T05:19 W1ZU8WYS [ARCHIVED] comments-only-notion-streamlit-leftovers **NOTE**
    Draft PR #965 for comments-leftovers-notion-streamlit: comment-only cleanup, all six CI checks green, no review comments, ready for merge. draft PR #965 open; all six CI checks green
- 10-08T05:19 N2caHL4z [ARCHIVED] remove-missing-tabs-field 
    missing_tabs removal paused; need remove-now vs two-release call Tell me which: option 1 (remove now) or option 2 (two releases)
- 10-08T05:20 pUfg97Rs [ARCHIVED] step-remote-extension-1-device-tokens 
    PR #969 CI green, no reviews; next check 11:03 UTC
- 10-08T05:24 EeqjXQtz [ARCHIVED] comic-chapters-in-reader 
    PR #970 merged into baihe-subtitler: chapter selector, chapter dividers, per-chapter translate, and chapter-aware Sources import; old imports stay 'Chapter unknown' until re-imported. PR #970 merged into baihe-subtitler; chapter reader shipped
- 10-08T05:28 Ezk9Zb35 [ARCHIVED] step-gpu-headroom-setting 
    I'll tell you where they differ from the brief before I design anything.
- 10-08T05:32 KBtsbssj [ARCHIVED] step-ollama-cloud-support **NOTE**
    Ollama cloud models added for translation; draft PR #972 open on step-ollama-cloud-support, CI green draft PR #972 open; CI green on all jobs incl. e2e
- 10-08T05:37 DqZpoMCF [ARCHIVED] docs-local-coding-agent-readiness **NOTE**
    Local-coding-agent docs commit fe930dab on docs-local-coding-agent-readiness, not yet pushed push denied for zkaelxz/U00; awaiting approval or manual push Approve push access to zkaelxz/U00, or run `git push -u origin docs-local-coding-agent-readiness` from /home/user/zkaelxz/u00 and open the draft PR into baihe-subtitler
- 10-08T05:39 NdLZ5FUK [ARCHIVED] docs-update-server-safely **NOTE**
    Then I'll open the draft PR into `baihe-subtitler`.
- 10-08T05:48 B7vbePL3 [ARCHIVED] extension-result-line-where-pages-went 
    Baihe repo not in session; need repo confirmed to start Confirm whether zkaelxz/U00 is the Baihe repo and approve attaching it, or give the owner/repo for Baihe
- 10-08T05:50 zQgZRaRH [ARCHIVED] fix-disk-usage-linked-folder-sizes **NOTE**
    PR #971 is a draft with CI green on d8f12d8; security review still outstanding PR #971 CI green on d8f12d8; Opus security review not run run the Opus security review on PR #971, or request a human review
- 10-08T05:56 HkEDT3o6 [ARCHIVED] fix-live-video-delay-seek 
    Live repo not attached; push-access attach denied, need repo choice Approve attaching zkaelxz/U00 with push access, name the correct repo (owner/name), or start a session with the repo already attached
- 10-08T05:56 6xXjLHZH [ARCHIVED] resolve-908-live-cancel-conflicts 
    PR #908 repo not attached; awaiting repo access or owner/repo Approve add_repo for zkaelxz/U00 with push access, or give the correct owner/repo for PR #908 (baihe-subtitler)
- 10-08T06:18 mL7ZVmsL [ARCHIVED] step-twmanga-adapter 
    no baihe-subtitler repo in session; need owner/repo Give the GitHub owner/repo containing the baihe-subtitler branch. Confirm zkaelxz/U00 or attach the correct repo (enable the GitHub app for it if private).
- 10-08T06:21 WDqjdKVn [ARCHIVED] chore-remove-unused-agents **NOTE**
    Then I'll open a draft PR.
- 10-08T06:22 VmqZL5m2 [ARCHIVED] chore-add-three-agent-roles **NOTE**
    Baihe repo not in session; needs confirm or attach Confirm zkaelxz/U00 is the Baihe repo and approve attaching it (read-only or push), or point me to the correct repo / attach it to the session
- 10-08T06:23 oBR5aK1R [ARCHIVED] resolve-955-browser-status-wording 
    PR #955 repo not attached; add_repo denied, awaiting you Approve attaching zkaelxz/U00 with push access if it's the right repo, or give the correct owner/repo for PR #955 (or attach it to the session)
- 10-08T06:23 QuEvBpBr [ARCHIVED] resolve-951-spend-history **NOTE**
    step-spend-history pushed at 86449597; PR #951 remains a draft, no action needed step-spend-history pushed at 86449597; PR #951 still draft
- 10-08T06:23 8GhQQiZV [ARCHIVED] resolve-956-source-pacing-profiles 
    Branch tip b126ec4 is pushed and in sync; the 120-commits-ahead warning came from a stale local origin ref, now refreshed. PR #956 branch in sync; false ahead-count was stale local ref
- 10-08T06:27 BWYUBZXL [ARCHIVED] extension-capture-whole-chapter 
    repo not attached; need correct owner/repo and push access Confirm whether zkaelxz/U00 is the baihe-subtitler repo (or give the correct owner/repo), and approve add_repo with push access or add the repo to the session yourself.
- 10-08T06:30 yoqRGYJm [ARCHIVED] fix-911-smoke-cli-and-conflicts **NOTE**
    diagnostics-smoke-test matches origin at ba4b1324; 0 local commits ahead PR #911 branch synced at ba4b1324; nothing left to push
- 10-08T06:34 fgshRKcf [ARCHIVED] resolve-952-subtitle-import 
- 10-08T06:35 SX66nrbr [ARCHIVED] fix-extension-only-note-length 
    target repo not in session; need repo choice to proceed Pick one: (1) approve attaching zkaelxz/U00 with push access, (2) give the correct owner/repo and confirm the session can access it, or (3) start a session with the repo checked out
- 10-08T06:39 Ng6p69Mw [ARCHIVED] fix-952-subtitle-import-review-and-conflicts 
    PR #952 repo not located; need repo confirmed or attached Confirm zkaelxz/U00 is the right repo and approve the push-access attach, or start the session with the correct repo attached as a source
- 10-08T06:58 j1BxM22g [ARCHIVED] fix-971-windows-device-check-and-unc 
    PR #971 repo not accessible; awaiting repo approval Approve attaching zkaelxz/U00 with push access, or tell me which repo holds PR #971 (or start a session with it checked out)
- 10-08T06:58 hbpW7SB6 [ARCHIVED] fix-972-ollama-cloud-review 
    repo not in session; attach to zkaelxz/U00 was denied Approve attaching zkaelxz/U00 with push access, or give the correct repo name / start the session from the repo with PR #972
- 10-08T06:59 DSrX1HzA [ARCHIVED] fix-comic-chapters-review-lows 
    PR #973 merged into baihe-subtitler with the three Opus security review LOW fixes; nothing further pending PR #973 merged; three LOW review fixes landed
- 10-08T07:06 mdcGzuQV [ARCHIVED] resolve-951-spend-history-round-2 **NOTE**
    Merge commit 1c6e94f resolves the route-permissions.md conflict; 11273 backend tests pass, 2 likely env-related (starlette version) failures remain. route-permissions.md conflict resolved; 2 tests fail
- 10-08T07:09 NRRuT3ji [ARCHIVED] fix-911-smoke-review-findings 
    Baihe repo not attached; need owner/repo + push access Confirm zkaelxz/U00 is the Baihe repo and allow the attach, or give the correct owner/repo and grant access, or start a new session with the repo already attached
- 10-08T07:16 LYgqLEbG [ARCHIVED] fix-assistant-tab-collapsible-sections **NOTE**
    repo not attached; asking which repo to use before branching Confirm the repo: say `zkaelxz/U00` to attach it, or attach/grant access to `baihe-subtitler`. Also confirm the branch and draft PR plan.
- 10-08T07:18 icxe9D78 [ARCHIVED] resolve-908-live-cancel-round-2 
    PR #908 resolver blocked: repo not attached, attach denied Approve attaching zkaelxz/U00 with push access, or give the correct owner/repo for PR #908
- 10-08T07:33 9BQxRtMr [ARCHIVED] resolve-908-live-cancel-round-3 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-08T07:33 5gNfrA3H [ARCHIVED] fix-911-smoke-review-findings-r2 **NOTE**
    Fixed flaky test_cli_and_app_report_the_same_checks race and pushed to diagnostics-smoke-test; PR #911 stays draft. Full suite not re-run after the fix. fixed GPU-slot race in CLI test; pushed, PR #911 still draft
- 10-08T07:33 6JB8TC4y [ARCHIVED] fix-971-windows-device-check-and-unc-r2 **NOTE**
    Pushed review fixes to fix-disk-usage-linked-sizes (PR #971, still draft). Backend 11572 passed; frontend tsc and vitest clean. PR description still needs the clear/Trash/purge/restore line. review fixes pushed to PR #971; full checks green, still draft
- 10-08T07:33 hmodrB8Y [ARCHIVED] fix-972-ollama-cloud-review-r2 **NOTE**
    Added engines.paid gate for cloud model tags (api/auth.py); fixed size-cap failure by moving the loopback check; frontend and 1960-test subset pass. engines.paid gate on cloud tags; size-cap test fixed, tests green
- 10-08T07:33 ZYxzppqk [ARCHIVED] fix-952-subtitle-import-review-and-conflicts-r2 
    Resolved #952 conflicts with baihe-subtitler and fixed review findings; backend, frontend, and Playwright suites pass. PR #952 conflicts and findings addressed; tests green
- 10-08T07:33 Hki3YsCz [ARCHIVED] resolve-955-browser-status-wording-r2 **NOTE**
    Resolved #955's 3 keep-both conflicts, merged latest baihe-subtitler, pushed fix-browser-status-wording; PR left as draft. Full suite green before the second merge. #955 conflicts resolved vs baihe-subtitler; pushed as draft
- 10-08T07:34 8r4CTQN1 [ARCHIVED] fix-extension-only-note-length-r2 
    PR #976 (extension-only note length fix) merged into baihe-subtitler; nothing further pending. PR #976 merged into baihe-subtitler; subscription ended
- 10-08T07:34 VWoXKe1m [ARCHIVED] fix-assistant-tab-collapsible-sections-r2 
    PR #974 merged into baihe-subtitler; Assistant tab categories are now collapsible like other pages. PR #974 merged; Assistant categories now collapsible
- 10-08T07:34 M61AA7Sn [ARCHIVED] extension-capture-whole-chapter-r2 
    PR #981 CI green on 1948a53; watching for reviews
- 10-08T07:34 ecq4e5so [ARCHIVED] extension-result-line-where-pages-went-r2 
    PR #979 CI green; watching for review comments
- 10-08T07:34 GrU7ap2o [ARCHIVED] step-twmanga-adapter-r2 **NOTE**
    Draft PR #978 adds the twmanga/twbzmg source adapter off baihe-subtitler; full CI suite passed on head commit. PR #978 twmanga adapter draft; CI green, awaiting review
- 10-08T07:35 UEAreb8V [ARCHIVED] fix-live-video-delay-seek-r2 **NOTE**
    PR #982 (draft) is green at 4c0a73f with all 7 checks passing; the live-delay probe fix is untested on a real YouTube stream. PR #982 CI green at 4c0a73f; draft awaiting lead merge
- 10-08T07:35 BKcBhyD3 [ARCHIVED] chore-agents-add-roles-and-remove-unused 
    Merged PR #975 (chore-agent-definitions) into baihe-subtitler; session unsubscribed from PR activity. PR #975 merged into baihe-subtitler; task complete
- 10-08T07:35 ugpDGvuJ [ARCHIVED] docs-local-coding-agent-readiness-r2 **NOTE**
    PR #983 merged into baihe-subtitler with all six CI checks green; remaining follow-ups are optional PR #983 merged into baihe-subtitler; all 6 CI checks green
- 10-08T07:35 36Yi4qZy [ARCHIVED] docs-update-server-safely-r2 **NOTE**
    Merged PR #980; 'Update a server install safely' checklist is live in docs/runbook.md section 3b. Open follow-ups remain: test steps 7, 11, 13 on a Windows install; decide on sources.db backup API; consider a newer-database startup refusal. PR #980 merged; server update checklist live in runbook 3b
- 10-08T07:35 PUYZC6Ez [ARCHIVED] step-gpu-headroom-setting-r2 
    keep-free headroom added; CI running on PR #984 head 5786f4c
- 10-08T07:37 3zyzD95v [ARCHIVED] resolve-956-source-pacing-profiles-r2 
    Merged origin/baihe-subtitler into step-source-pacing-profiles with no conflicts and pushed ad665c0. The full suite ran on the earlier merge f53160f, so only the guard tests were re-run on the final head. merged baihe-subtitler into PR #956, pushed ad665c0
- 10-08T07:37 PWLdp2Tb [ARCHIVED] fix-spend-history-month-range 
    PR #977 merged to baihe-subtitler; out-of-range spend-history months return 422 instead of 500 PR #977 merged; out-of-range spend months now 422, not 500
- 10-08T07:55 NTX3wqX6 [ARCHIVED] live-translation-context-and-thinking **NOTE**
    PR #985 merged into baihe-subtitler; open follow-ups: cancel comment in live_translate.py needs a fix, real-world checks, title glossary. PR #985 merged; live_translate cancel comment still wrong
- 10-08T07:56 S9Decuj7 [ARCHIVED] step-language-pack-glossaries **NOTE**
    PR #986 language packs: all 7 checks green, mergeable, still draft pending owner review of entry list PR #986 CI green on 6038eb2; draft awaiting owner review
- 10-08T08:33 WCVc7U9Y [ARCHIVED] fix-971-review-round-2 **NOTE**
    Pushed 5adbbcd to fix-disk-usage-linked-sizes (PR #971, draft): link-chain guards, move-test teeth; 11616 pytest and 1958 vitest pass. PR #971 pushed at 5adbbcd as draft; full suite passes
- 10-08T08:34 AYKKzw9G [ARCHIVED] fix-984-headroom-review **NOTE**
    Pushed step-gpu-headroom-setting for PR #984; clean-checkout run: 11671 passed, 90 skipped, 8 xfailed, 0 failed. PR #984 pushed; clean-checkout tests 11671 passed, 0 failed
- 10-08T08:35 gKJRzGqy [ARCHIVED] fix-twmanga-review-lows 
    PR #987 (fix-twmanga-review-lows) merged into baihe-subtitler, addressing the four LOW review items PR #987 merged into baihe-subtitler; four LOW fixes landed
- 10-08T08:35 XYFLL9Zn [ARCHIVED] fix-985-live-review 
    PR #985 updated with real-language context, thinking switch, and Ollama gemma4:12b default; tests pass, but nothing was run against live Ollama, DeepSeek, or stream. PR #985 updated; full suite, vitest, Playwright all green
- 10-08T08:53 WJ1Tt1ES [ARCHIVED] resolve-952-conflicts-r3 
    Merged origin/baihe-subtitler into step-subtitle-import (fce4a88), keeping both sides; full pytest, tsc and vitest pass PR #952 conflicts resolved and pushed (fce4a88); tests green
- 10-08T08:53 Eu1Pv9rF [ARCHIVED] resolve-972-conflicts-r3 
    Merged origin/baihe-subtitler into step-ollama-cloud-support (5a514d6) and pushed; pytest, tsc, and vitest pass. merged base into PR #972 branch, pushed 5a514d6, tests green
- 10-08T08:53 2NGvRMuy [ARCHIVED] resolve-911-conflicts-r3 
    Merged origin/baihe-subtitler into diagnostics-smoke-test and pushed 68e6486 with additive conflict resolution; no final full-suite run on the pushed head. merged base into PR #911 branch, pushed 68e6486
- 10-08T08:53 H1pAYMeh [ARCHIVED] resolve-968-conflicts 
    Merged origin/baihe-subtitler into novel-raw-chapters-panel (a82d29c), keeping both sides of the two doc conflicts; no restart of the wait loop. PR #968 conflicts merged into branch, pushed a82d29c
- 10-08T08:53 GHH5UksM [ARCHIVED] resolve-969-conflicts 
    Merged origin/baihe-subtitler into step-remote-ext-1-device-tokens (839005e), keeping both sides; Python and frontend checks pass; GitHub CI not re-run PR #969 base merged, conflicts resolved, pushed 839005e
- 10-08T08:57 nSTXaDQm [ARCHIVED] fix-956-pacing-review 
    Fixed PR #956 findings 3-6: per-host generic slowdown, ASCII-only Retry-After, clamped levels, locked pace writes; limiter in sources/limiter.py PR #956 review findings fixed; TestHardening tests added
- 10-08T08:59 TTuF7QhQ [ARCHIVED] fix-982-video-delay-probe 
    Fixed all three PR #982 findings (probe step, mock advancing mode, missing currentTime) and pushed to fix-live-video-delay-seek after merging origin/baihe-subtitler. PR #982: three review findings fixed and pushed, tests green
- 10-08T08:59 7deqRVX1 [ARCHIVED] fix-986-language-pack-review 
    PR #986 fixes: language-scoped entries, longest-first matching, style labels, traditional entries, deterministic order addressed 5 PR #986 review findings, tests added for each
- 10-08T08:59 E1dMAVva [ARCHIVED] resolve-981-popup-conflict-and-lows 
    Merged origin/baihe-subtitler into extension-capture-whole-chapter with no conflicts, fixed three review items, and pushed; Python and frontend tests pass. merged #979 into PR #981; review fixes pushed, tests green
- 10-08T09:19 oU1fzAps [ARCHIVED] fix-985-think-memory-round-2 
    Fixed think-refusal detection (reads error body) and omitted-engine gating on PR #985 at 4173d44; tests pass. PR #985 findings fixed in 4173d44; backend and vitest green
- 10-08T09:36 oSw6kukL [ARCHIVED] docs-local-agent-backlog 
    waiting for you to review and merge. waiting for you to review and merge.
- 10-08T09:41 RB1TGWRh [ARCHIVED] fix-971-device-path-allowlist 
    Fixed allowlist, parent-folder checks, and overlapping-target flag on fix-disk-usage-linked-sizes; pushed. Python 11696 passed; vitest 1962 passed. PR #971: all 3 findings fixed, pushed; suites green
- 10-08T10:02 eVztPmAc [ARCHIVED] live-transcript-first-and-caption-overlay **NOTE**
    PR #989 CI green on head; draft, watching for reviews
- 10-08T10:02 ZxaPEVdV [ARCHIVED] translation-thinking-toggle **NOTE**
    PR #993 green on 251286a; draft, staying subscribed for reviews
- 10-08T10:02 TX3WkMJU [ARCHIVED] step-live-streaming-a-agreement 
- 10-08T10:16 hcGscbFi [ARCHIVED] fix-984-headroom-per-item-refusals 
    PR #984 fix: dub, narration, and scanlate handlers now re-raise HeadroomError; new tests added; dub.py +196 bytes, within allowlist. HeadroomError re-raised in dub/narration/scanlate; tests added
- 10-08T10:18 Dwa5gipk [ARCHIVED] lead-baihe-studio-2 
    STEP 0 inventory reported: lead tools and agents listed; no code-reviewer agent, so security-reviewer or code-review is used STEP 0 inventory done; awaiting owner go-ahead reply with your go, and paste owner-test-checklist.md when you have it
- 10-08T10:21 pW8yD8Pk [ARCHIVED] resolve-972-conflicts-r4 
    Merged base into step-ollama-cloud-support, resolved conflicts, fixed allowlist size cap; full backend suite not re-run after the fix. merged origin/baihe-subtitler into PR #972; conflicts resolved
- 10-08T10:21 7KHGmc2G [ARCHIVED] resolve-989-live-spec-conflict 
    Merged origin/baihe-subtitler into live-transcript-first-and-caption-overlay (c309b55), resolved live-video.spec.ts conflict keeping both test sets; tsc, vitest, Playwright, and pytest pass. merged baihe-subtitler into PR #989 branch, pushed c309b55
- 10-08T10:21 dgqLn3EH [ARCHIVED] resolve-952-docs-table-r4 
    Merged origin/baihe-subtitler into step-subtitle-import as 8602863; route-permissions.md counts recomputed and tests pass. PR #952 CI not checked. pushed 8602863 merging base into PR #952; docs counts fixed
- 10-08T10:22 eKTteeMb [ARCHIVED] fix-986-honorific-matching-r2 
    Merged origin/baihe-subtitler into step-language-pack-glossaries with conflicts resolved and short-entry matching fixed; 240 tests passed, full suite not re-run after second merge. PR #986: merged baihe-subtitler, fixed short-entry matching
- 10-08T10:22 fcf7nvsN [ARCHIVED] fix-leftovers-956-981 **NOTE**
    Follow-up fixes for #956/#981 are on PR #991 (draft); CI clean on newer head 7e708c8 from another contributor; left for the lead session to merge. PR #991 CI clean on 7e708c8; left as draft for lead
- 10-08T10:23 CR7NvYwh [ARCHIVED] docs-tables-conflict-free 
    PR #992 open; subscribed, waiting on CI and reviews
- 10-08T10:56 FczvY8a2 [ARCHIVED] resolve-992-base-merge 
    Merged latest origin/baihe-subtitler into docs-tables-conflict-free (PR #992) with no conflicts and pushed; no rebase or force-push. merged base into docs-tables-conflict-free; pushed, no conflicts
- 10-08T11:02 MRStKtB9 [ARCHIVED] fix-972-cloud-gate-everywhere 
    Ollama cloud tags now require engines.paid on every model-taking route, enforced by a route sweep test; restored cloud defaults are ignored on read. cloud-tag gate on model routes; 7 new tests added
- 10-08T11:03 1iQzYYp2 [ARCHIVED] fix-993-thinking-toggle-review 
    Fixed PR #993 review items 1-4 on translation-thinking-toggle: thinking wiring, paid-caller gating, title save; Playwright 41 passed, real DeepSeek/Ollama untested. PR #993 review items 1-4 fixed; specs pass
- 10-08T11:03 9C3UCiCN [ARCHIVED] fix-991-limiter-strict-wins 
    Pushed limiter waiting-limit fix and popup in-flight guard to PR #991; full pytest suite, tsc, and vitest pass. PR #991: limiter waiting-limit fix and popup guard pushed
- 10-08T15:48 nNkrbne2 [ARCHIVED] resolve-952-new-table-format 
    Merged origin/baihe-subtitler (incl. #992) into step-subtitle-import and pushed b4ec4aa; conflicts resolved to the new format; permissions/file-org 136 passed, full suite 11959 passed, frontend tsc and vitest clean. merged baihe-subtitler into PR #952 branch; pushed b4ec4aa, tests pass
- 10-08T15:48 PRkAUXfj [ARCHIVED] resolve-993-new-table-format 
    Merged origin/baihe-subtitler into translation-thinking-toggle and pushed 251286a..05b3502; full backend suite, tsc and vitest pass. merged baihe-subtitler into #993, pushed 05b3502; tests green
- 10-08T15:48 v58tdmXZ [ARCHIVED] resolve-989-new-table-format 
    Merged origin/baihe-subtitler into live-transcript-first-and-caption-overlay (plain merge, pushed). Full suite ran on the first merge only; the second merge got a partial re-run. merged baihe-subtitler into PR #989 branch and pushed
- 10-08T15:48 T3h5GmwT [ARCHIVED] resolve-986-new-table-format 
    Merged origin/baihe-subtitler into step-language-pack-glossaries and pushed 8bf754f; backend and frontend checks pass, Playwright e2e not run. merged baihe-subtitler into PR #986; pushed 8bf754f
- 10-08T15:48 YeZTJtpn [ARCHIVED] resolve-911-new-table-format 
    Merged origin/baihe-subtitler into diagnostics-smoke-test (PR #911) and pushed; all checks passed. merged baihe-subtitler into PR #911; checks green
- 10-08T15:48 E7sg2Acr [ARCHIVED] resolve-968-new-table-format 
    Merged origin/baihe-subtitler into novel-raw-chapters-panel and pushed adce9c6; conflicts resolved to the new format, full test suites green. merged baihe-subtitler into PR #968, pushed adce9c6
- 10-08T15:48 mq1NvfTU [ARCHIVED] resolve-969-table-format-and-expiry 
    Merged origin/baihe-subtitler into the device-token branch, set 90-day default expiry, synced docs, and pushed; full suite passes (11914). PR #969 merged base, 90d default expiry, docs synced; pushed
- 10-08T15:50 zb52Ysf6 [ARCHIVED] remove-missing-tabs-field-2 
    PR #994 merged into baihe-subtitler; deprecated missing_tabs field removed across backend, frontend, and tests PR #994 merged; missing_tabs field removed from baihe-subtitler
- 10-08T16:13 V72fkydP [ARCHIVED] fix-969-device-token-hardening 
    PR #969: device-token fixes and docs done; full suite 11958 passed, frontend tsc and vitest clean PR #969 fixes applied; full suite 11958 passed
- 10-08T16:20 nZvrGT3C [ARCHIVED] fix-993-thinking-in-db 
    translate_thinking column on dramas replaces translate_prefs.json; per-engine settings hash added; Vitest and Playwright pass. PR #993: thinking choice moved to dramas column; tests green
- 10-08T16:28 foHdkr5v [ARCHIVED] fix-986-traditional-and-claiming **NOTE**
    Traditional Chinese glossary fixes pushed to PR #986; backend suite passed (11,982 tests), vitest not verified. PR #986 pushed; backend suite green, vitest unverified
- 10-08T16:40 Xgc3F7W7 [ARCHIVED] fix-989-caption-delay-and-tests 
    PR #989 review fixes: captions wait for the clamped delay, cue-id chunk test added, run_live_job and route docs updated PR #989: review findings 1-3 fixed; tests added, docs updated
- 10-08T16:42 dwk8NBv9 [ARCHIVED] fix-968-confinement-cost-conflict 
    Merged baihe-subtitler (kept both sides in SourceStage.tsx); added safe_file confinement, per-request caching, lines.read gate on chapter text, and page-scoped in_translation count. PR #968: chapter routes confined, cached, permission-gated
- 10-08T22:21 L4WnMrku [ARCHIVED] fix-989-test-and-drift 
    Pushed a29fbb0 to live-transcript-first-and-caption-overlay with all three review fixes; Python, vitest, and Live Playwright suites pass. PR #989 review fixes pushed as a29fbb0; suites pass
- 10-08T22:24 LhegCZhm [ARCHIVED] fix-969-conflict-and-lows 
    Branch step-remote-ext-1-device-tokens pushed at 2860fa8; prior test results stand, wait helper not restarted PR #969 pushed at 2860fa8; wait-loop helper stopped, no loss
- 10-08T22:24 Tz5CY24V [ARCHIVED] fix-968-conflict-and-lows 
    PR #968 branch merged with origin/baihe-subtitler, pushed; full suite passed PR #968 branch up to date; suite passed, pushed
- 10-08T22:43 pU3fHZWK [ARCHIVED] fix-993-paid-gate-and-offpeak **NOTE**
    Fixed paid-gate and off-peak thinking findings on PR #993; finding 3 deferred, full pytest and Playwright not re-run. PR #993 thinking fixes pushed; full suite not re-run
- 10-08T22:44 PUCaZKUK [ARCHIVED] fix-911-ocr-offline-headroom-conflict 
    Please confirm on a real PaddleOCR install that those folder names appear under `official_models`. Please confirm on a real PaddleOCR install that those folder names appear under `official_models`.
- 10-08T23:34 DVg7Vik1 [ARCHIVED] fix-993-ci-test-and-rebase **NOTE**
    Test now passes thinking=True and asserts it's persisted; pushed ec0d54a after merging origin/baihe-subtitler (b1f5c1e); local suites green. pushed pronoun test fix for thinking arg (ec0d54a); CI not yet run
- 10-08T23:48 auVSyLHm [ARCHIVED] fix-911-ollama-headroom 
    please confirm the three pinned model folder names appear under `official_models`. please confirm the three pinned model folder names appear under `official_models`.
- 10-09T00:14 1ZXuvtsP [ARCHIVED] splits-batch **NOTE**
    Draft PR #996 combines the three split drafts on splits-batch; all seven CI checks passed. #917, #919 and #920 are superseded but not closed. draft PR #996 merges three splits; all 7 CI checks green
- 10-09T00:14 Amm2nHpd [ARCHIVED] fix-leftovers-batch **NOTE**
    Draft PR #995 on fix-leftovers-batch bundles leftovers (a)-(i), skipping (g) and glossary-only (f); all 7 checks green. draft PR #995 CI green on 6ddf468; ready for lead to merge
- 10-09T01:16 bShPhsuF [ARCHIVED] fix-996-import-cycle 
    Pushed f9c3a0d to splits-batch, breaking the glossary import cycle via new glossary_common.py; backend 12285 passed, frontend 2035 passed. cycle fixed in f9c3a0d; backend and frontend suites green
- 10-09T03:08 vVToSAKE [IDLE] resolve-891-qwen3-conflict-and-realcheck **NOTE**
    Merged origin/baihe-subtitler into qwen3-asr-native and pushed; full suite earlier had 7 failures, fixed and re-ran affected files, but no complete clean run on final head. PR #891 merged baihe-subtitler, pushed; final suite unverified
- 10-09T03:09 uqpoT2od [ARCHIVED] resolve-938-browser-install-marker **NOTE**
    PR #938 fix for half-unpacked Chromium installs and LOW items done; full Python, tsc, vitest, and Playwright specs pass #938: INSTALLATION_COMPLETE check added; suites green after merge
- 10-09T03:09 Zb9xJDkC [ARCHIVED] fix-bilibili-capture-missing-pages 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-09T03:10 A2w123fs [ARCHIVED] fix-bilibili-capture-8-of-47-pages 
    subscribed to PR #997; waiting on CI and review
- 10-09T03:11 PGZ3QaNp [ARCHIVED] resolve-942-asmr-vad-fixes 
    Pushed PR #942 branch at ff53715; full pytest passed (12336), final commit not re-run, CI will cover it PR #942 pushed at ff53715; full pytest passed, CI pending
- 10-09T03:16 LtwRDGiz [ARCHIVED] fix-twmanga-tainted-canvas **NOTE**
    Draft PR #998 adds an Allow-this-site fallback for the twmanga tainted-canvas error; CI green, not yet tested on the live page. PR #998 draft: image-read fallback for extension; CI green
- 10-09T03:21 kJ6srAgn [IDLE] source-pace-profiles **NOTE**
    draft PR #999 CI green; watching for review events
- 10-09T03:28 iKpb3HY7 [ARCHIVED] title-source-link-and-chapters **NOTE**
    Draft PR #1001 adds source-link viewing for novels and manhua at title and chapter level; CI is green and it's left for you to merge. PR #1001 draft: CI green, no review comments, awaiting merge
- 10-09T03:37 sWdPFNE1 [IDLE] fix-toggle-full-width **NOTE**
    Draft PR #1000 fixes the Live 'Reply without thinking' toggle width via flex: 0 0 auto on Field toggles; all seven CI checks green, left unmerged for the lead session. draft PR #1000 CI green; Live toggle width fixed
- 10-09T03:41 KQnLvEaa [ARCHIVED] small-fixes-oct9 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-09T03:42 syBQTYJx [IDLE] small-fixes-oct9 
    waiting on CI
- 10-09T03:48 sKQCBP7h [IDLE] install-on-next-start 
    PR #1004 open, no CI yet; subscribed for CI/review events
- 10-09T03:48 gNf2TLH2 [ARCHIVED] fix-playwright-installed-check **NOTE**
    draft PR #1003 open, suite green; watching PR activity
- 10-09T03:50 17gcCbLM [IDLE] resolve-997-capture-chapter-labels **NOTE**
    merge staged, 11 own tests red; need delete-vs-port call Decide whether the agent may delete tests/test_extension_batching.py and tests/test_extension_popup.py, or keep that front-end accounting and re-port it onto the base's capture code
- 10-09T03:53 kd9jsW17 [IDLE] fix-998-extension-fetch-hardening 
    Fixed PR #998 per the FIX FIRST review: popup origin re-validation, credentials omit and redirect error, origin-only referrer, expanded private-host blocking. Branch updated. addressed 3 review fixes on PR #998; branch updated
- 10-09T03:54 aewJFZHd [ARCHIVED] fix-live-stalls-and-latency 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-09T03:56 9BGNrmBt [ARCHIVED] fix-live-stalls-and-latency 
    full pytest run at ~12%; will report result when done
- 10-09T04:01 H5NaJVfR [ARCHIVED] fix-gpu-lock-release-on-restart **NOTE**
    PR #1002 CI green; watching for review and failures
- 10-09T04:17 qeALxLzJ [IDLE] fix-setup-playwright-package-field 
    Setup 'Playwright package' row now uses a real bool and shows a Problem only when false; PR description updated with the real cause. Full backend suite not re-run after final edits. PR #1003: package now a real bool; tests pass
- 10-09T04:19 5NuyhmiY [ARCHIVED] fix-temp-folder-leaks **NOTE**
    pytest 2 failures pre-existing/flaky; watching #1008 for CI
- 10-09T04:20 eMd9oyzw [IDLE] header-scanlate-button-on-1001 **NOTE**
    Merged origin/baihe-subtitler into title-source-link-and-chapters and made header buttons primary. Python, vitest and Playwright pass; CI pickup and sources specs after last layout change unverified. PR #1001: merged base, primary header buttons, tests green
- 10-09T04:22 TZgbkBji [ARCHIVED] fix-voice-detector-field-layout **NOTE**
    Voice detector layout regression fixed in PR #1006 (draft); all 7 CI checks green, no review comments yet PR #1006 layout fix: CI green, still draft for lead merge
- 10-09T04:36 mbyUSV13 [IDLE] resolve-1004-install-on-next-start 
    PR #1004 conflict: mapping pending-install read/write paths
- 10-09T04:38 zWJzETXn [IDLE] fix-1002-gpu-release-window **NOTE**
    Pushed 0709b3e: acquired_at guard on owner-gone release, tests and docs updated; suite 12378 passed. LOW 2 deferred, LOW 1 to PR description. PR #1002 MEDIUM fixed in 0709b3e; full suite green
- 10-09T04:40 MSsBdsVj [IDLE] fix-1005-review-findings 
    PR #1005 fixes applied: friendly download names, symlink-safe .download.json write, byte-capped stems; pytest and vitest pass. PR #1005 review fixes done; pytest and vitest green
- 10-09T04:41 hpXGuVSA [ARCHIVED] fix-1007-live-review-findings **NOTE**
    PR #1007 fixes the per-chunk model load, unload throttle and lost-notice bugs; the chunk-1 stall still needs a real run to confirm. PR #1007 stall fixes done; real-stream checks left for you
- 10-09T04:46 qwt8uhbw [ARCHIVED] translate-context-defaults 
    PR #1009 open; watching activity, no CI or review issues yet
- 10-09T05:03 KPfxsTMF [ARCHIVED] pronoun-default-guard 
- 10-09T05:16 MQurjemm [ARCHIVED] job-run-settings **NOTE**
    PR #1011 CI green; draft awaiting Opus security review
- 10-09T05:21 98ip7DZW [ARCHIVED] fix-retranscribe-line-stuck-cancel **NOTE**
    I'll act on any failure or review that arrives.
- 10-09T05:50 FbFbyrQZ [ARCHIVED] resolve-1009-translate-context-defaults **NOTE**
    Merged baihe-subtitler into translate-context-defaults and pushed; targeted tests pass and the size guard is fixed. PR #1009 is still a draft. merged baihe-subtitler into #1009, pushed; size guard fixed
- 10-09T05:50 Z9AjBfKE [ARCHIVED] resolve-1008-temp-folder-leaks **NOTE**
    Merged origin/baihe-subtitler into fix-temp-folder-leaks and pushed; full suite 12405 passed, 89 skipped, 8 xfailed. PR #1008 still draft. merged baihe-subtitler into PR #1008; 12405 tests pass
- 10-09T05:53 BRJxcQZU [ARCHIVED] fix-1007-opus-round2 **NOTE**
    Three review fixes pushed to fix-live-stalls-and-latency; full suite 12404 passed, PR #1007 still draft PR #1007 fixes pushed; full suite green, still draft
- 10-09T06:00 Tw9chVzk [ARCHIVED] fix-1011-run-settings-review 
    Pushed the PR #1011 review fixes to job-run-settings (no force); the full test suite has finished. PR #1011 review fixes pushed to job-run-settings branch
- 10-09T06:00 CANyumQU [ARCHIVED] fix-1012-process-job-redesign 
    Re-transcribe moved to a child process with timeout and cancel; bounded_whisper removed; two new tests added PR #1012 reworked: retranscribe runs in child process
- 10-09T06:01 mm8HPJDW [ARCHIVED] translate-stage-persist-and-glossary-button 
    PR #1013 (translate-stage persist and glossary button) merged into baihe-subtitler after all seven CI checks passed PR #1013 merged into baihe-subtitler; all 7 checks green
- 10-09T06:02 1YWWxfPS [ARCHIVED] fix-llm-task-slow-deepseek-calls 
    [ede_diagnostic] result_type=user last_content_type=n/a stop_reason=tool_use
- 10-09T06:02 i1pJQtE2 [ARCHIVED] fix-llm-task-slow-deepseek-calls 
    PR #1014 CI green; subscribed for review/CI changes
- 10-09T06:07 jPZTNc1R [ARCHIVED] resolve-997-port-capture-accounting **NOTE**
    Merged origin/baihe-subtitler into fix-bilibili-capture-pages without rebase or force, pushed, and the full test run finished. PR #997 merged baihe-subtitler, pushed; nothing left to watch
- 10-09T06:37 faTbV2is [ARCHIVED] fix-resplit-makes-no-changes 
    watching PR #1017 for CI or review activity; nothing new yet
- 10-09T06:44 fQvb8ZXp [ARCHIVED] fix-added-line-untranslated-count 
    PR #1015 ready: all six CI checks green, including full backend and four Playwright shards PR #1015 CI green; ready for review and merge
- 10-09T06:48 fXz76K4r [ARCHIVED] glossary-scan-resume-on-return 
    waiting on the reviewer
- 10-09T06:51 85ynDfmY [ARCHIVED] asr-settings-test-on-real-clip **NOTE**
    PR #1022 CI green, mergeable; still draft, watching it
- 10-09T06:52 FeRfAyEo [ARCHIVED] fix-997-opus-findings 
    Fixed MEDIUM-1/2 and LOW-1/2 on fix-bilibili-capture-pages: redacted path leaks, rollback on partial stores, blank-check memory cap, stop-reason popup. 226 pytest passed. PR #997 security fixes applied; targeted tests pass, Playwright not run
- 10-09T06:53 DZWK4Tpp [ARCHIVED] resolve-1012-and-lows **NOTE**
    Pushed fix-retranscribe-line-stuck-cancel with the test fix, reviewer items, and proc.join after kill_tree; CI not yet re-run. PR #1012 pushed: test fixed, reviewer items done, suites green
- 10-09T06:54 pPDcY24L [ARCHIVED] fix-1014-opus-findings **NOTE**
    Pushed a1630df to fix-llm-task-slow-deepseek-calls with all review items fixed; PR left as draft, full suite passing. PR #1014 review fixes pushed as a1630df; still draft
- 10-09T06:54 UZBs82Ed [ARCHIVED] fix-1008-opus-findings 
    Applied PR #1008 review fixes: clean-temp requires confirm:true, normalized held paths, and live-process hold markers. PR #1008 review fixes done: confirm gate, hold markers
- 10-09T06:54 qoUn6cRo [ARCHIVED] fix-1007-opus-round3 
    Fixed LOW-1 (admin checks refuse while an abandoned Whisper call runs) and LOW-2 (startup clears stale live-whisper claims); added redaction and tests. PR #1007: LOW-1/2 and log redaction fixed, tests added
- 10-09T07:13 VoLRvU5Y [ARCHIVED] timing-drift-review-flag **NOTE**
    Draft PR #1018 on timing-drift-review-flag; all 7 CI checks green on 0dd2681. User to run the title #25 Qwen-only check. PR #1018 draft; CI green on 0dd2681, title #25 check pending
- 10-09T07:17 4nmQBufM [ARCHIVED] resolve-1017-file-organization **NOTE**
    PR #1017 merged with origin/baihe-subtitler and pushed (still draft); focused and full suites green, tsc and vitest clean. PR #1017 merged baihe-subtitler, pushed; tests green
- 10-09T07:22 zSowjiwX [ARCHIVED] fix-1014-round3 **NOTE**
    Pushed fixes as a30e018 to fix-llm-task-slow-deepseek-calls; PR #1014 stays draft pending Opus re-review. PR #1014 fixes pushed as a30e018; suite passed 12426
- 10-09T07:22 CSTjLMHj [ARCHIVED] fix-997-round3 
    Fixed HIGH-1 re-capture data loss and LOW-1/LOW-2 on PR #997; full suite 12442 passed. Vitest pass count was not seen; push not confirmed in tail. PR #997 HIGH-1 and LOW-1/2 fixed; full suite green
- 10-09T08:15 kyHDCNH4 [ARCHIVED] transcribe-gap-and-bulk-retranscribe 
    I'll act when CI or a review arrives.
- 10-09T08:18 aPLt7gFN [ARCHIVED] fix-997-round4 **NOTE**
    Pushed d54298f to fix-bilibili-capture-pages; PR #997 remains a draft, ready for delta Opus review. PR #997 fixes pushed as d54298f; still draft, ready for review
- 10-09T08:19 FcrsXcxi [ARCHIVED] lows-1007-1014-followup **NOTE**
    PR #1019 merged into baihe-subtitler with all six #1007/#1014 LOW follow-ups; #1002 gpu_lock release must skip live rows still alive PR #1019 merged; six LOW follow-ups in, CI green on 9b29d3a
- 10-09T08:21 hN67k6i2 [ARCHIVED] resolve-1017-e2e-and-conflict **NOTE**
    PR #1017 merge with baihe-subtitler resolved and pushed; e2e (2) spec fixed; still draft PR #1017: merged baihe-subtitler, fixed e2e (2) locator, pushed
- 10-09T08:21 pgcWcoU9 [ARCHIVED] resolve-1018-conflicts 
    PR #1018 merged latest origin/baihe-subtitler; three timing-check routes listed for security review, none public PR #1018 base merged, conflicts resolved; security notes ready
- 10-09T08:55 bxvwSQeR [ARCHIVED] fix-base-background-jobs-size 
    PR #1021 CI clear on head; agent still watching it
- 10-09T09:00 dTthfQ3P [ARCHIVED] fix-1020-review-findings **NOTE**
    Review fixes pushed to transcribe-gap-and-bulk-retranscribe, PR #1020 left as draft. Suite: 12613 passed, 1 failed on the base's background_jobs.py size guard. PR #1020 fixes pushed; still draft; 1 base-branch failure
- 10-09T09:00 68VuB2cA [IDLE] fix-997-round5 **NOTE**
    Fixed the MEDIUM and two LOWs on fix-bilibili-capture-pages; full suite 12,553 passed, 4 failed (3 from base merge, 1 unidentified); Opus delta review can proceed. PR #997 fixes in; size test fails on base, not this PR
- 10-09T09:01 k7bF6DGo [ARCHIVED] fix-1018-review-findings **NOTE**
    Review fixes pushed to timing-drift-review-flag (748220b), PR #1018 left draft; only failure is background_jobs.py size guard, also red on base. PR #1018 fixes pushed (748220b); still draft; 1 base-red fail
- 10-09T15:14 W3oWthjb [ARCHIVED] fix-1018-round3 **NOTE**
    PR #1018 fixes pushed to timing-drift-review-flag; full suite passed (12588), PR left as draft PR #1018 fixes pushed; 12588 tests passed, nothing pending
- 10-09T23:31 Zm91jAMx [ARCHIVED] fix-live-stalls-flaky-warmstart-test 
    PR #1023 pushed; awaiting CI result, subscribed to activity
- 10-09T23:31 LKc8D7kG [ARCHIVED] fix-1020-tsc-test-fixtures **NOTE**
    Pushed PR #1020 fixes to transcribe-gap-and-bulk-retranscribe; first full test run showed 2 failed and 12619 passed. pushed PR #1020 fixes; full run 2 failed, 12619 passed
- 10-09T23:40 myny7Njv [IDLE] qc-bounded-llm-calls-by-default **NOTE**
    PR #1026 fixes LLM-call cancel/hang; CI green on d48387a. Still draft, pending Opus review and PC cancel test. CI green on draft PR #1026 at d48387a; Opus review pending
- 10-09T23:40 ouPs6dqw [ARCHIVED] qc-diagnostics-installs-as-jobs **NOTE**
    PR #1025: CI red on 2 pre-existing tests; watching e2e
- 10-09T23:40 CphUASJ8 [ARCHIVED] qc-job-escape-hatch-and-small **NOTE**
    PR #1024 CI green; still draft, awaiting Opus review
- 10-09T23:40 jpaw1oe4 [IDLE] resolve-1002-gpu-lock-release **NOTE**
    Merged origin/baihe-subtitler into fix-gpu-lock-release-on-restart and pushed 746da57; full suite passes (12539). PR still draft. PR #1002 merged base, pushed 746da57; full suite green
- 10-10T00:42 59x7UV6u [IDLE] resolve-1023-live-whisper-conflict **NOTE**
    Merged origin/baihe-subtitler into fix-live-stalls-flaky-warmstart-test and pushed; PR #1023 stays draft; full suite passes locally, CI not yet checked merged baihe-subtitler into PR #1023; local suite green
- 10-10T00:42 vgXcoTzR [IDLE] resolve-1018-after-1020 
    Merged origin/baihe-subtitler into timing-drift-review-flag (no rebase/force), kept both sides of conflicts, pushed; full suite passed (12705). merged baihe-subtitler into timing-drift-review-flag; pushed
- 10-10T00:48 pBnHq7jV [IDLE] fix-1025-review-findings 
    Opus review fixes on PR #1025: UTF-8 env for child processes, capture-checker hardening, nits; suites green PR #1025 review fixes applied; Python, vitest, Playwright green
- 10-10T00:49 HE6iiGmP [IDLE] fix-1024-review-findings **NOTE**
    Four Opus review fixes pushed to qc-job-escape-hatch-and-small (93d09b3); PR stays draft. Frontend checks and Playwright specs not run. PR #1024 fixes pushed as 93d09b3; still draft, ready for re-review
- 10-10T11:22 t6rVskwx [ARCHIVED] fix-1052-review-findings 
    Addressed all HIGH/MEDIUM/LOW review findings on PR #1052; area tests 426 passed, guards 187 passed. PR #1052 review findings fixed; area and guard tests pass
- 10-10T11:22 RrZkEHLs [ARCHIVED] fix-1004-review-findings 
    Fixed MEDIUM-2 and LOW-3 through LOW-6 on PR #1004: merged queue checks, service apply path, preview lock, and key/type validation. PR #1004 review findings fixed; tests use fakes, no real pip
- 10-10T11:44 5ayoUT9C [ARCHIVED] fix-1054-review-findings **NOTE**
    Pushed four LOW-finding fixes to shrink-http-wave2-root-and-sources; PR #1054 stays draft, no new PR PR #1054: four LOW findings fixed, pushed, still draft
- 10-10T12:17 1mwr9VwP [ARCHIVED] qc-ytdlp-subprocess 
- 10-10T12:17 tToQDLfj [ARCHIVED] split-diagnostics-upgrade-check 
    PR #1056 subscribed; waiting on CI and review
- 10-10T12:17 LwX6Zcna [ARCHIVED] shrink-http-wave3-sessions-and-engines 
    PR #1057 open; waiting on CI or review event to resume
- 10-10T12:41 PgQqHWAk [ARCHIVED] jobs-phase1-pr-a-store-exit-flush **NOTE**
    PR #1058 CI green; draft; re-checking at 13:52 UTC
- 10-10T12:42 xVtvq6RD [ARCHIVED] fix-1055-review-findings **NOTE**
    Fixed forged-event spoofing (nonce plus event validation), cancel-test liveness, and docstring on draft PR #1055; pushed to qc-ytdlp-subprocess. MEDIUM-1, LOW-2, LOW-3 fixed on #1055; pushed, still draft
- 10-10T12:43 1EUdGSo2 [ARCHIVED] fix-1057-review-findings **NOTE**
    Pushed commit 3464598 to draft PR #1057 fixing post_json transport errors, header case, and deadline; tests and guards pass, shared.py at 30,003 bytes. HIGH-1, LOW-2, LOW-3 fixed in post_json; pushed 3464598 to #1057
- 10-10T13:11 GQqwSArf [ARCHIVED] fix-1055-settings-parity-test **NOTE**
    Pushed 31cc0bc to qc-ytdlp-subprocess fixing test_url_download_passes_saved_cookies; PR #1055 still draft, CI not yet checked. cookie parity test fixed and pushed as 31cc0bc; CI unchecked
- 10-10T13:11 8A4A7NiD [ARCHIVED] fix-1057-transport-error-tests 
    fix pushed as 4d67956; guards running in background
- 10-10T13:11 zWXfCNVJ [ARCHIVED] split-transcribe-pipeline 
    PR #1059 subscribed; waiting on CI (~17 min) to report
- 10-10T13:18 GoyhW14g [ARCHIVED] fix-1058-review-findings 
    Fixed MEDIUM-4 and LOW-1 through LOW-4 on jobs-db-first-store (renamed to jobs/job_store.py); 611+152+22 tests pass, frozen file sizes not grown. PR #1058 concurrency findings fixed; test suites green
- 10-10T13:40 XXAaDcia [ARCHIVED] route-table-generator 
    PR #1060 CI green (7/7); watching for reviews or conflicts
- 10-10T14:07 1zPBqLeN [ARCHIVED] split-scanlate-detect **NOTE**
    Draft PR #1061 moves page-geometry detection into scanlate_detect.py; all 7 checks green, no review comments or conflicts. split scanlate_detect out; draft PR #1061 CI green
- 10-10T14:10 rvs1RVRw [ARCHIVED] settings-schema-pr1-declarations-and-reader **NOTE**
    Draft PR #1062 (PR 1 of 3, no behaviour change) at 7b55412; all 7 CI checks pass, no review comments or conflicts. draft PR #1062 settings schema; CI green, 7/7 checks
- 10-10T14:39 euAWTrCq [ARCHIVED] split-cli-translate 
    waiting for CI
- 10-10T14:40 q5eyGQxA [ARCHIVED] settings-schema-pr2-one-reader 
    PR #1068 open; watching for CI, review, or conflicts
- 10-10T14:51 NMEeTgwZ [ARCHIVED] split-scanlate-inpaint 
    waiting for CI
- 10-10T14:51 gNPde8HP [ARCHIVED] split-core-whisper-models 
    PR #1067 subscribed; waiting on CI or review results
- 10-10T14:51 UhFDF8c7 [ARCHIVED] split-diagnostics-report 
    I'll respond when one does.
- 10-10T14:51 jvrdYptJ [ARCHIVED] settings-schema-pr3-endpoint-and-form 
    I'll respond when CI or a review arrives.
- 10-10T14:51 aAKiNQsv [ARCHIVED] shrink-http-wave4-sessions **NOTE**
    Draft wave 4 PR #1070 opened into baihe-subtitler; subscribed to its activity draft PR #1070 open; subscribed to its activity
- 10-10T14:51 RsRwEL9q [ARCHIVED] resolve-935-hide-novel-sections **NOTE**
    Merged origin/baihe-subtitler into hide-novel-sections (2ae2a7d); tsc, vitest, and Playwright pass; PR still draft. PR #935 merged with base at 2ae2a7d; checks green
- 10-10T14:51 gJFLJ2cK [ARCHIVED] resolve-937-review-flags-tuning **NOTE**
    Resolved 2 conflicts on PR #937 with a merge commit (68930e6); guard, area, and frontend checks pass; PR still draft. PR #937 merged baihe-subtitler, pushed 68930e6; checks green
- 10-10T14:52 iZZgwcNp [ARCHIVED] resolve-903-paddleocr-install **NOTE**
    Merged origin/baihe-subtitler into PR #903 and pushed; the PR diff vs base is now empty, so it may be superseded and closable. PR #903 conflicts merged into branch; quick guards pass
- 10-10T14:52 eu3XTQQh [ARCHIVED] jobs-phase1-pr-b-gpu-slots 
    PR #1073 CI running; re-checking at 15:56 UTC
- 10-10T14:52 At4QrZrb [ARCHIVED] extension-novel-capture 
    PR #1072 open; waiting on CI and review events
- 10-10T14:52 mwjFr4zk [ARCHIVED] docs-sync-after-merges 
    PR #1064 ready for review; CI green, staying subscribed
- 10-10T14:53 EiRyRBzT [ARCHIVED] ux-punchlist-make-subtitles-and-transcribe 
    PR #1069 open; watching for CI results and reviews
- 10-10T15:09 x9cVUKHg [ARCHIVED] fix: lib.http Response.ok 2xx-only, case-insensitive headers, 307/308  
    I'll pick the PR back up when CI or a review comes in.
- 10-10T15:09 uXXiK7m9 [ARCHIVED] fix: pending_install stale lock by pid, restore state visible to the w 
    PR #1077 open; 7 CI checks queued, awaiting results
- 10-10T15:09 9RkhyZT6 [ARCHIVED] fix: job_store short busy wait under lock, prune _last_write, owner_go 
    PR #1078 open; CI queued (7 jobs), watching for results
- 10-10T15:09 hyBunT4K [ARCHIVED] fix: gpu_process_job post-exit drain, guarded on_item, bounded give_up **NOTE**
    draft PR #1076 open; subscribed, awaiting CI/review events
- 10-10T15:11 mudE6b9P [ARCHIVED] resolve-1063-split-cli-translate **NOTE**
    Merge commit 51fcb53 pushed to split-cli-translate (PR #1063 still draft); conflict was in cli.py imports; all tests pass. merged baihe-subtitler into split-cli-translate; tests green
- 10-10T15:11 NWr1JX7K [ARCHIVED] resolve-1073-gpu-slots-after-1058 
    #1073 CI queued (8 checks); will resume when it finishes
- 10-10T15:13 vK2aTP1a [ARCHIVED] fix: hostile-review low findings (settings log, cancellable_lock, ytdl **NOTE**
    PR #1080 draft open; watching for CI and review activity
- 10-10T15:13 P5eV5aa9 [ARCHIVED] hygiene: compute test_db import-safety module list from core's imports 
    I'll respond when a CI result, review or other PR event arrives.
- 10-10T15:13 5ra8pBux [ARCHIVED] resolve-910-benchmark-from-reviewed **NOTE**
    Merged origin/baihe-subtitler into PR #910 (57a74e4) and pushed; Python and frontend tests pass; real-key checks left for user. PR #910 merged with base, pushed; tests green; still draft
- 10-10T15:13 XBqqRJTY [ARCHIVED] resolve-891-qwen3-asr-native 
    PR #891 now on latest base: qwen-asr dropped, merge breaks fixed, 1162+371 tests pass, tsc and vitest clean; GPU checks posted for owner PR #891 rebased on baihe-subtitler; tests green, GPU checklist posted
- 10-10T15:20 JWYaHy7m [ARCHIVED] fix-1070-review-findings **NOTE**
    Fixed M1 and L1-L4 in 6bdfe7a on shrink-http-wave4-sessions, pushed without rebase or force; PR #1070 still draft, pass counts posted as a comment. fixed 5 review findings in 6bdfe7a; pushed to PR #1070
- 10-10T15:20 6owBWw5U [ARCHIVED] fix-1072-review-finding **NOTE**
    Pushed the 200-code-point heading cut and surrogate stripping to extension-novel-capture; PR #1072 stays draft. Test runs: 138 and 186 passed. split-surrogate fix pushed to #1072; tests pass
- 10-10T15:23 m5DNSQNp [ARCHIVED] fix-937-phone-overlap-and-conflict **NOTE**
    Merged base into PR #937, fixed pacing-button overlap at 360/390px, pushed as draft; 2 unrelated RecordsPanel e2e failures noted. PR #937 merged base, fixed 360px overlap, pushed as draft
- 10-10T15:23 nPM6SUaf [ARCHIVED] fix: one pip output cap, document dist_in_use's conservative rule 
    waiting for CI
- 10-10T15:28 2se1FUPH [IDLE] fix: hostile-review jobs leftovers (close_stale liveness, atexit place 
    I'll keep watching it, and the 16:29 UTC check-in stays in place as a backstop.
- 10-10T15:28 ZXVXzRCs [ARCHIVED] fix: hostile-review install leftovers (argv check, lock touch, queue g 
    waiting on CI
- 10-10T15:28 4TqR5rsj [ARCHIVED] fix: hostile-review http/ytdlp/settings leftovers (proxy doc, nonce ec 
    I'll respond when CI finishes or a review arrives.
- 10-10T15:28 s6X8565D [ARCHIVED] fix: hostile-review extension leftovers (base64 without string growth, **NOTE**
    draft PR #1084 open; subscribed, waiting on CI/reviews
- 10-10T15:32 qpDULLeX [ARCHIVED] review-W1-1 auth, server and core routers **NOTE**
    Read-only review of api/auth.py, server.py and routes; no HIGH issues, 5 MEDIUM, 8 LOW. Report at scratchpad/hostile-review-auth-server-core-routers.md. Issue not posted. hostile review of auth/server/routers: 0 high, 5 med, 8 low
- 10-10T15:32 zxAKAzu7 [IDLE] review-W1-7 background jobs and process workers **NOTE**
    read-only review of job runner; two checks left before filing
- 10-10T15:32 Nd253xtz [ARCHIVED] review-W1-8 db, storage, migrations and restore 
    Read-only hostile review of db.py, storage.py, and related services filed as GitHub issue #1088. read-only review of db.py and storage modules filed as #1088
- 10-10T15:32 BFrEYDQn [IDLE] review-W1-11 sources fetch ladder, adapters and URL guard 
    hostile review of sources/ still in progress; cites #1097
- 10-10T15:34 G8mvq9in [IDLE] fix: extension canvas-to-image match, per-step re-download, partial-re 
    I'll keep watching the PR and act when CI results or reviews arrive.
- 10-10T15:34 SioZtVEn [IDLE] fix: Diagnostics install UI (preview failure fallback, consent carry-o 
    I'll pick it up when CI results or reviews arrive.
- 10-10T15:34 NoR9VYXt [IDLE] fix: UX review leftovers (two primaries, source-link touch target, sta **NOTE**
    U00 draft PR: CI on fix commit fb8777d still pending
- 10-10T15:34 fPMzwXXn [ARCHIVED] hygiene: server messages say \ **NOTE**
    backend tests running; one failed, fixing before push
- 10-10T15:35 quWUDorX [ARCHIVED] fix: engine_backends review lows (3xx not transient, no retry on overs **NOTE**
    Draft PR #1090 into baihe-subtitler from fix-engines-review-lows; subscribed to CI and review activity draft PR #1090 opened; subscribed to its CI/review events
- 10-10T15:41 p7HRvYvY [IDLE] fix: installer launcher stale apply.lock by pid (review H1) 
    waiting for CI
- 10-10T15:41 EvkhKjio [ARCHIVED] fix: page_server translation outside the lock double-bills and clobber 
    Waiting on permission: bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Subscrib… Approve or deny bf7c680d-5fdc-5ef4-b4a0-abadb619bf0a — Subscrib…
- 10-10T15:41 Fz45yDLd [IDLE] fix: display_url keeps ordinary long slugs; assistant maps FetchError  
    PR #1093 subscribed; waiting on CI or review
- 10-10T15:41 de7taTJU [IDLE] fix: services review lows (follow deadline order, retranscribe_many wi **NOTE**
    draft PR #1099 open; 7 CI checks queued, awaiting results
- 10-10T15:42 MHvBLJcb [IDLE] hygiene: server messages say \ 
    I'll act when a CI result or review arrives.
- 10-10T15:45 CLE4VxZj [IDLE] review-W2-2 remaining routers and schemas 
    filed GitHub issue #1106 from review findings; review ongoing
- 10-10T15:45 YcKXyiNp [ARCHIVED] review-W2-3 services: drama, library, ownership, settings, backup, res **NOTE**
    Read-only review of drama/library/ownership services: 17 findings (2 high, 7 medium, 8 low); issue draft saved for approval review done: 17 findings; issue draft awaits your go reply \
- 10-10T15:45 q4drFHZB [ARCHIVED] review-W2-4 services: transcribe, translate, review, export, line tool **NOTE**
    Review draft at scratchpad/hostile-review-issue.md: 25 ranked findings (3 HIGH), no repo edits; GitHub issue not posted. hostile review drafted: 25 findings, 3 HIGH; not posted
- 10-10T15:45 7RTwXoTc [IDLE] review-W2-9 core, segment splitting, ASR backends, whisper models 
    review done; checking for duplicate issue, then filing it
- 10-10T15:45 mXu3jxEt [ARCHIVED] review-W3-5 diagnostics, installs, disk, maintenance, upgrade, install 
    Read-only review of diagnostics/upgrade_check/install_registry filed as #1098: 23 findings (5 HIGH, 11 MEDIUM, 7 LOW) hostile review filed as #1098: 5 HIGH, 11 MED, 7 LOW
- 10-10T15:45 8C7oUYsQ [ARCHIVED] review-W3-6 scanlate, comic, novel, reader services, page server, exte **NOTE**
    Read-only review of scanlate and page_server code: 23 findings (2 HIGH, 8 MEDIUM, 13 LOW) with source cites; GitHub issue drafted in a file, not posted. read-only review done: 23 findings, 2 HIGH; issue drafted
- 10-10T15:45 hCrknRw4 [ARCHIVED] review-W3-10 translate engines, engine backends, bulk translate, LLM t 
    Read-only hostile review of translate_engines.py and engine_backends/ filed as issue #1096; no edits, commits, or pushes. engine_backends hostile review filed as issue #1096
- 10-10T15:45 c8ycqaP3 [ARCHIVED] review-W3-12 CLI and headless parity with the app 
    Shall I open the issue on zkaelxz/U00 with this body as written? Shall I open the issue on zkaelxz/U00 with this body as written?
- 10-10T15:47 5pMMKSMi [ARCHIVED] fix-1068-review-findings (restore four deleted behaviour tests) **NOTE**
    Restored H1–H4 tests (whisper patches now target core), applied L1–L5 fixes, pushed to settings-schema-one-reader; PR #1068 remains draft, counts posted as comment. PR #1068: 4 tests restored, 5 fixes pushed; still draft
- 10-10T15:48 6TuwDv7c [ARCHIVED] review-W4-16 frontend workspace stages 
    Hostile code review written up and filed as GitHub issue #1102; no code edited. read-only review filed as issue #1102
- 10-10T15:48 jim8fRvj [IDLE] review-W4-17 frontend library, settings, diagnostics, jobs, admin, api 
    review done; checking #1101 duplicate before filing
- 10-10T15:48 QV48hmua [IDLE] review-W4-18 frontend reader, comic, scanlate, manga, live, benchmark, 
    read-only frontend review; cross-checking issue #1103
- 10-10T15:48 ya5gRHvv [ARCHIVED] review-W4-19 frontend e2e and vitest coverage audit 
    Read-only frontend test audit filed as GitHub issue #1104; no code edited, committed, or pushed. frontend test audit filed as issue #1104
- 10-10T15:48 3D338J1X [RUNNING] review-W5-20 python test suite audit 
    audit: top vacuous-test claims confirmed; 3 subagents running
- 10-10T15:48 DqJSU2QH [IDLE] review-W5-21 docs versus code drift 
    docs audit still running; last output cites #1105
- 10-10T15:52 9kz5cmrC [IDLE] split-bulk-translate-batches 
    I'll respond when CI or a review lands on #1110.
- 10-10T15:52 BG9QTD7q [IDLE] split-installer-service 
    I'll respond when CI or review events arrive.
- 10-10T15:52 uW7yBzx5 [IDLE] split-auto-backup-service **NOTE**
    PR #1112 open; waiting on CI (~17 min), will fix failures
- 10-10T15:52 4R8tc1F6 [IDLE] split-disk-usage-service 
    I'll respond when a CI result or review arrives.
- 10-10T15:52 q19GezeR [IDLE] split-transcribe-service-options 
    waiting on CI
- 10-10T15:55 b83FQXhp [IDLE] senior review: architecture, security, maintainability (project-level) 
    architecture review: auth/CI checked, writing issue #1113
- 10-10T15:55 zEGqnswQ [IDLE] modernize: frontend duplication (SelectionBar, clipboard, dialogs) wit **NOTE**
    draft PR #1117 open; 8 CI checks queued, watching
- 10-10T15:55 f4Q6vnJt [IDLE] modernize: extension JS readability and duplication without behaviour  **NOTE**
    draft PR #1111 open; CI queued, watching for failures/reviews
- 10-10T15:57 8NYp4RFs [IDLE] design audit + fixes: today's UI changes (hierarchy, density, edge cas 
    PR #1120 open; 8 CI checks queued, check-in set 17:10 UTC
- 10-10T15:59 kDtM6i3M [IDLE] review-W1-1 auth, server and core routers (relaunch, files issue direc 
    Read-only hostile review of zkaelxz/U00 filed as GitHub issue #1118 filed hostile review issue #1118 on zkaelxz/U00
- 10-10T16:00 pZBYw2C1 [IDLE] review-W2-3 services: drama, library, ownership, settings, backup, res **NOTE**
    Read-only review of Baihe Studio drafted with 14 findings (3 HIGH) in scratchpad/hostile-review-issue.md; no repo changes. review drafted (14 findings); issue not posted yet reply \
- 10-10T16:00 Gshtz7Tw [IDLE] review-W2-4 services: transcribe, translate, review, export, line tool 
    Read-only hostile review of zkaelxz/U00 filed as GitHub issue #1116; no repo edits. hostile review filed as zkaelxz/U00 issue #1116
- 10-10T16:00 jQhEHXiW [IDLE] review-W3-6 scanlate, comic, novel, reader services, page server, exte 
    Read-only hostile code review of U00 filed as GitHub issue #1119 hostile review of zkaelxz/U00 filed as issue #1119
- 10-10T16:00 AWMsJRvA [IDLE] review-W3-12 CLI and headless parity with the app (relaunch) **NOTE**
    Read-only review of U00 CLI found 5 high-severity GPU-lock and overwrite bugs plus medium crash and exit-code issues; GPU, Windows, and real-model paths unverified. read-only U00 CLI review: 5 high, several medium issues
- 10-10T16:00 JHwZkNLL [IDLE] finish fix-page-server-inflight: open the draft PR, verify tests 
    Reviewed branch fix-page-server-inflight with no code changes; PR #1100 retitled; 167 and 187 tests pass page_server in-flight fix reviewed; PR #1100 tests pass
- 10-10T16:06 avagd3fc [IDLE] resolve-1068-settings-one-reader **NOTE**
    Merged origin/baihe-subtitler into settings-schema-one-reader (e618508), keeping the PR's settings.get reader; 995 tests pass; PR #1068 stays draft. merged baihe-subtitler into PR #1068 as e618508; tests pass
- 10-10T16:06 x5zpEYeF [IDLE] fix-910-settings-guard-dynamic-key **NOTE**
    Registered benchmark_judge.<session> in DYNAMIC_KEYS in tests/test_settings_schema.py and pushed to benchmark-from-reviewed; local tests pass, CI on the new head is unconfirmed. pushed DYNAMIC_KEYS fix for PR #910; CI not yet confirmed
- 10-10T16:20 kq1bxV3Z [RUNNING] B1 fix: bound hostile-input parsers in source adapters 
- 10-10T16:20 s9xUSyMZ [RUNNING] B2 fix: bound generic import, image fetches and the read deadline 
- 10-10T16:20 eQjGhbNA [RUNNING] B3 fix: diagnostics redaction and GPU status without loading torch in  
- 10-10T16:20 Sg9qftcs [RUNNING] B4 fix: install now gets the same safety as the queued install 
- 10-10T16:20 DSQDZ2kB [RUNNING] B5 fix: spend caps, privacy leaks, glossary integrity and the search-j 
- 10-10T16:20 VacXHptF [RUNNING] T1: make the frontend tests able to fail 
- 10-10T16:20 UU9Dv6Lb [RUNNING] D1 docs: fix user-facing menu paths and the content-sources drift 
- 10-10T16:22 xG6qxHat [RUNNING] F1 fix: Library, Quick translate and Diagnostics: gate member actions, 
- 10-10T16:22 CUqzAChD [RUNNING] F2 fix: session, jobs list and key form: no stale state, no lost draft 
- 10-10T16:22 8izhN36T [RUNNING] F3 fix: workspace: stop silent data loss in glossary, novel, review an 
- 10-10T16:22 pHFtrN9E [RUNNING] F4 fix: workspace: double-submit guards and stale panel state 
- 10-10T16:22 zjs6rwxn [RUNNING] F5 fix: reader, assistant, live and lab: cancellable asks, honest erro 
