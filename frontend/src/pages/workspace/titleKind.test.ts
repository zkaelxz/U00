import { describe, expect, it } from 'vitest'

import { foldsTranslateOptions, titleKind } from './titleKind'

describe('foldsTranslateOptions', () => {
  it('folds the run options for novels and comics only', () => {
    expect((['novel', 'comic'] as const).map(foldsTranslateOptions)).toEqual([true, true])
    expect((['video', 'audio', 'subtitle'] as const).map(foldsTranslateOptions)).toEqual([false, false, false])
  })
})

describe('titleKind', () => {
  it.each([
    ['video_drama', 'video'],
    ['anime', 'video'],
    ['streamer_vod', 'video'],
    ['audio_drama', 'audio'],
    ['asmr', 'audio'],
    ['novel', 'novel'],
    ['manga', 'comic'],
    ['manhua', 'comic'],
    ['manhwa', 'comic'],
    ['music', 'subtitle'],
    ['other', 'subtitle'],
    [null, 'subtitle'],
    [undefined, 'subtitle'],
  ])('%s is a %s title', (mediaType, kind) => {
    expect(titleKind(mediaType, null)).toBe(kind)
  })

  it('treats novel narration as a novel whatever the media type is', () => {
    expect(titleKind('audio_drama', 'novel_narration')).toBe('novel')
    expect(titleKind('video_drama', 'novel_narration')).toBe('novel')
  })
})
