import { describe, expect, it } from 'vitest'

import { markEpisodeReviewedText as resultText } from './markEpisodeReviewedText'

const result = { reviewed: 0, remembered: 0, kept_existing: 0, skipped_flagged: 0, already_reviewed: 0 }

describe('resultText', () => {
  it('says so when nothing needed marking', () => {
    expect(resultText({ ...result, already_reviewed: 4 })).toBe('No lines needed marking.')
  })

  it('reports what was marked, remembered, kept and left alone', () => {
    expect(resultText({ ...result, reviewed: 3, remembered: 2, kept_existing: 1, skipped_flagged: 2 })).toBe(
      'Marked 3 lines reviewed; 2 added to translation memory; 1 already in memory kept; 2 flagged lines left alone.',
    )
  })
})
