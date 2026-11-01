import { describe, expect, it } from 'vitest'

import { isSharedLink, linkFromShare, shareRedirect } from './shareTarget'

const q = (s: string) => new URLSearchParams(s)

describe('linkFromShare', () => {
  it('takes the url field', () => {
    expect(linkFromShare(q('url=https%3A%2F%2Fsite.example%2Fc%2F1'))).toBe('https://site.example/c/1')
  })
  it('finds the link inside text, as Android Chrome sends it', () => {
    expect(linkFromShare(q('title=Ch1&text=' + encodeURIComponent('Chapter 1 https://site.example/c/1?x=2')))).toBe('https://site.example/c/1?x=2')
  })
  it('prefers url over text', () => {
    expect(linkFromShare(q('url=https://a.example/&text=https://b.example/'))).toBe('https://a.example/')
  })
  it('refuses other schemes, embedded sign-ins and long links', () => {
    expect(linkFromShare(q('url=javascript:alert(1)'))).toBeNull()
    expect(linkFromShare(q('url=file:///etc/passwd'))).toBeNull()
    expect(isSharedLink('https://user:pw@site.example/')).toBe(false)
    expect(isSharedLink('https://site.example/' + 'a'.repeat(2000))).toBe(false)
    expect(linkFromShare(q('text=no link here'))).toBeNull()
  })
})

describe('shareRedirect', () => {
  it('sends a shared link to Sources', () => {
    expect(shareRedirect('/share', '?url=https%3A%2F%2Fsite.example%2Fc%2F1')).toBe('#/sources?url=https%3A%2F%2Fsite.example%2Fc%2F1')
  })
  it('still opens Sources when nothing usable was shared', () => {
    expect(shareRedirect('/share', '?text=hi')).toBe('#/sources')
  })
  it('leaves every other address alone', () => {
    expect(shareRedirect('/', '')).toBeNull()
    expect(shareRedirect('/share/x', '?url=https://a.example/')).toBeNull()
  })
})
