import { describe, expect, it } from 'vitest'

import type { DiagnosticsInstallPresets, DiagnosticsSetupChecks, GpuStatus } from '../../types/diagnostics'
import type { TranslateEngine } from '../../types/translate'
import { alternativeEngine, preflightReady, preflightRows, type PreflightInputs, type Read } from './preflightModel'

const ok = <T,>(data: T): Read<T> => ({ data, denied: false, failed: false })
const denied = <T,>(): Read<T> => ({ data: null, denied: true, failed: false })

const presets = (installed: boolean): DiagnosticsInstallPresets => ({
  tasks: [{
    id: 'transcribe', group: 'Audio', label: 'Transcribe', help: '', packages: ['faster_whisper'],
    installed_count: installed ? 1 : 0, to_install: installed ? [] : ['faster_whisper'], approx_mb: 80,
  }],
  packages: { faster_whisper: { name: 'faster_whisper', installed } as DiagnosticsInstallPresets['packages'][string] },
})
const setup = (ffmpeg: boolean, cuda: boolean | null): DiagnosticsSetupChecks => ({
  python: { version: '3.12', ok: true }, ffmpeg: { found: ffmpeg, version: null },
  js_runtime: { found: true, name: 'deno' }, cuda: { torch_installed: true, cuda_available: cuda },
  files: { all_present: true, missing_top_level: [] }, library_writable: true,
})
const gpu = (available: boolean): GpuStatus =>
  ({ available, name: null, vram_used_gb: null, vram_total_gb: null, torch_cuda_version: null, message: null })
const engine = (name: string, key: boolean): TranslateEngine => ({ name, label: name, free: false, models: null, key_configured: key })

const allOk = (): PreflightInputs => ({
  presets: ok(presets(true)), setup: ok(setup(true, true)), gpu: ok(gpu(true)),
  engines: ok([engine('claude', true), engine('ollama', true)]), engine: 'claude',
})
const ALL = ['whisper', 'ffmpeg', 'gpu', 'key'] as const

describe('preflightRows', () => {
  it('has no rows when everything is OK', () => {
    const rows = preflightRows([...ALL], allOk())
    expect(rows).toEqual([])
    expect(preflightReady(rows)).toBe(true)
  })

  it('names a missing Whisper with its size and blocks the run', () => {
    const rows = preflightRows(['whisper'], { ...allOk(), presets: ok(presets(false)) })
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ need: 'whisper', badge: 'Missing', blocking: true, noFix: null })
    expect(rows[0].state).toContain('approx. 80 MB')
    expect(preflightReady(rows)).toBe(false)
  })

  it('names a missing key without ever carrying a value', () => {
    const rows = preflightRows(['key'], { ...allOk(), engines: ok([engine('claude', false)]) })
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ need: 'key', blocking: true })
    expect(JSON.stringify(rows)).not.toMatch(/sk-|\/home\/|[A-Z]:\\/)
  })

  it('shows a missing GPU as information that does not block', () => {
    const rows = preflightRows(['gpu'], { ...allOk(), gpu: ok(gpu(false)), setup: ok(setup(true, false)) })
    expect(rows[0]).toMatchObject({ need: 'gpu', tone: 'info', blocking: false })
    expect(preflightReady(rows)).toBe(true)
  })

  it('flags missing FFmpeg', () => {
    const rows = preflightRows(['ffmpeg'], { ...allOk(), setup: ok(setup(false, true)) })
    expect(rows[0]).toMatchObject({ need: 'ffmpeg', blocking: true })
  })

  it('on 403 falls back to the stage answer and offers no fix', () => {
    const rows = preflightRows(['whisper', 'ffmpeg', 'gpu'], {
      presets: denied(), setup: denied(), gpu: denied(), engines: ok([]), whisperInstalled: false,
    })
    expect(rows.map((r) => r.need)).toEqual(['whisper'])
    expect(rows[0].noFix).toBe('denied')
  })

  it('shows nothing for a read that failed and has no stage answer', () => {
    const failed = { data: null, denied: false, failed: true }
    expect(preflightRows([...ALL], { presets: failed, setup: failed, gpu: failed, engines: failed, engine: 'claude' })).toEqual([])
  })
})

describe('alternativeEngine', () => {
  it('offers Ollama only when it can run and is not already chosen', () => {
    expect(alternativeEngine([engine('ollama', true)], 'claude')).toBe('ollama')
    expect(alternativeEngine([engine('ollama', false)], 'claude')).toBeNull()
    expect(alternativeEngine([engine('ollama', true)], 'ollama')).toBeNull()
    expect(alternativeEngine(null, 'claude')).toBeNull()
  })
})
