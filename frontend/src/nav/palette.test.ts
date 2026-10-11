import { describe, expect, it } from 'vitest'

import type { AuthMe } from '../api/auth'
import type { SessionState } from '../hooks/useSession'
import { filterEntries, isPaletteShortcut, paletteEntries, type PaletteEntry } from './palette'
import type { NavContext } from './navItems'

const owner = { id: 1, email: 'o@example.com', display_name: 'Owner', is_admin: true, is_local_owner: true }
const sessionOf = (permissions: string[]): SessionState => ({
  status: 'ready',
  me: { auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', user: owner, permissions } as AuthMe,
})
const member: NavContext = { session: sessionOf(['library.read']), pcMode: 'remote', developerMode: false }
const admin: NavContext = { session: sessionOf(['library.read', 'admin.users.read', 'admin.diagnostics']), pcMode: 'local', developerMode: false }
const labels = (es: PaletteEntry[]) => es.map((e) => e.label)

const entry = (label: string, keywords = ''): PaletteEntry => ({ id: label, label, hint: 'Page', href: '#/x', keywords })

describe('paletteEntries', () => {
  it('lists the menu pages and always Library and Settings', () => {
    const l = labels(paletteEntries(member, { name: 'library' }))
    expect(l).toEqual(expect.arrayContaining(['Library', 'Settings', 'Discover', 'Sources']))
  })

  it('leaves out pages the person hid, but never Library or Settings', () => {
    const l = labels(paletteEntries({ ...member, hidden: ['discover', 'library', 'settings'] }, { name: 'library' }))
    expect(l).not.toContain('Discover')
    expect(l).toContain('Library')
    expect(l).toContain('Settings')
  })

  it('leaves out permission-hidden pages and shows them with the permission', () => {
    const without = labels(paletteEntries(member, { name: 'library' }))
    for (const hiddenPage of ['Admin', 'Diagnostics', 'Benchmark Lab', 'Assistant']) expect(without).not.toContain(hiddenPage)
    const withIt = labels(paletteEntries(admin, { name: 'library' }))
    for (const page of ['Admin', 'Diagnostics', 'Benchmark Lab']) expect(withIt).toContain(page)
  })

  it('adds the open title\'s stages first, only inside a title', () => {
    const inDrama = paletteEntries(member, { name: 'drama', id: 7, stage: null })
    expect(labels(inDrama).slice(0, 5)).toEqual(['Go to Media', 'Go to Translate', 'Go to Review', 'Go to Dub', 'Go to Export'])
    expect(inDrama[2].href).toBe('#/drama/7/review')
    expect(labels(paletteEntries(member, { name: 'jobs' })).some((l) => l.startsWith('Go to'))).toBe(false)
  })
})

describe('filterEntries', () => {
  const all = [entry('Library', 'titles'), entry('Quick translate', 'text'), entry('Saved manga'), entry('Settings', 'disk')]

  it('returns everything for an empty or blank query', () => {
    expect(filterEntries(all, '')).toBe(all)
    expect(filterEntries(all, '   ')).toBe(all)
  })

  it('is case-insensitive and matches anywhere in a word', () => {
    expect(labels(filterEntries(all, 'TRANS'))).toEqual(['Quick translate'])
    expect(labels(filterEntries(all, 'brar'))).toEqual(['Library'])
  })

  it('needs every word, in any order', () => {
    expect(labels(filterEntries(all, 'text translate'))).toEqual(['Quick translate'])
    expect(filterEntries(all, 'text library')).toEqual([])
  })

  it('ranks a label prefix before a word start before a substring before a keyword', () => {
    const es = [entry('Alpha', 'tab'), entry('Cat tab'), entry('Stable'), entry('Tab')]
    expect(labels(filterEntries(es, 'tab'))).toEqual(['Tab', 'Cat tab', 'Stable', 'Alpha'])
  })

  it('matches keywords that are not in the label', () => {
    expect(labels(filterEntries(all, 'disk'))).toEqual(['Settings'])
  })

  it('matches CJK as a substring and folds full-width Latin', () => {
    const es = [entry('設定'), entry('Jobs'), entry('ライブラリ')]
    expect(labels(filterEntries(es, '設'))).toEqual(['設定'])
    expect(labels(filterEntries(es, 'ブラ'))).toEqual(['ライブラリ'])
    expect(labels(filterEntries(es, 'ＪＯＢ'))).toEqual(['Jobs'])
  })

  it('returns nothing when nothing matches', () => {
    expect(filterEntries(all, 'zzz')).toEqual([])
  })
})

describe('isPaletteShortcut', () => {
  const key = (over: object) => ({ key: 'k', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...over })
  it('is Ctrl+K or Cmd+K only', () => {
    expect(isPaletteShortcut(key({ ctrlKey: true }))).toBe(true)
    expect(isPaletteShortcut(key({ metaKey: true, key: 'K' }))).toBe(true)
    expect(isPaletteShortcut(key({}))).toBe(false)
    expect(isPaletteShortcut(key({ ctrlKey: true, shiftKey: true }))).toBe(false)
    expect(isPaletteShortcut(key({ ctrlKey: true, altKey: true }))).toBe(false)
    expect(isPaletteShortcut(key({ ctrlKey: true, key: 's' }))).toBe(false)
  })
  it('ignores an IME composition', () => {
    expect(isPaletteShortcut(key({ ctrlKey: true, isComposing: true }))).toBe(false)
  })
})
