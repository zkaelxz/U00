import { describe, expect, it } from 'vitest'

import { ADVANCED_FIELD_IDS, advancedGroups } from './stages/advancedGroups'
import { appliesTo, SETTING_GROUPS_BY_KIND, type SettingGroup } from './mediaTypeSettings'
import { mediaTabsFor } from './mediaTabs'
import type { TitleKind } from './titleKind'

const KINDS: TitleKind[] = ['video', 'audio', 'novel', 'comic', 'subtitle']
const shown = (kind: TitleKind) => advancedGroups(true, false, kind).flatMap((g) => g.fields)

describe('SETTING_GROUPS_BY_KIND', () => {
  it('lists every kind', () => {
    expect(Object.keys(SETTING_GROUPS_BY_KIND).sort()).toEqual([...KINDS].sort())
  })

  it('gives audio and video the speech settings, and only video (and plain subtitle titles) the hardsub ones', () => {
    for (const g of ['transcribe', 'speech_tuning', 'audio_prep', 'speakers', 'subtitle_import'] as SettingGroup[]) {
      expect(appliesTo('audio', g) && appliesTo('video', g)).toBe(true)
    }
    expect(appliesTo('video', 'hardsub')).toBe(true)
    expect(appliesTo('audio', 'hardsub')).toBe(false)
  })

  it.each<TitleKind>(['novel', 'comic'])('%s has none of the transcription or audio settings', (kind) => {
    for (const g of ['transcript_source', 'transcribe', 'speech_tuning', 'audio_prep', 'speakers', 'hardsub', 'subtitle_import'] as SettingGroup[]) {
      expect(appliesTo(kind, g)).toBe(false)
    }
  })

  it('keeps the audio upload for a novel (dubbing comes after translating) but not for a comic', () => {
    expect(appliesTo('novel', 'media_upload')).toBe(true)
    expect(appliesTo('comic', 'media_upload')).toBe(false)
    expect(appliesTo('comic', 'chapter_ocr')).toBe(true)
    expect(appliesTo('novel', 'chapter_ocr')).toBe(false)
  })
})

describe('what the table drives', () => {
  it('shows every advanced control for video and plain titles, and no vocal separation or hardsub for novel or comic', () => {
    expect(shown('video').sort()).toEqual([...ADVANCED_FIELD_IDS].sort())
    expect(shown('subtitle').sort()).toEqual([...ADVANCED_FIELD_IDS].sort())
    for (const kind of ['novel', 'comic'] as TitleKind[]) expect(shown(kind)).toEqual([])
  })

  it('drops only the hardsub controls for an audio drama', () => {
    expect(shown('audio').sort()).toEqual(ADVANCED_FIELD_IDS.filter((f) => f !== 'hardsub_interval_sec' && f !== 'hardsub_ocr_backend').sort())
    expect(shown('audio')).toEqual(expect.arrayContaining(['separate_vocals_first', 'min_silence_ms', 'vad_threshold', 'beam_size']))
  })

  it('has the Transcribe tab (its Tools part included) exactly for the kinds with those settings', () => {
    for (const kind of KINDS) {
      const ids = mediaTabsFor(kind).map((t) => t.id)
      expect(ids.includes('transcribe')).toBe(appliesTo(kind, 'transcribe'))
      expect(ids.includes('transcribe')).toBe(appliesTo(kind, 'speech_tuning'))
    }
  })
})
