import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { DiagnosticsSetupChecks, GpuStatus, ModelEngineVersion } from '../../types/diagnostics'
import {
  LOST_CONTACT_INSTALL, adminErrorText, bugBundleReplayText, bugBundleTitle, busyLine, copyFallbackText,
  extensionEngineNote, extensionSummary, extensionToggleNote,
  headerBadges, historySummary, installBlockedReason, installConfirmLabel, installResultText, installableEngines,
  isInstallable, libraryStatsLine, logEmptyText, modelCacheSummary, pyannoteSummary, resetBlockedReason,
  setupRows, setupSummary,
} from './diagnosticsAdmin'

const checks = (o: Partial<DiagnosticsSetupChecks> = {}): DiagnosticsSetupChecks => ({
  python: { version: '3.11.9', ok: true },
  ffmpeg: { found: true, version: '6.1' },
  js_runtime: { found: true, name: 'deno' },
  cuda: { torch_installed: false, cuda_available: null },
  files: { all_present: true, missing_top_level: [], missing_tabs: [] },
  library_writable: true,
  ...o,
})
const gpu: GpuStatus = {
  available: false, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: 'No GPU.',
}
const err = (status: number, code: string, message: string) => new ApiError(status, { code, message })

describe('setup rows', () => {
  it('reads "Label: value" when everything is fine', () => {
    const rows = setupRows(checks(), gpu)
    expect(rows.map((r) => r.text)).toEqual([
      'Python: 3.11.9', 'ffmpeg: 6.1', 'JS runtime: deno', 'GPU: No GPU.', 'App files: all present',
      'Library folder: writable',
    ])
    expect(rows[0]).toMatchObject({ label: 'Python', value: '3.11.9', problem: false })
    expect(setupSummary(rows)).toBe('All 6 OK')
  })

  it('turns failures into "Problem: …" rows and names them in the summary', () => {
    const rows = setupRows(checks({
      python: { version: '3.9.1', ok: false },
      ffmpeg: { found: false, version: null },
      js_runtime: { found: false, name: null },
      cuda: { torch_installed: true, cuda_available: false },
      files: { all_present: false, missing_top_level: ['a.py', 'b.py'], missing_tabs: ['c.py'] },
      library_writable: false,
    }), gpu)
    expect(rows.every((r) => r.problem)).toBe(true)
    expect(rows.map((r) => r.text)).toEqual([
      'Problem: Python 3.9.1 is too old',
      'Problem: ffmpeg not found',
      'Problem: no JS runtime (some video sites lose formats)',
      "Problem: PyTorch can't see the GPU",
      'Problem: 3 missing',
      "Problem: can't be written to",
    ])
    expect(rows[1]).toMatchObject({ label: 'ffmpeg', value: 'ffmpeg not found' })
    expect(setupSummary(rows.slice(1, 3))).toBe('2 problems: ffmpeg, JS runtime')
    expect(setupSummary(rows.slice(1, 2))).toBe('1 problem: ffmpeg')
  })

  it('checks ffmpeg for libass and marks the core rows', () => {
    const withLibass = setupRows(checks({ ffmpeg: { found: true, version: '6.1', libass: true } }), gpu)
    expect(withLibass[1]).toMatchObject({ text: 'ffmpeg: 6.1 (with libass)', problem: false })
    const noLibass = setupRows(checks({ ffmpeg: { found: true, version: '6.1', libass: false } }), gpu)
    expect(noLibass[1]).toMatchObject({ problem: true, text: expect.stringContaining('no libass') })
    expect(noLibass.filter((r) => r.core).map((r) => r.key)).toEqual(['python', 'ffmpeg', 'js'])
    // Unknown (an older API or a failed version check) is not a problem.
    expect(setupRows(checks({ ffmpeg: { found: true, version: '6.1', libass: null } }), gpu)[1].problem).toBe(false)
  })

  it('titles bug bundles and describes their last replay', () => {
    const b = { id: 4, label: 'Bad pronoun', drama_title: 'Signal', replayed: false, replay_output: null, reproduced: null }
    expect(bugBundleTitle(b)).toBe('#4 Bad pronoun · Signal')
    expect(bugBundleTitle({ ...b, label: '', drama_title: null })).toBe('#4 Untitled · (deleted drama)')
    expect(bugBundleReplayText(b)).toBeNull()
    expect(bugBundleReplayText({ ...b, replayed: true, replay_output: 'x', reproduced: true }))
      .toBe('Still reproduces the same output. Last replay: x')
    expect(bugBundleReplayText({ ...b, replayed: true, replay_output: 'y', reproduced: false }))
      .toBe('No longer reproduces: the output changed. Last replay: y')
  })

  it('skips the GPU row until the overview has loaded', () => {
    expect(setupRows(checks(), null).map((r) => r.key)).not.toContain('gpu')
  })
})

describe('header', () => {
  it('joins setup, packages and running jobs', () => {
    const texts = (...a: Parameters<typeof headerBadges>) => headerBadges(...a).map((b) => `${b.text} (${b.tone})`)
    expect(texts(0, 22, 25, 1)).toEqual(['Setup OK (ok)', '22 of 25 packages (neutral)', '1 job running (info)'])
    expect(texts(2, 22, 25, 0)).toEqual(['2 setup problems (warn)', '22 of 25 packages (neutral)', 'No jobs running (neutral)'])
    expect(texts(1, null, null, 3)).toEqual(['1 setup problem (warn)', '3 jobs running (info)'])
    expect(texts(null, null, null, null)).toEqual([])
    expect(texts(0, 22, 25, 0, { kind: 'install', name: 'yt-dlp' })[3]).toBe('Installing yt-dlp (info)')
    expect(texts(0, 22, 25, 0, { kind: 'upgrade', name: 'jieba' })[3]).toBe('Updating jieba (info)')
    expect(texts(0, 22, 25, 0, { kind: 'reset', name: 'library' })).toHaveLength(3)
  })
})

describe('packages', () => {
  it('only feature and engine tiers are installable', () => {
    expect(['feature', 'engine', 'required', 'dev'].map(isInstallable)).toEqual([true, true, false, false])
  })

  it('warns about the size of torch downloads only', () => {
    expect(installConfirmLabel('torch')).toBe('Confirm install torch (about 2.5 GB)')
    expect(installConfirmLabel('torchaudio')).toBe('Confirm install torchaudio (about 2.5 GB)')
    expect(installConfirmLabel('jieba')).toBeUndefined()
  })

  it('lists uninstalled model engines with a package not already shown', () => {
    const e = (name: string, installed: boolean, pkg: string | null): ModelEngineVersion =>
      ({ name, installed, package: pkg, version: null, url: null, help: null })
    const out = installableEngines(
      [e('A', false, 'a-pkg'), e('B', true, 'b-pkg'), e('C', false, null), e('D', false, 'pyannote.audio')],
      ['pyannote.audio'],
    )
    expect(out.map((m) => m.name)).toEqual(['A'])
  })

  it('gives one blocked reason for install and for reset', () => {
    expect(installBlockedReason(false, null)).toBeNull()
    expect(installBlockedReason(true, null)).toBe('Wait for running jobs to finish before installing.')
    expect(installBlockedReason(false, { kind: 'install', name: 'x' })).toBe('Wait for the install to finish.')
    expect(installBlockedReason(false, { kind: 'reset', name: 'library' })).toBe('Wait for the reset to finish.')
    expect(resetBlockedReason(true, null)).toBe('Stop running jobs first (see Jobs above).')
    expect(resetBlockedReason(false, { kind: 'upgrade', name: 'x' })).toBe('Wait for the install to finish.')
    expect(resetBlockedReason(false, { kind: 'reset', name: 'library' })).toBeNull()
  })

  it('announces a running install or upgrade, never a reset', () => {
    expect(busyLine({ kind: 'install', name: 'jieba' })).toBe(
      'Installing jieba… this can take several minutes. Keep this tab open.')
    expect(busyLine({ kind: 'upgrade', name: 'jieba' })).toMatch(/^Updating jieba…/)
    expect(busyLine({ kind: 'reset', name: 'library' })).toBeNull()
    expect(busyLine(null)).toBeNull()
  })

  it('words results', () => {
    expect(installResultText('install', 'x', true)).toBe('Installed x.')
    expect(installResultText('upgrade', 'x', true)).toBe('Updated x. Restart Baihe to load the new version.')
    expect(installResultText('install', 'x', false)).toBe('Install failed for x.')
    expect(installResultText('upgrade', 'x', false)).toBe('Update failed for x.')
  })

  it('maps admin errors to one sentence', () => {
    const busy = 'A background job is running or queued; wait for it to finish.'
    expect(adminErrorText(err(409, 'conflict', busy), 'install')).toBe(busy)
    expect(adminErrorText(err(404, 'not_found', 'whatever'), 'install')).toBe('Unknown or non-installable package.')
    expect(adminErrorText(err(403, 'forbidden', 'x'), 'upgrade')).toBe('This only works on the main PC.')
    expect(adminErrorText(err(0, 'network_error', 'x'), 'install')).toBe(LOST_CONTACT_INSTALL)
    expect(adminErrorText(err(422, 'invalid_input', 'Needs confirm_text "RESET".'), 'reset'))
      .toBe('Needs confirm_text "RESET".')
    // A path-like server message never reaches the page.
    expect(adminErrorText(err(409, 'conflict', 'Locked by /home/kae/lib'), 'reset'))
      .toBe('That cannot be done right now because something else is already using it.')
    expect(adminErrorText(err(500, 'internal_error', 'boom'), 'reset'))
      .toBe('Something went wrong inside Baihe. Details are in the app log.')
  })
})

describe('other sections', () => {
  it('summarises speaker detection', () => {
    const base = { pyannote_installed: true, hf_token_configured: true, models: null, ready: true }
    expect(pyannoteSummary(base)).toBe('Ready')
    expect(pyannoteSummary({ ...base, ready: false, pyannote_installed: false })).toBe('Not ready: pyannote missing')
    expect(pyannoteSummary({ ...base, ready: false, hf_token_configured: false })).toBe('Not ready: no Hugging Face token')
  })

  it('summarises the model cache', () => {
    expect(modelCacheSummary({
      hf_cache: Array.from({ length: 7 }, (_, i) => ({ repo_id: `r${i}`, repo_type: 'model', revision: 'x', size_bytes: 1 })),
      hf_total_bytes: 12_000_000_000,
      piper_voices: [{ voice: 'a', size_bytes: 1 }, { voice: 'b', size_bytes: 1 }],
      piper_total_bytes: 400_000_000,
    })).toBe('12.4 GB · 7 models · 2 voices')
  })

  it('summarises a history entry', () => {
    expect(historySummary({
      job_id: 'j', label: 'Translate', status: 'done', description: null, message: '', error: null,
      gpu_touching: true, started_at: 1, finished_at: 186, duration_seconds: 185,
    })).toBe('Translate · Done · 3m 05s · GPU')
  })

  it('log and copy copy', () => {
    expect(logEmptyText('ERROR')).toBe('No lines match.')
    expect(logEmptyText('  ')).toBe('Nothing logged yet.')
    expect(copyFallbackText(false)).toBe('Press Ctrl+C to copy.')
    expect(copyFallbackText(true)).toBe('Long-press to copy.')
  })

  it('states library size before a reset', () => {
    expect(libraryStatsLine({ total_dramas: 12, total_lines: 48210 })).toBe('Currently 12 dramas, 48,210 lines.')
    expect(libraryStatsLine({ total_dramas: 1, total_lines: 1 })).toBe('Currently 1 drama, 1 line.')
    expect(libraryStatsLine({ total_dramas: 0, total_lines: 0 })).toBeNull()
  })
})

describe('browser extension', () => {
  it('engine note says what happens to a page', () => {
    const base = { model: null, engines: [] }
    expect(extensionEngineNote({ ...base, engine: null, ready: false })).toBe(
      'No engine: pages come back with their original text only.')
    expect(extensionEngineNote({ ...base, engine: 'claude', ready: false })).toBe(
      'No Claude key is saved on this PC, so pages come back untranslated.')
    expect(extensionEngineNote({ ...base, engine: 'claude', ready: true })).toBe(
      'Pages are translated with Claude. The key stays on this PC.')
  })


  it('summarises every enabled/running pair', () => {
    expect(extensionSummary({ enabled: true, running: true })).toBe('On · running')
    expect(extensionSummary({ enabled: true, running: false })).toBe('On · starts next time Baihe starts')
    expect(extensionSummary({ enabled: false, running: true })).toBe('Off · still running until Baihe restarts')
    expect(extensionSummary({ enabled: false, running: false })).toBe('Off')
  })

  it('notes what a toggle did', () => {
    expect(extensionToggleNote({ enabled: false, running: true, restart_needed: true })).toBe('Off. Restart Baihe to stop it now.')
    expect(extensionToggleNote({ enabled: true, running: false, restart_needed: false })).toBe('On. It starts next time Baihe starts.')
    expect(extensionToggleNote({ enabled: true, running: true, restart_needed: false })).toBeNull()
    expect(extensionToggleNote({ enabled: false, running: false, restart_needed: false })).toBeNull()
  })
})
