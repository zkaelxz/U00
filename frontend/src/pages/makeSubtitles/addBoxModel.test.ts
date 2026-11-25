import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { MediaLinkProbe } from '../../api/linkDetails'
import {
  HANDOFF_LIFE_MS, SETTLE_MS, clearHandoff, fileKind, foundForFile, foundForLink, handOff, linkCanRun, linkFrom, linkTaker, peekHandoff, planForFile,
  lengthText, planForLink, previewRows, type FileRunRequest,
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
  it('offers Make English subtitles for a link to an account with the rights, not otherwise or for a Sources page', () => {
    expect(linkCanRun(true, { state: 'reading' })).toBe(true)
    expect(linkCanRun(true, { state: 'failed' })).toBe(true)
    expect(linkCanRun(false, { state: 'off' })).toBe(false)
    expect(linkCanRun(true, { state: 'read', probe: probe({ kind: 'source' }) })).toBe(false)
    expect(linkCanRun(true, { state: 'read', probe: probe({ kind: 'page' }) })).toBe(true)
  })
})

describe('Add box: the plan line', () => {
  it('for a file names the chosen language, where it runs and the translator', () => {
    expect(planForFile({ pc: 'local', language: 'zh', engine: 'deepseek' })).toBe('Transcribe it as Chinese speech on this PC, then translate it with DeepSeek.')
    expect(planForFile({ pc: 'remote', language: 'ja', engine: '' })).toBe('Transcribe it as Japanese speech on the main PC, then translate it.')
  })
  it('for a link names the profile and its engine, or what is missing', () => {
    expect(planForLink({ pc: 'local', allowed: true, source: false, loaded: true, profile: 'Fast', engine: 'deepseek' }))
      .toBe('Download it on this PC, transcribe it, then translate it with DeepSeek (profile “Fast”), through the Queue.')
    expect(planForLink({ pc: 'local', allowed: true, source: false, loaded: true, profile: null, engine: null }))
      .toBe('Choose a One-click translate profile in Settings first.')
    expect(planForLink({ pc: 'local', allowed: true, source: false, loaded: false, profile: null, engine: null }))
      .toBe('Download it on this PC, transcribe it, then translate it, through the Queue.')
    expect(planForLink({ pc: 'remote', allowed: false, source: false, loaded: false, profile: null, engine: null })).toMatch(/needs the Import from a link right/)
    expect(planForLink({ pc: 'remote', allowed: true, source: false, loaded: true, profile: 'Fast', engine: null }))
      .toBe('Download it on the main PC, transcribe it, then translate it (profile “Fast”), through the Queue.')
    expect(planForLink({ pc: 'local', allowed: true, source: true, loaded: true, profile: 'Fast', engine: null })).toMatch(/Sources page/)
    expect(planForLink({ pc: 'local', allowed: true, source: false, loaded: true, profile: 'Default', engine: 'claude', capNeeded: true }))
      .toBe('Set a monthly spending cap in Settings > Spending first: this profile translates with a paid engine.')
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

describe('previewRows', () => {
  const summary = [
    { label: 'Source language', value: 'Not set' }, { label: 'Speech recognizer', value: 'Whisper large-v3' },
    { label: 'Engine and model', value: 'gemma' }, { label: 'Cost', value: 'Free' }, { label: 'Batch size', value: '30' },
  ]
  const read = (extra: object) => ({ state: 'read', probe: { kind: 'video', title: '', channel: '', release_date: '', is_live: false, summary: '', platform: '', series_suggestion: '', note: '', display_url: '', ...extra } }) as never
  it('leads with the length, then language, recognizer, engine and cost only', () => {
    const rows = previewRows(read({ duration_seconds: 3900 }), summary)
    expect(rows.map((r) => [r.label, r.value])).toEqual([
      ['Length', '1 h 5 min'], ['Source language', 'Worked out from the speech'],
      ['Speech recognizer', 'Whisper large-v3'], ['Engine and model', 'gemma'], ['Cost', 'Free'],
    ])
  })
  it('names a live stream and a link without a length', () => {
    expect(previewRows(read({ is_live: true }), [])[0].value).toBe('Live stream')
    expect(previewRows(read({ duration_seconds: null }), [])[0].value).toBe('Not given by the link')
    expect(previewRows({ state: 'reading' } as never, summary)[0].label).toBe('Source language')
  })
  it('writes short lengths plainly', () => {
    expect(lengthText(20)).toBe('under a minute')
    expect(lengthText(42 * 60)).toBe('42 min')
    expect(lengthText(2 * 3600)).toBe('2 h')
  })
})
