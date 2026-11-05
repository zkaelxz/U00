import { describe, expect, it } from 'vitest'

import type { VoiceEntry } from '../../types/voices'
import { retrainNote, similarityText, voiceSummary } from './voicesModel'

const voice = (over: Partial<VoiceEntry> = {}): VoiceEntry => ({
  series_id: 1, series_name: 'S', character_id: 2, character_name: 'Mei', sample_count: 3,
  linked: [], average_similarity: 0.874, lowest_similarity: 0.62, rebuildable: 3, ...over,
})

describe('voices model', () => {
  it('summarises samples and match', () => {
    expect(voiceSummary(voice())).toBe('3 samples · 87% average match, lowest 62%')
    expect(voiceSummary(voice({ sample_count: 1, average_similarity: null }))).toContain('1 sample ·')
    expect(similarityText(null)).toBe('no stored voice')
    expect(similarityText(0.911)).toBe('91% match')
  })

  it('says what a retrain would drop', () => {
    expect(retrainNote(voice())).toBe('Retrain rebuilds the print from the 3 linked speakers.')
    expect(retrainNote(voice({ rebuildable: 1 }))).toContain('2 older samples can\'t be rebuilt')
    expect(retrainNote(voice({ rebuildable: 0 }))).toContain('unavailable')
  })
})
