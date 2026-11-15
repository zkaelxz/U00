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
const ALL = Object.keys(EXPORT_BLOCK_TITLES).sort()

const ready: ExportInputs = { kind: 'video', hasSourceVideo: true, hasDub: true, hasNarration: true, ffmpegFound: true, novelNarration: true }
const unknown: ExportInputs = { kind: 'video', hasSourceVideo: null, hasDub: null, hasNarration: null, ffmpegFound: null, novelNarration: true }

describe('exportBlockOrder', () => {
  it.each(KINDS)('lists every export exactly once for a %s title', (kind) => {
    expect([...exportBlockOrder(kind)].sort()).toEqual(ALL)
  })

  it.each([
    ['video', 'softsub_video'],
    ['audio', 'audiobook'],
    ['novel', 'epub'],
    ['comic', 'subtitles'],
    ['subtitle', 'subtitles'],
  ] as const)('a %s title leads with %s', (kind, first) => {
    expect(exportBlockOrder(kind)[0]).toBe(first)
  })

  it('puts the video exports, then the subtitle file, for a video title', () => {
    expect(exportBlockOrder('video').slice(0, 5)).toEqual([
      'softsub_video', 'burned_video', 'dubbed_video', 'audiobook', 'subtitles',
    ])
  })

  it('keeps the subtitle-only layout: the subtitle file, then the media jobs, then EPUB', () => {
    expect(exportBlockOrder('subtitle')).toEqual(['subtitles', 'audiobook', 'burned_video', 'softsub_video', 'dubbed_video', 'epub'])
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
    openingExportBlock(shownExportBlocks(kind, simple), (id) => exportBlockReason(id, { ...i, kind }))
  const noVideo = { ...ready, hasSourceVideo: false }

  it('shows every block in Full and the two most titles need in Simple, in the kind order', () => {
    expect(shownExportBlocks('audio', false)).toEqual(exportBlockOrder('audio'))
    expect(shownExportBlocks('audio', true)).toEqual(['softsub_video', 'subtitles'])
    expect(shownExportBlocks('subtitle', true)).toEqual(['subtitles', 'softsub_video'])
  })

  it('opens the subtitle file in Simple on an audio title with no source video', () => {
    expect(opens('audio', true, noVideo)).toBe('subtitles')
  })

  it('opens the same block as before when the audio title has a video, or nothing is known yet', () => {
    expect(opens('audio', true, ready)).toBe('softsub_video')
    expect(opens('audio', true, unknown)).toBe('softsub_video')
  })

  it.each(KINDS)('opens the first block in Full on a %s title when everything can run', (kind) => {
    expect(opens(kind, false, ready)).toBe(exportBlockOrder(kind)[0])
  })

  it('skips blocks that cannot run in Full, keeping the order', () => {
    // No narration and no dub: Audiobook and the dub video are skipped for the subtitle-track video.
    expect(opens('audio', false, { ...ready, hasNarration: false, hasDub: false })).toBe('softsub_video')
    // No video either: the next one that can run is the subtitle file.
    expect(opens('audio', false, { ...noVideo, hasNarration: false })).toBe('subtitles')
    expect(opens('video', false, noVideo)).toBe('audiobook')
    expect(opens('novel', false, { ...ready, novelNarration: false })).toBe('audiobook')
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
