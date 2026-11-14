import { describe, expect, it } from 'vitest'

import type { MediaLinkProbe } from '../../api/linkDetails'
import {
  clearHandoff, fileKind, foundForFile, foundForLink, handOff, linkCanRun, linkFrom, peekHandoff, planForFile, planForLink,
  type FileRunRequest,
} from './addBox'

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
})
