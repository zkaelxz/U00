import { describe, expect, it } from 'vitest'

import { castLabel, castUrl, reachableByTv } from './castLogic'

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
