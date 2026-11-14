import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MediaLinkProbe } from '../../api/linkDetails'
import {
  HANDOFF_LIFE_MS, SETTLE_MS, clearHandoff, fileKind, foundForFile, foundForLink, handOff, linkCanRun, linkFrom, linkTaker, peekHandoff, planForFile,
  planForLink, type FileRunRequest,
} from './addBoxModel'

const probe = (o: Partial<MediaLinkProbe>): MediaLinkProbe => ({
  kind: 'video', title: '', channel: '', release_date: '', is_live: false, summary: '', platform: '',
  series_suggestion: '', note: '', display_url: '', ...o,
})
const LINK = 'https://www.youtube.com/watch?v=abc'

describe('Add box: what counts as a link', () => {
  it('takes an http(s) address and leaves other text alone', () => {
    expect(linkFrom(`  ${LINK} `)).toBe(LINK)
    expect(linkFrom('youtube.com/watch')).toBeNull()
    expect(linkFrom('第一回 字幕')).toBeNull()
    expect(linkFrom('https://')).toBeNull()
  })
})

describe('Add box: Found for a file', () => {
  it('names the kind from the extension, the name and the size, and nothing it cannot know', () => {
    expect(foundForFile({ name: 'Ep 1.MP4', size: 3 * 1024 * 1024 })).toBe('a video file, “Ep 1.MP4”, 3.0 MB.')
    expect(foundForFile({ name: 'drama.flac', size: 2048 })).toBe('an audio file, “drama.flac”, 2.0 KB.')
    expect(fileKind('notes.txt')).toBe('a file')
  })
})

describe('Add box: Found for a link', () => {
  it('gives only the host while the probe cannot run or is reading, and says when it failed', () => {
    expect(foundForLink(LINK, { state: 'off' })).toBe('a link to youtube.com.')
    expect(foundForLink(LINK, { state: 'reading' })).toBe('a link to youtube.com. Reading it…')
    expect(foundForLink(LINK, { state: 'failed' })).toBe('a link to youtube.com. Baihe could not read it.')
  })
  it('says what the free link probe read, leaving out what it did not give', () => {
    expect(foundForLink(LINK, { state: 'read', probe: probe({ title: '第一回', platform: 'YouTube', channel: 'Studio', release_date: '2026-10-02' }) }))
      .toBe('a video on YouTube: “第一回”, from Studio, 2026-10-02.')
    expect(foundForLink(LINK, { state: 'read', probe: probe({ is_live: true }) })).toBe('a live video on youtube.com.')
    expect(foundForLink('https://novels.example/book/5', { state: 'read', probe: probe({ kind: 'source', title: 'Book' }) }))
      .toBe('a Sources page on novels.example: “Book”.')
    expect(foundForLink('https://blog.example/p', { state: 'read', probe: probe({ kind: 'page', note: 'Nothing was read.' }) }))
      .toBe('a page on blog.example. Nothing was read.')
  })
})

describe('Add box: which actions show', () => {
  it('offers Make English subtitles for a link at the PC, not away from it or for a Sources page', () => {
    expect(linkCanRun('local', { state: 'reading' })).toBe(true)
    expect(linkCanRun('local', { state: 'failed' })).toBe(true)
    expect(linkCanRun('remote', { state: 'off' })).toBe(false)
    expect(linkCanRun('local', { state: 'read', probe: probe({ kind: 'source' }) })).toBe(false)
    expect(linkCanRun('local', { state: 'read', probe: probe({ kind: 'page' }) })).toBe(true)
  })
})

describe('Add box: the plan line', () => {
  it('for a file names the chosen language, where it runs and the translator', () => {
    expect(planForFile({ pc: 'local', language: 'zh', engine: 'deepseek' })).toBe('Transcribe it as Chinese speech on this PC, then translate it with DeepSeek.')
    expect(planForFile({ pc: 'remote', language: 'ja', engine: '' })).toBe('Transcribe it as Japanese speech on the main PC, then translate it.')
  })
  it('for a link names the profile and its engine, or what is missing', () => {
    expect(planForLink({ pc: 'local', source: false, loaded: true, profile: 'Fast', engine: 'deepseek' }))
      .toBe('Download it on this PC, transcribe it, then translate it with DeepSeek (profile “Fast”), through the Queue.')
    expect(planForLink({ pc: 'local', source: false, loaded: true, profile: null, engine: null }))
      .toBe('Choose a One-Click Translate profile in Settings > Quick video translate first.')
    expect(planForLink({ pc: 'local', source: false, loaded: false, profile: null, engine: null }))
      .toBe('Download it on this PC, transcribe it, then translate it, through the Queue.')
    expect(planForLink({ pc: 'remote', source: false, loaded: false, profile: null, engine: null })).toMatch(/only on the main PC/)
    expect(planForLink({ pc: 'local', source: true, loaded: true, profile: 'Fast', engine: null })).toMatch(/Sources page/)
  })
})

describe('Add box hand-off', () => {
  it('keeps a file run for the Add page until it is cleared, and only for the kind asked', () => {
    const request: FileRunRequest = { file: new File(['x'], 'a.mp3'), engine: 'deepseek', language: 'ja', title: '', variant: 'en-GB' }
    handOff({ kind: 'run-file', request })
    expect(peekHandoff('new-title')).toBeNull()
    const h = peekHandoff('run-file')
    expect(h?.request).toBe(request)
    // A second read (React's doubled first render) sees the same hand-off.
    expect(peekHandoff('run-file')).toBe(h)
    clearHandoff(h)
    expect(peekHandoff('run-file')).toBeNull()
  })
  it('does not clear a newer hand-off', () => {
    handOff({ kind: 'new-title', start: { link: LINK } })
    const old = peekHandoff('new-title')
    handOff({ kind: 'new-title', start: null })
    clearHandoff(old)
    expect(peekHandoff('new-title')?.start).toBeNull()
    clearHandoff(peekHandoff('new-title'))
  })

  describe('lifetime', () => {
    beforeEach(() => { vi.useFakeTimers() })
    afterEach(() => { vi.useRealTimers() })

    it('is still there for a page that opens within its life', () => {
      handOff({ kind: 'new-title', start: { link: LINK } })
      vi.advanceTimersByTime(HANDOFF_LIFE_MS)
      const h = peekHandoff('new-title')
      expect(h?.start).toEqual({ link: LINK })
      clearHandoff(h)
    })
    it('is gone once older than its life, and stays gone', () => {
      const request: FileRunRequest = { file: new File(['x'], 'a.mp3'), engine: 'deepseek', language: 'ja', title: '', variant: '' }
      handOff({ kind: 'run-file', request })
      vi.advanceTimersByTime(HANDOFF_LIFE_MS + 1)
      expect(peekHandoff('run-file')).toBeNull()
      vi.setSystemTime(Date.now() - 2 * HANDOFF_LIFE_MS)
      expect(peekHandoff('run-file')).toBeNull()
    })
  })
})

describe('Add box: when a link counts as given (each take probes it on the PC)', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  const taker = () => {
    const taken: string[] = []
    return { taken, t: linkTaker((link) => { if (link) taken.push(link) }) }
  }
  const typeIn = (t: ReturnType<typeof linkTaker>, text: string) => {
    for (let i = 1; i <= text.length; i += 1) {
      t.typed(text.slice(0, i))
      vi.advanceTimersByTime(80)
    }
  }

  it('takes typed text one character at a time only after the pause, and then exactly once', () => {
    const { taken, t } = taker()
    typeIn(t, LINK)
    expect(taken).toEqual([])
    vi.advanceTimersByTime(SETTLE_MS - 81)
    expect(taken).toEqual([])
    vi.advanceTimersByTime(1)
    expect(taken).toEqual([LINK])
    vi.advanceTimersByTime(10 * SETTLE_MS)
    expect(taken).toEqual([LINK])
  })

  it('takes a paste or Enter at once, exactly once', () => {
    const { taken, t } = taker()
    t.now(LINK)
    expect(taken).toEqual([LINK])
    vi.advanceTimersByTime(10 * SETTLE_MS)
    expect(taken).toEqual([LINK])
  })

  it('a change, Clear or Enter cancels the pending take', () => {
    const { taken, t } = taker()
    t.typed(LINK)
    t.clear()
    vi.advanceTimersByTime(10 * SETTLE_MS)
    expect(taken).toEqual([])
    t.typed('https://a.example/1')
    vi.advanceTimersByTime(SETTLE_MS - 1)
    t.typed('https://a.example/12')
    vi.advanceTimersByTime(SETTLE_MS)
    expect(taken).toEqual(['https://a.example/12'])
    t.typed('https://b.example/x')
    t.now('https://b.example/x')
    vi.advanceTimersByTime(10 * SETTLE_MS)
    expect(taken).toEqual(['https://a.example/12', 'https://b.example/x'])
  })

  it('says nothing is given while typing and after Clear', () => {
    const seen: (string | null)[] = []
    const t = linkTaker((link) => seen.push(link))
    t.now(LINK)
    t.typed(`${LINK}x`)
    t.clear()
    expect(seen).toEqual([LINK, null, null])
  })
})
