import { describe, expect, it } from 'vitest'

import {
  EXPORT_BLOCK_TITLES,
  NOT_NOVEL_NARRATION,
  NO_DUB,
  NO_FFMPEG,
  NO_NARRATION,
  exportBlockOrder,
  exportBlockReason,
  exportBlockStorageKey,
  jellyfinMediaReason,
  noSourceVideo,
  openingExportBlock,
  shownExportBlocks,
  type ExportBlockId,
  type ExportInputs,
} from './exportBlocks'
import type { TitleKind } from './titleKind'

const KINDS: TitleKind[] = ['video', 'audio', 'novel', 'comic', 'subtitle']

const ready: ExportInputs = { kind: 'video', hasSourceVideo: true, hasDub: true, hasNarration: true, ffmpegFound: true, novelNarration: true }
const unknown: ExportInputs = { kind: 'video', hasSourceVideo: null, hasDub: null, hasNarration: null, ffmpegFound: null, novelNarration: true }

describe('exportBlockOrder', () => {
  it.each([
    ['video', ['softsub_video', 'burned_video', 'dubbed_video', 'subtitles']],
    ['audio', ['audiobook', 'subtitles']],
    ['novel', ['epub', 'audiobook', 'subtitles']],
    ['comic', []],
    ['subtitle', ['subtitles']],
  ] as const)('a %s title lists only the exports that fit it', (kind, expected) => {
    expect(exportBlockOrder(kind)).toEqual(expected)
  })

  it('adds an export that does not fit the kind once the title has what it works from', () => {
    expect(exportBlockOrder('audio', { hasSourceVideo: true, hasDub: false, hasNarration: true })).toEqual([
      'audiobook', 'dubbed_video', 'softsub_video', 'burned_video', 'subtitles',
    ])
    expect(exportBlockOrder('comic', { hasSourceVideo: true, hasDub: true, hasNarration: true })).toEqual([])
    expect(exportBlockOrder('subtitle', { hasSourceVideo: false, hasDub: true, hasNarration: true })).toEqual(['subtitles', 'audiobook', 'dubbed_video'])
    expect(exportBlockOrder('video', { hasNarration: true })).toEqual(['softsub_video', 'burned_video', 'dubbed_video', 'audiobook', 'subtitles'])
  })

  it('shows EPUB only on a novel in novel-narration mode', () => {
    expect(exportBlockOrder('novel', { novelNarration: false })).not.toContain('epub')
    for (const kind of ['video', 'audio', 'comic', 'subtitle'] as const) {
      expect(exportBlockOrder(kind, { hasSourceVideo: true, hasDub: true, hasNarration: true, novelNarration: true })).not.toContain('epub')
    }
  })
})

describe('exportBlockStorageKey', () => {
  it('is separate per title kind and per block', () => {
    expect(exportBlockStorageKey('video', 'epub')).not.toBe(exportBlockStorageKey('novel', 'epub'))
    expect(exportBlockStorageKey('video', 'epub')).not.toBe(exportBlockStorageKey('video', 'audiobook'))
  })
})

describe('exportBlockReason', () => {
  it('has no reason when everything is there or nothing is known', () => {
    for (const id of Object.keys(EXPORT_BLOCK_TITLES) as ExportBlockId[]) {
      expect(exportBlockReason(id, ready)).toBeNull()
      expect(exportBlockReason(id, unknown)).toBeNull()
    }
  })

  it('names the missing source video for the three video exports', () => {
    const i = { ...ready, hasSourceVideo: false }
    for (const id of ['softsub_video', 'burned_video', 'dubbed_video'] as const) expect(exportBlockReason(id, i)).toBe(noSourceVideo('video'))
    expect(exportBlockReason('audiobook', i)).toBeNull()
  })

  it('names a missing dub or narration', () => {
    expect(exportBlockReason('dubbed_video', { ...ready, hasDub: false })).toBe(NO_DUB)
    expect(exportBlockReason('softsub_video', { ...ready, hasDub: false })).toBeNull()
    expect(exportBlockReason('audiobook', { ...ready, hasNarration: false })).toBe(NO_NARRATION)
  })

  it('names missing ffmpeg for every media job and not for text exports', () => {
    const i = { ...ready, ffmpegFound: false }
    for (const id of ['softsub_video', 'burned_video', 'dubbed_video', 'audiobook'] as const) expect(exportBlockReason(id, i)).toBe(NO_FFMPEG)
    expect(exportBlockReason('subtitles', i)).toBeNull()
    expect(exportBlockReason('epub', i)).toBeNull()
  })

  it('gives one reason, the first thing the viewer must fix', () => {
    const i = { ...ready, hasSourceVideo: false, hasDub: false, ffmpegFound: false }
    expect(exportBlockReason('dubbed_video', i)).toBe(noSourceVideo('video'))
  })

  it('only offers EPUB for novel-narration titles', () => {
    expect(exportBlockReason('epub', { ...ready, novelNarration: false })).toBe(NOT_NOVEL_NARRATION)
  })

  it('keeps every reason to one short line', () => {
    for (const r of [...KINDS.map(noSourceVideo), NO_FFMPEG, NO_NARRATION, NO_DUB, NOT_NOVEL_NARRATION]) {
      expect(r.length).toBeLessThan(70)
      expect(r).not.toContain('\n')
    }
  })
})

describe('noSourceVideo', () => {
  it('names the first stage as the title kind calls it', () => {
    expect(noSourceVideo('video')).toBe('No source video yet. Add one in Transcribe.')
    expect(noSourceVideo('audio')).toBe('No source video yet. Add one in Transcribe.')
    expect(noSourceVideo('novel')).toBe('No source video yet. Add one in Media.')
    expect(exportBlockReason('softsub_video', { ...ready, kind: 'comic', hasSourceVideo: false })).toBe('No source video yet. Add one in Media.')
  })
})

describe('openingExportBlock', () => {
  const opens = (kind: TitleKind, simple: boolean, i: ExportInputs) =>
    openingExportBlock(shownExportBlocks(kind, simple, i), (id) => exportBlockReason(id, { ...i, kind }))
  const noVideo = { ...ready, hasSourceVideo: false }

  it('shows the fitting blocks in Full and the two most titles need in Simple, in the kind order', () => {
    expect(shownExportBlocks('video', false)).toEqual(exportBlockOrder('video'))
    expect(shownExportBlocks('audio', true)).toEqual(['subtitles'])
    expect(shownExportBlocks('audio', true, { hasSourceVideo: true })).toEqual(['softsub_video', 'subtitles'])
    expect(shownExportBlocks('subtitle', true)).toEqual(['subtitles'])
  })

  it('opens the subtitle file in Simple on an audio title with no source video', () => {
    expect(opens('audio', true, noVideo)).toBe('subtitles')
  })

  it('opens the subtitle-track video in Simple on an audio title with a video, and the subtitle file while nothing is known', () => {
    expect(opens('audio', true, ready)).toBe('softsub_video')
    expect(opens('audio', true, unknown)).toBe('subtitles')
  })

  it.each(KINDS)('opens the first block in Full on a %s title when everything can run', (kind) => {
    expect(opens(kind, false, ready)).toBe(exportBlockOrder(kind, ready)[0])
  })

  it('skips blocks that cannot run in Full, keeping the order', () => {
    expect(opens('audio', false, { ...ready, hasNarration: false, hasDub: false })).toBe('softsub_video')
    expect(opens('audio', false, { ...noVideo, hasNarration: false })).toBe('subtitles')
    expect(opens('video', false, noVideo)).toBe('audiobook')
    expect(opens('video', false, { ...noVideo, hasNarration: false })).toBe('subtitles')
  })

  it('falls back to the first shown block when none can run', () => {
    expect(openingExportBlock(['softsub_video', 'burned_video'], () => 'blocked')).toBe('softsub_video')
  })
})

describe('jellyfinMediaReason', () => {
  it('needs nothing for subtitles only, or when everything is there or unknown', () => {
    expect(jellyfinMediaReason('none', { ...ready, hasSourceVideo: false })).toBeNull()
    expect(jellyfinMediaReason('source', ready)).toBeNull()
    expect(jellyfinMediaReason('dubbed', unknown)).toBeNull()
  })
  it('names the missing video or dub', () => {
    expect(jellyfinMediaReason('source', { ...ready, hasSourceVideo: false })).toBe(noSourceVideo('video'))
    expect(jellyfinMediaReason('dubbed', { ...ready, hasDub: false })).toBe(NO_DUB)
  })
})
