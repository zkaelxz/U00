import { describe, expect, it } from 'vitest'

import type { SourceDomainEntry } from '../../types/sourceDomains'
import {
  MAX_DOMAINS,
  addDomain,
  domainsSummary,
  moveDomain,
  normalizeHost,
  pendingTotal,
  removeDomain,
  sameList,
} from './sourceDomainsModel'

const entry = (over: Partial<SourceDomainEntry> = {}): SourceDomainEntry => ({
  source: 'a', display_name: 'A', domains: ['a.example'], default_domains: ['a.example'],
  customized: false, last_good: null, pending_proposals: 0, ...over,
})

describe('normalizeHost', () => {
  it('lowercases, strips a trailing dot and a default 443 port', () => {
    expect(normalizeHost('  Novels.Example. ')).toEqual({ host: 'novels.example' })
    expect(normalizeHost('x.example:443')).toEqual({ host: 'x.example' })
    expect(normalizeHost('x.example.:8080')).toEqual({ host: 'x.example:8080' })
    expect(normalizeHost('xn--fsq.example')).toEqual({ host: 'xn--fsq.example' })
  })
  it.each([
    ['', 'Type a host'],
    ['a b.example', 'spaces'],
    ['https://a.example', 'without http'],
    ['a.example/path', 'without http'],
    ['user@a.example', 'without http'],
    ['127.0.0.1', 'IP'],
    ['[::1]', 'IP'],
    ['::1', 'IP'],
    ['a.example:0', 'port'],
    ['a.example:70000', 'port'],
    ['a.example:x', 'port'],
    ['-a.example', 'letters'],
    ['例え.example', 'letters'],
    ['a..example', 'letters'],
  ])('rejects %j', (raw, part) => {
    expect(normalizeHost(raw).error).toContain(part)
  })
})

describe('list edits', () => {
  it('adds, de-dupes and caps at ten', () => {
    expect(addDomain(['a.example'], 'B.example').list).toEqual(['a.example', 'b.example'])
    expect(addDomain(['a.example'], 'A.example.').error).toMatch(/already/)
    const full = Array.from({ length: MAX_DOMAINS }, (_, i) => `h${i}.example`)
    expect(addDomain(full, 'more.example')).toEqual({ list: full, error: 'At most 10 hosts.' })
  })
  it('moves and removes', () => {
    expect(moveDomain(['a', 'b', 'c'], 2, -1)).toEqual(['a', 'c', 'b'])
    expect(moveDomain(['a', 'b'], 0, -1)).toEqual(['a', 'b'])
    expect(removeDomain(['a', 'b'], 0)).toEqual(['b'])
    expect(sameList(['a', 'b'], ['b', 'a'])).toBe(false)
  })
})

describe('summary', () => {
  it('counts pending and customized', () => {
    const list = [entry({ customized: true, pending_proposals: 2 }), entry({ source: 'b' })]
    expect(pendingTotal(list)).toBe(2)
    expect(domainsSummary(list)).toBe('2 sources · 1 customized · 2 possible new')
    expect(domainsSummary([entry()])).toBe('1 source · all default')
  })
})
