import { describe, expect, it } from 'vitest'

import type { DiagnosticsGpuTorchStatus, DiagnosticsSetupChecks } from '../../types/diagnostics'
import { hardwareLines } from './RecommendedInstalls'

const setup = (over: Partial<DiagnosticsSetupChecks> = {}) => ({
  ffmpeg: { found: true, version: '6.1', libass: true }, js_runtime: { found: true, name: 'deno' }, ...over,
}) as DiagnosticsSetupChecks
const gpu = (found: boolean) => ({ nvidia: { found, gpu_name: found ? 'NVIDIA Tesla V100' : null, driver_version: found ? '570.65' : null } }) as unknown as DiagnosticsGpuTorchStatus

describe('hardwareLines', () => {
  it('names the GPU and driver, FFmpeg with libass, and the JavaScript runtime', () => {
    const lines = hardwareLines(setup(), gpu(true))
    expect(lines.map((l) => l.label)).toEqual(['GPU', 'FFmpeg', 'JavaScript runtime'])
    expect(lines[0]).toMatchObject({ ok: true, value: 'NVIDIA Tesla V100, driver 570.65' })
    expect(lines[1]).toMatchObject({ ok: true, value: 'Found, with libass' })
    expect(lines[2]).toMatchObject({ ok: true, value: 'Found (deno)' })
  })

  it('says plainly what is missing', () => {
    const lines = hardwareLines(setup({ ffmpeg: { found: true, version: '6.1', libass: false }, js_runtime: { found: false, name: null } }), gpu(false))
    expect(lines[0].ok).toBe(false)
    expect(lines[0].value).toContain('No NVIDIA GPU found')
    expect(lines[1]).toMatchObject({ ok: false })
    expect(lines[1].value).toContain('without libass')
    expect(lines[2].ok).toBe(false)
  })

  it('shows "checking" until the reads arrive, never a guess', () => {
    const lines = hardwareLines(null, null)
    expect(lines.every((l) => !l.ok && l.value === 'checking…')).toBe(true)
  })

  it('FFmpeg that is missing reads Not found', () => {
    expect(hardwareLines(setup({ ffmpeg: { found: false, version: null, libass: null } }), gpu(true))[1].value).toBe('Not found')
  })
})
