import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../api/client'
import type { PendingInstallPlan } from '../../types/pendingInstall'
import { previewInstall } from './useInstallPlan'

const plan = (o: Partial<PendingInstallPlan> = {}): PendingInstallPlan => ({
  packages: ['faster_whisper'], available: true, mode: 'now', changes: [], summary: [], loaded: [], blocked: [],
  needs_confirm: [], note: null, ...o,
})

describe('previewInstall', () => {
  it('asks the server about exactly the keys it is given', async () => {
    const ask = vi.fn().mockResolvedValue(plan())
    await previewInstall(['a', 'b'], ask)
    expect(ask).toHaveBeenCalledWith(['a', 'b'])
  })

  it('goes straight ahead for a plain fresh install', async () => {
    expect(await previewInstall(['x'], async () => plan())).toEqual({ kind: 'go' })
  })

  it.each([
    ['a risk', { needs_confirm: ['numpy is a downgrade.'] }],
    ['a restart', { mode: 'restart' as const }],
    ['a refusal', { blocked: ['two OpenCV packages'] }],
  ])('shows the panel for %s', async (_n, o) => {
    const p = plan(o)
    expect(await previewInstall(['x'], async () => p)).toEqual({ kind: 'panel', plan: p })
  })

  it('goes ahead when this server has no plan endpoint', async () => {
    expect(await previewInstall(['x'], async () => { throw new ApiError(404, { code: 'not_found', message: 'no' }) })).toEqual({ kind: 'go' })
  })

  it('does not install when the check itself failed', async () => {
    const e = new ApiError(409, { code: 'conflict', message: 'A job is running.' })
    expect(await previewInstall(['x'], async () => { throw e })).toEqual({ kind: 'failed', error: e })
  })
})
