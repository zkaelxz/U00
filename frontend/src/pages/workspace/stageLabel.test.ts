import { describe, expect, it } from 'vitest'

import { STAGE_LABELS, stageLabel } from './stages'

describe('stageLabel', () => {
  it('calls the first stage Transcribe for audio, video and subtitle-only titles', () => {
    expect(STAGE_LABELS.source).toBe('Transcribe')
    for (const kind of ['audio', 'video', 'subtitle', null, undefined] as const) expect(stageLabel('source', kind)).toBe('Transcribe')
  })
  it('keeps Media for a novel or comic, which has nothing to transcribe', () => {
    expect(stageLabel('source', 'novel')).toBe('Media')
    expect(stageLabel('source', 'comic')).toBe('Media')
  })
  it('leaves the other stages alone', () => {
    expect(stageLabel('translate', 'comic')).toBe('Translate')
  })
})
