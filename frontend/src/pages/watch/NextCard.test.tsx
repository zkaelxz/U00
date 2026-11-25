import { describe, expect, it } from 'vitest'

import type { NextEpisode } from '../../types/nextEpisode'
import { nextCardText, shouldPrepareNext } from './nextLogic'

const next = (state: NextEpisode['state'], free = true, drama_id: number | null = 2): NextEpisode => ({ drama_id, title: 'Ep 2', state, free })

describe('shouldPrepareNext', () => {
  it('starts a free untouched next title for someone who may start jobs', () => {
    expect(shouldPrepareNext(next('idle'), true)).toBe(true)
  })
  it('leaves a paid title, a queued one and a viewer who may not start jobs alone', () => {
    expect(shouldPrepareNext(next('idle', false), true)).toBe(false)
    expect(shouldPrepareNext(next('working'), true)).toBe(false)
    expect(shouldPrepareNext(next('idle'), false)).toBe(false)
  })
})

describe('nextCardText', () => {
  it('shows a ready title before the end and the others only at the end', () => {
    expect(nextCardText(next('ready'), false)).toBe('Ready to watch')
    expect(nextCardText(next('working'), false)).toBeNull()
    expect(nextCardText(next('working'), true)).toBe('Being translated, it will be ready soon')
    expect(nextCardText(next('idle'), true)).toBe('Not translated yet')
  })
  it('shows nothing without a next title', () => {
    expect(nextCardText(null, true)).toBeNull()
    expect(nextCardText(next('idle', true, null), true)).toBeNull()
  })
})
