import { describe, expect, it } from 'vitest'

import { appVersionText, phoneAppText, shortDate } from './appVersionText'

describe('the version line', () => {
  it('shows an installed copy with its release date', () => {
    expect(appVersionText({ version: '0.1.6', released: '2026-11-22', source_copy: false, commit_date: null }))
      .toBe('Baihe 0.1.6 · released 22 Nov 2026')
    expect(appVersionText({ version: '0.1.6', released: null, source_copy: false, commit_date: null })).toBe('Baihe 0.1.6')
    expect(appVersionText({ version: null, released: null, source_copy: false, commit_date: null })).toBeNull()
  })

  it('shows a source copy with its commit date', () => {
    expect(appVersionText({ version: '0.1.6-dev', released: null, source_copy: true, commit_date: '2026-09-03' }))
      .toBe('Baihe 0.1.6-dev · source copy, 3 Sep 2026')
    expect(appVersionText({ version: '0.1.6-dev', released: null, source_copy: true, commit_date: null }))
      .toBe('Baihe 0.1.6-dev · source copy')
  })

  it('reads the phone app build from its user agent', () => {
    expect(phoneAppText('Mozilla/5.0 (Linux; Android 14) Chrome/130 Mobile Safari/537.36 BaiheAndroid/1042 (2026-11-20)'))
      .toBe('Phone app build 1042 · built 20 Nov 2026')
    expect(phoneAppText('Mozilla/5.0 BaiheAndroid/1042')).toBe('Phone app build 1042')
    expect(phoneAppText('Mozilla/5.0 (Windows NT 10.0) Chrome/130')).toBeNull()
  })

  it('refuses a date it cannot read', () => {
    expect(shortDate('2026-13-01')).toBeNull()
    expect(shortDate('22 Nov')).toBeNull()
  })
})
