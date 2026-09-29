import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ApiError } from '../api/client'
import { ConfirmButton } from './ConfirmButton'
import { CONFIRM_REVERT_MS, armedAnnouncement, confirmLabelFor, confirmStep, replay } from './confirmButton'
import { PC_ONLY_FORBIDDEN, describeError } from './errorMessages'

describe('confirmStep', () => {
  it('first press only arms; second press runs', () => {
    expect(confirmStep(false, 'press')).toEqual({ armed: true, run: false })
    expect(confirmStep(true, 'press')).toEqual({ armed: false, run: true })
  })

  it.each(['cancel', 'timeout', 'blur', 'escape', 'blocked'] as const)('%s disarms without running', (ev) => {
    expect(confirmStep(true, ev)).toEqual({ armed: false, run: false })
  })

  it('arm, disable, enable, press once: no call', () => {
    expect(replay([
      { event: 'press' },
      { event: 'blocked', blocked: true },
      { event: 'press' },
    ])).toBe(0)
    // A press while blocked never runs either.
    expect(replay([{ event: 'press' }, { event: 'press', blocked: true }])).toBe(0)
    expect(replay([{ event: 'press' }, { event: 'press' }])).toBe(1)
  })

  it('labels and timing', () => {
    expect(confirmLabelFor('Preset A')).toBe('Confirm delete Preset A')
    expect(armedAnnouncement('Preset A')).toBe('Press again to delete Preset A')
    expect(CONFIRM_REVERT_MS).toBe(5000)
  })
})

describe('ConfirmButton', () => {
  it('renders the first step with a named label and an empty live region', () => {
    const out = renderToStaticMarkup(createElement(ConfirmButton, { name: 'Voice B', onConfirm: () => {} }))
    expect(out).toContain('>Delete…</button>')
    expect(out).toContain('aria-label="Delete Voice B"')
    expect(out).toContain('aria-live="polite"')
    expect(out).not.toContain('Confirm delete')
  })

  it('busy: disabled first step, Working…, aria-busy', () => {
    const out = renderToStaticMarkup(createElement(ConfirmButton, { name: 'V', busy: true, onConfirm: () => {} }))
    expect(out).toContain('aria-busy="true"')
    expect(out).toMatch(/<button[^>]*disabled=""[^>]*>Working…<\/button>/)
  })
})

describe('describeError additions', () => {
  const err = (status: number, code: string, message = 'Restoring needs RESTORE.') =>
    new ApiError(status, { code, message })

  it('has plain copy for forbidden, unauthenticated and rate_limited', () => {
    expect(describeError(err(403, 'forbidden')).title).toBe('Not allowed from this device or account.')
    expect(describeError(err(401, 'unauthenticated')).title).toBe('Your session has ended. Sign in again.')
    expect(describeError(err(429, 'rate_limited')).title).toBe('Too many requests. Wait a moment and try again.')
  })

  it('PC-only callers get the main-PC copy on 403', () => {
    expect(describeError(err(403, 'forbidden'), { pcOnly: true }).title).toBe(PC_ONLY_FORBIDDEN)
  })

  it('serverText opts validation errors into the server text, still filtered', () => {
    expect(describeError(err(422, 'validation_error')).detail).toBeNull()
    expect(describeError(err(422, 'validation_error'), { serverText: true }).detail).toBe('Restoring needs RESTORE.')
    expect(describeError(err(422, 'validation_error', 'bad /home/kae/x.zip'), { serverText: true }).detail).toBeNull()
  })
})
