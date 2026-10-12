import { describe, expect, it } from 'vitest'

import type { DiagnosticsGpuTorchStatus } from '../../types/diagnostics'
import { gpuBannerState } from './gpuBanner'

const status = (o: { found?: boolean; build?: 'cuda' | 'cpu' | null } = {}): DiagnosticsGpuTorchStatus => ({
  nvidia: { found: o.found ?? true },
  installed: [{ name: 'torch', version: '2.11.0', build: o.build === undefined ? 'cuda' : o.build }],
  problems: [], state: 'recommended', python_supported: true,
} as unknown as DiagnosticsGpuTorchStatus)

describe('gpuBannerState', () => {
  it('offers Turn on GPU with CUDA torch, an NVIDIA GPU and the setting off', () => {
    expect(gpuBannerState(status(), false, 'local')).toBe('turn_on')
    expect(gpuBannerState(status(), false, 'unknown')).toBe('turn_on')
  })

  it('only says where to turn it on away from the main PC', () => {
    expect(gpuBannerState(status(), false, 'remote')).toBe('ask_pc')
  })

  it('hides once the setting is on, or when it could not be read', () => {
    expect(gpuBannerState(status(), true, 'local')).toBeNull()
    expect(gpuBannerState(status(), null, 'local')).toBeNull()
  })

  it('shows nothing for CPU-only torch, no torch or no NVIDIA GPU', () => {
    expect(gpuBannerState(status({ build: 'cpu' }), false, 'local')).toBeNull()
    expect(gpuBannerState(status({ build: null }), false, 'local')).toBeNull()
    expect(gpuBannerState(status({ found: false }), false, 'local')).toBeNull()
    expect(gpuBannerState(null, false, 'local')).toBeNull()
  })
})
