import { describe, expect, it } from 'vitest'

import { NARRATION_RESUME_NOTE, narrationResumeNote } from './novelSetup'

describe('narrationResumeNote', () => {
  it('explains Start over only when a narration run resumes', () => {
    expect(narrationResumeNote('Resuming: 3 of 8 chunks already tagged...')).toBe(NARRATION_RESUME_NOTE)
    expect(narrationResumeNote('Tagging chunk 4 of 8')).toBeNull()
    expect(narrationResumeNote('Not Resuming: x')).toBeNull()
    expect(narrationResumeNote('')).toBeNull()
    expect(narrationResumeNote(null)).toBeNull()
    expect(narrationResumeNote(undefined)).toBeNull()
  })
})
