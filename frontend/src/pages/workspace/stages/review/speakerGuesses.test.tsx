import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { needsSpeaker, nextNeedingSpeakerId } from './reviewLogic'
import { guessReason, SpeakerGuesses } from './SpeakerGuesses'
import type { ReviewLine } from '../../../../types/review'

const line = (over: Partial<ReviewLine>) =>
  ({ id: 1, idx: 0, start: 0, end: 1, zh: '你好', en: 'Hi', speaker: null, speaker_manual: false, sfx: false, ...over }) as ReviewLine

describe('needsSpeaker', () => {
  it('is a line with words and no speaker nobody chose', () => {
    expect(needsSpeaker(line({}))).toBe(true)
    expect(needsSpeaker(line({ speaker: 'Anok' }))).toBe(false)
    expect(needsSpeaker(line({ speaker_manual: true }))).toBe(false)
    expect(needsSpeaker(line({ sfx: true }))).toBe(false)
    expect(needsSpeaker(line({ zh: ' ', en: '' }))).toBe(false)
  })
  it('steps over named lines in search results', () => {
    const lines = [line({ id: 1, speaker: 'A' }), line({ id: 2 }), line({ id: 3, speaker: 'A' }), line({ id: 4 })]
    expect(nextNeedingSpeakerId(lines, 0, 1)).toBe(2)
    expect(nextNeedingSpeakerId(lines, 1, 1)).toBe(4)
    expect(nextNeedingSpeakerId(lines, 3, -1)).toBe(2)
    expect(nextNeedingSpeakerId(lines, 3, 1)).toBeNull()
    expect(nextNeedingSpeakerId(lines, -1, -1)).toBe(4)
  })
})

describe('SpeakerGuesses', () => {
  const guesses = [
    { speaker: 'Chion', reasons: ['above', 'below'] as const },
    { speaker: 'Oumua', reasons: ['voice'] as const },
  ].map((g) => ({ ...g, reasons: [...g.reasons] }))

  it('says why each speaker is offered', () => {
    expect(guessReason(['above', 'below'])).toBe('Same as the line above and the line below')
    expect(guessReason(['below', 'voice'])).toBe('Same as the line below and the voice detection’s unsure guess')
    expect(guessReason(['voice'])).toBe('From the voice detection’s unsure guess')
  })
  it('is one button per speaker, with the keys only when asked', () => {
    const html = renderToStaticMarkup(<SpeakerGuesses guesses={guesses} lineNumber={12} showKeys tabbable onAccept={() => {}} />)
    expect(html).toContain('aria-label="Suggested speaker for line 12"')
    expect(html).toContain('aria-label="Chion: Same as the line above and the line below"')
    expect(html).toContain('aria-label="Oumua: From the voice detection’s unsure guess"')
    expect(html).toContain('<kbd aria-hidden="true">2</kbd>')
    const plain = renderToStaticMarkup(<SpeakerGuesses guesses={guesses} lineNumber={12} showKeys={false} tabbable={false} onAccept={() => {}} />)
    expect(plain).not.toContain('<kbd')
    expect(plain).toContain('tabindex="-1"')
  })
})
