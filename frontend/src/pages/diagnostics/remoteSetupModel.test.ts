import { describe, expect, it } from 'vitest'

import type { RemoteSetup } from '../../types/diagnostics'
import { redirectAddress, setupSummary, signInProblem, stepBadge } from './remoteSetupModel'

const step = (id: string, state: RemoteSetup['steps'][number]['state']) =>
  ({ id, title: `Step ${id}`, state, message: '', who: 'you' }) as RemoteSetup['steps'][number]

const base = { fields: { public_url: false, google_client_id: false, google_client_secret: false }, restart_needed: false }

describe('remote setup model', () => {
  it('names the next step and how many are done', () => {
    const s = { ...base, steps: [step('a', 'done'), step('b', 'todo')], next: 'b' }
    expect(setupSummary(s)).toBe('1 of 2 done · next: Step b')
  })
  it('says all done when nothing is next', () => {
    expect(setupSummary({ ...base, steps: [step('a', 'done')], next: null })).toBe('All steps done')
  })
  it('says all set on this PC when only steps it cannot see are left', () => {
    expect(setupSummary({ ...base, steps: [step('a', 'done'), step('router', 'manual')], next: 'router' }))
      .toBe('All set on this PC · check once from a phone')
  })
  it('badges a problem as bad and a manual step as neutral', () => {
    expect(stepBadge({ state: 'problem' }).tone).toBe('bad')
    expect(stepBadge({ state: 'manual' }).tone).toBe('neutral')
  })
})

describe('sign-in drafts', () => {
  it('builds the redirect address from the typed name', () => {
    expect(redirectAddress('https://Baihe.Example.com/')).toBe('https://baihe.example.com/api/auth/callback')
    expect(redirectAddress('192')).toBeNull()
    expect(redirectAddress('baihe.example.com:8443')).toBeNull()
  })
  it('catches a secret pasted into the id box', () => {
    expect(signInProblem({ name: '', id: 'GOCSPX-abc', secret: '' })).toMatch(/ends with/)
    expect(signInProblem({ name: '', id: '1-a.apps.googleusercontent.com', secret: 'x y' })).toMatch(/spaces/)
    expect(signInProblem({ name: 'baihe.example.com', id: '', secret: '' })).toBeNull()
  })
})
