import { describe, expect, it } from 'vitest'

import { ApiError } from '../../api/client'
import type { DiagnosticsSetupChecks, GpuStatus, ModelEngineVersion } from '../../types/diagnostics'
import {
  LOST_CONTACT_INSTALL, adminErrorText, busyLine, copyFallbackText,
  extensionEngineNote, extensionSummary, extensionToggleNote,
  headerBadges, installBlockedReason, installConfirmLabel, installResultText, installableEngines,
  isInstallable, libraryStatsLine, logEmptyText, modelCacheSummary, pyannoteSummary, reconcileModels, resetBlockedReason,
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
  it('adds a browser row only when the server reports one', () => {
    expect(setupRows(checks(), gpu).map((r) => r.key)).not.toContain('browser')
    const ok = setupRows(checks({ browser: { found: true, name: 'Chrome' } }), gpu).find((r) => r.key === 'browser')
    expect(ok?.text).toBe('Browser for JavaScript-only sites: found (Chrome)')
    const bad = setupRows(checks({ browser: { found: false, name: null } }), gpu).find((r) => r.key === 'browser')
    expect(bad?.problem).toBe(true)
  })

  it('reads "Label: value" when everything is fine', () => {
    const rows = setupRows(checks(), gpu)
    expect(rows.map((r) => r.text)).toEqual([
      'Python: 3.11.9', 'FFmpeg: 6.1', 'JS runtime: deno', 'GPU: No GPU.', 'App files: all present',
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
      'Problem: FFmpeg not found',
      'Problem: no JS runtime (some video sites lose formats)',
      "Problem: PyTorch can't see the GPU",
      'Problem: 3 missing',
      "Problem: can't be written to",
    ])
    expect(rows[1]).toMatchObject({ label: 'FFmpeg', value: 'FFmpeg not found' })
    expect(setupSummary(rows.slice(1, 3))).toBe('2 problems: FFmpeg, JS runtime')
    expect(setupSummary(rows.slice(1, 2))).toBe('1 problem: FFmpeg')
  })

  it('checks ffmpeg for libass', () => {
    const withLibass = setupRows(checks({ ffmpeg: { found: true, version: '6.1', libass: true } }), gpu)
    expect(withLibass[1]).toMatchObject({ text: 'FFmpeg: 6.1 (with libass)', problem: false })
    const noLibass = setupRows(checks({ ffmpeg: { found: true, version: '6.1', libass: false } }), gpu)
    expect(noLibass[1]).toMatchObject({ problem: true, text: expect.stringContaining('no libass') })
    // Unknown (an older API or a failed version check) is not a problem.
    expect(setupRows(checks({ ffmpeg: { found: true, version: '6.1', libass: null } }), gpu)[1].problem).toBe(false)
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

describe('reconcileModels', () => {
  const eng = (name: string, o: Partial<ModelEngineVersion> = {}): ModelEngineVersion =>
    ({ name, version: '1.0', url: null, installed: true, package: name, help: null, ...o })
  const hf = (repo_id: string, revision: string) => ({ repo_id, repo_type: 'model', revision, size_bytes: 1 })

  it('puts each download under its engine and the rest in other', () => {
    const engines = [
      eng('Whisper (faster-whisper)'),
      eng('pyannote diarization model', { package: null, version: 'pyannote/speaker-diarization-3.1, pyannote/segmentation-3.0' }),
      eng('Qwen3-ASR', { installed: false, version: 'not installed' }),
      eng('pydub'),
    ]
    const { rows, other } = reconcileModels(engines, [
      hf('Systran/faster-whisper-large-v3', 'a'), hf('Systran/faster-whisper-small', 'b'),
      hf('pyannote/speaker-diarization-3.1', 'c'), hf('someone/unknown', 'd'),
    ])
    expect(rows[0].cached.map((c) => c.revision)).toEqual(['a', 'b'])
    expect(rows[1].cached.map((c) => c.revision)).toEqual(['c'])
    expect(other.map((c) => c.revision)).toEqual(['d'])
    expect(rows.map((r) => r.notDownloaded)).toEqual([false, false, false, false])
  })

  it('flags an installed weight-downloading engine with nothing cached', () => {
    const { rows } = reconcileModels([eng('Whisper (faster-whisper)'), eng('Qwen3-ASR', { installed: false }), eng('pydub')], [])
    expect(rows.map((r) => r.notDownloaded)).toEqual([true, false, false])
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
      model_files: [],
      model_files_total_bytes: 0,
    })).toBe('12.0 GB · 7 models')
    expect(modelCacheSummary({
      hf_cache: [], hf_total_bytes: 0,
      model_files: [{ folder: 'torch', name: 'htdemucs.th', size_bytes: 1 }],
      model_files_total_bytes: 84_000_000,
    })).toBe('84.0 MB · 1 model file')
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
    expect(extensionToggleNote({ enabled: false, running: true, restart_needed: true }))
      .toBe('Off, but it could not be stopped. Restart Baihe to stop it.')
    expect(extensionToggleNote({ enabled: true, running: false, restart_needed: false })).toBe('On. It starts next time Baihe starts.')
    expect(extensionToggleNote({ enabled: true, running: true, restart_needed: false })).toBeNull()
    expect(extensionToggleNote({ enabled: false, running: false, restart_needed: false }))
      .toBe("Off. The extension can't reach Baihe now.")
  })
})
