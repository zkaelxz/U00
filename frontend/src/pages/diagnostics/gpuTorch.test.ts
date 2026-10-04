import { describe, expect, it } from 'vitest'

import type { DiagnosticsGpuTorchStatus } from '../../types/diagnostics'
import {
  driverText, gpuTorchSummary, packageVersionText, setupBlockedReason, setupConfirmLabel, stateBadge, stateIsProblem, stateText,
  verifyText,
} from './gpuTorch'

const cu128 = {
  variant: 'cu128' as const, label: 'NVIDIA GPU (CUDA 12.8)', index_url: 'https://download.pytorch.org/whl/cu128',
  versions: { torch: '2.11.0+cu128', torchvision: '0.26.0+cu128', torchaudio: '2.11.0+cu128' }, needs_nvidia: true,
}
const cpu = { ...cu128, variant: 'cpu' as const, label: 'CPU only', needs_nvidia: false }

const status = (o: Partial<DiagnosticsGpuTorchStatus> = {}): DiagnosticsGpuTorchStatus => ({
  nvidia: { found: true, gpu_name: 'RTX 3080 Ti', driver_version: '581.42', status: 'ok', recommended: '570.65', minimum: '528.33' },
  installed: [], problems: [], state: 'missing', python_supported: true, recommended: cu128, variants: [cu128, cpu],
  probe: null, ...o,
})

describe('gpuTorch', () => {
  it('describes the driver', () => {
    expect(driverText(status().nvidia)).toBe('RTX 3080 Ti, driver 581.42')
    expect(driverText({ ...status().nvidia, status: 'too_old', driver_version: '522.06' })).toContain('too old for CUDA 12.8')
    expect(driverText({ ...status().nvidia, status: 'old' })).toContain('570.65 or newer is recommended')
    expect(driverText({ ...status().nvidia, found: false })).toContain('No NVIDIA GPU')
  })

  it('labels builds', () => {
    expect(packageVersionText({ name: 'torch', version: '2.11.0+cu128', build: 'cuda' })).toBe('2.11.0+cu128 (CUDA build)')
    expect(packageVersionText({ name: 'torch', version: '2.11.0+cpu', build: 'cpu' })).toBe('2.11.0+cpu (CPU only)')
    expect(packageVersionText({ name: 'torch', version: null, build: null })).toBe('Not installed')
  })

  it('states and problems', () => {
    expect(stateText(status({ state: 'cpu_on_gpu' }))).toContain('CPU only')
    expect(stateIsProblem(status({ state: 'mismatched' }))).toBe(true)
    expect(stateIsProblem(status({ state: 'recommended' }))).toBe(false)
  })

  it('blocks setup without a GPU, with an old driver or an unsupported Python', () => {
    expect(setupBlockedReason(status(), cu128)).toBeNull()
    expect(setupBlockedReason(status({ nvidia: { ...status().nvidia, found: false } }), cu128)).toContain('No NVIDIA GPU')
    expect(setupBlockedReason(status({ nvidia: { ...status().nvidia, found: false } }), cpu)).toBeNull()
    expect(setupBlockedReason(status({ nvidia: { ...status().nvidia, status: 'too_old' } }), cu128)).toContain('driver')
    expect(setupBlockedReason(status({ python_supported: false }), cpu)).toContain('Python')
    expect(setupConfirmLabel(cu128)).toBe('Confirm install GPU PyTorch (about 2.5 GB)')
  })

  it('describes a CUDA check', () => {
    const v = { torch: '2.11.0+cu128', torchvision: null, torchaudio: null, cuda_build: '12.8', cuda_available: true, device: 'RTX', error: null }
    expect(verifyText(v)).toBe('PyTorch 2.11.0+cu128: CUDA works on RTX.')
    expect(verifyText({ ...v, cuda_available: false })).toContain('no GPU is available')
    expect(verifyText({ ...v, cuda_available: false, cuda_build: null })).toContain('CPU-only')
    expect(verifyText({ ...v, torch: null, error: 'ImportError: x' })).toBe("PyTorch didn't import: ImportError: x")
  })

  it('gives each state a short badge', () => {
    expect(stateBadge(status())).toEqual({ text: 'Not installed', tone: 'neutral' })
    expect(stateBadge(status({ state: 'mismatched' }))).toEqual({ text: "Versions don't match", tone: 'warn' })
    expect(stateBadge(status({ state: 'cpu_on_gpu' })).tone).toBe('warn')
    expect(stateBadge(status({ state: 'recommended' }))).toEqual({ text: 'Recommended set', tone: 'ok' })
    expect(stateBadge(status({ state: 'different' })).tone).toBe('neutral')
    const oldDriver = { ...status().nvidia, status: 'too_old' as const }
    expect(stateBadge(status({ state: 'recommended', nvidia: oldDriver }))).toEqual({ text: 'Driver too old', tone: 'warn' })
    expect(stateBadge(status({ nvidia: oldDriver })).text).toBe('Not installed')
  })

  it('summarises the fold in one line', () => {
    const installed = [{ name: 'torch', version: '2.11.0+cu128', build: 'cuda' as const }]
    expect(gpuTorchSummary(status({ installed, state: 'recommended' }))).toBe('PyTorch 2.11.0+cu128 · CUDA build · RTX 3080 Ti')
    expect(gpuTorchSummary(status({ installed, probe: { torch: '2.11.0', cuda_available: true, cuda_build: '12.8', device: 'RTX 3080 Ti', error: null } as never })))
      .toContain('CUDA OK')
    expect(gpuTorchSummary(status({ nvidia: { ...status().nvidia, found: false, gpu_name: null } }))).toBe('PyTorch not installed · no NVIDIA GPU')
  })
})
