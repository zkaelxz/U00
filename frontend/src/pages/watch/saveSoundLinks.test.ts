import { describe, expect, it } from 'vitest'

import { saveSoundLinks } from './saveSoundLinks'

describe('saveSoundLinks', () => {
  it('offers the sound and the English subtitles to someone who may have both', () => {
    expect(saveSoundLinks(7, true, true).map((l) => [l.label, l.href])).toEqual([
      ['Save the sound', '/api/media/dramas/7/audio'],
      ['Save English subtitles', '/api/export/dramas/7/subtitle?fmt=srt&field=en'],
    ])
  })
  it('leaves out what the account may not read, and everything when it may read neither', () => {
    expect(saveSoundLinks(7, false, true)).toHaveLength(1)
    expect(saveSoundLinks(7, true, false)).toHaveLength(1)
    expect(saveSoundLinks(7, false, false)).toEqual([])
  })
})
