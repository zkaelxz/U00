import { describe, expect, it } from 'vitest'

import { SHARE_CHOICES, castLabel, castUrl, reachableByTv, shareNote } from './castLogic'

describe('reachableByTv', () => {
  it('refuses the loopback addresses the PC window uses', () => {
    for (const host of ['localhost', '127.0.0.1', '[::1]', '::1']) expect(reachableByTv(host)).toBe(false)
  })
  it('accepts the public address', () => {
    expect(reachableByTv('baihe.example.com')).toBe(true)
  })
})

describe('castUrl / castLabel', () => {
  it('puts the link on the address the page is open at', () => {
    expect(castUrl('https://baihe.example.com', '/api/cast/video?k=abc')).toBe('https://baihe.example.com/api/cast/video?k=abc')
  })
  it('says what to do when there is no copy yet and says nothing before the status is known', () => {
    expect(castLabel('missing')).toContain('Make it once')
    expect(castLabel(null)).toBe('')
  })
})

describe('share links', () => {
  it('offers the three lengths the server accepts', () => {
    expect(SHARE_CHOICES.map((c) => c.days)).toEqual([1, 3, 7])
  })
  it('says how long it lasts and that it opens one episode', () => {
    expect(shareNote(1)).toContain('1 day')
    expect(shareNote(7)).toContain('7 days')
    expect(shareNote(3)).toContain('nothing else')
  })
})
