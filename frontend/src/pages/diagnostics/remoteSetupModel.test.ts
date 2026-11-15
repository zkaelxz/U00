import { describe, expect, it } from 'vitest'

import type { RemoteSetup } from '../../types/diagnostics'
import { setupSummary, stepBadge } from './remoteSetupModel'

const step = (id: string, state: RemoteSetup['steps'][number]['state']) =>
  ({ id, title: `Step ${id}`, state, message: '', who: 'you' }) as RemoteSetup['steps'][number]

describe('remote setup model', () => {
  it('names the next step and how many are done', () => {
    const s: RemoteSetup = { steps: [step('a', 'done'), step('b', 'todo')], next: 'b' }
    expect(setupSummary(s)).toBe('1 of 2 done · next: Step b')
  })
  it('says all done when nothing is next', () => {
    expect(setupSummary({ steps: [step('a', 'done')], next: null })).toBe('All steps done')
  })
  it('badges a problem as bad and a manual step as neutral', () => {
    expect(stepBadge({ state: 'problem' }).tone).toBe('bad')
    expect(stepBadge({ state: 'manual' }).tone).toBe('neutral')
  })
})
