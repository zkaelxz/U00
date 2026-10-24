import { describe, expect, it } from 'vitest'

import type { Need } from './needs'
import { installAllPlan } from './useNeedInstall'

const need = (over: Partial<Need>): Need => ({ id: 'x', label: 'X', reason: 'X is missing.', action: 'diagnostics', packages: [], ...over })

describe('installAllPlan', () => {
  it('puts GPU PyTorch ahead of the packages, which install together afterwards', () => {
    const plan = installAllPlan([
      need({ action: 'install_whisper', packages: ['faster_whisper'] }),
      need({ action: 'install_gpu_torch' }),
      need({ packages: ['demucs', 'audio-separator'] }),
    ], ['faster_whisper'])
    expect(plan).toEqual({ gpuTorch: true, keys: ['faster_whisper', 'demucs', 'audio-separator'] })
  })

  it('lists a shared package once and skips tokens and separate programs', () => {
    const plan = installAllPlan([
      need({ packages: ['pyannote.audio'] }), need({ packages: ['pyannote.audio'] }),
      need({ action: 'settings' }), need({ action: 'external' }),
    ], [])
    expect(plan).toEqual({ gpuTorch: false, keys: ['pyannote.audio'] })
  })
})
