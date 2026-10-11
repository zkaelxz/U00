import { describe, expect, it } from 'vitest'

import {
  EXPORT_BLOCK_TITLES,
  NOT_NOVEL_NARRATION,
  NO_DUB,
  NO_FFMPEG,
  NO_NARRATION,
  NO_SOURCE_VIDEO,
  exportBlockOrder,
  exportBlockReason,
  exportBlockStorageKey,
  firstExportBlock,
  type ExportBlockId,
  type ExportInputs,
} from './exportBlocks'
import type { TitleKind } from './titleKind'

const KINDS: TitleKind[] = ['video', 'audio', 'novel', 'comic', 'subtitle']
const ALL = Object.keys(EXPORT_BLOCK_TITLES).sort()

const ready: ExportInputs = { hasSourceVideo: true, hasDub: true, hasNarration: true, ffmpegFound: true, novelNarration: true }
const unknown: ExportInputs = { hasSourceVideo: null, hasDub: null, hasNarration: null, ffmpegFound: null, novelNarration: true }

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
    expect(firstExportBlock(kind)).toBe(first)
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
    for (const id of ['softsub_video', 'burned_video', 'dubbed_video'] as const) expect(exportBlockReason(id, i)).toBe(NO_SOURCE_VIDEO)
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
    expect(exportBlockReason('dubbed_video', i)).toBe(NO_SOURCE_VIDEO)
  })

  it('only offers EPUB for novel-narration titles', () => {
    expect(exportBlockReason('epub', { ...ready, novelNarration: false })).toBe(NOT_NOVEL_NARRATION)
  })

  it('keeps every reason to one short line', () => {
    for (const r of [NO_SOURCE_VIDEO, NO_FFMPEG, NO_NARRATION, NO_DUB, NOT_NOVEL_NARRATION]) {
      expect(r.length).toBeLessThan(70)
      expect(r).not.toContain('\n')
    }
  })
})
