import { describe, expect, it } from 'vitest'

import { runLabel } from './QuickReadRuns'

const run = { run_id: 'run-aaaaaaaa', site: 'site.example', chapter: 'series/ch-4', pages_received: 5, pages_left: 3, running_seconds: 9 }

describe('runLabel', () => {
  it('names the site and the chapter', () => {
    expect(runLabel(run)).toBe('site.example / series/ch-4')
  })
  it('falls back when the run came without a link', () => {
    expect(runLabel({ ...run, site: '', chapter: '' })).toBe('A Quick read')
  })
})
