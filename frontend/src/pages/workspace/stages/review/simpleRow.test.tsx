import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import type { TimingSuggestion } from '../../../../types/timingCheck'
import { LineRow, type RetranscribeState, type RowActions } from './LineRow'

const LINE: ReviewLine = {
  id: 7, idx: 6, start: 1, end: 3, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: 'take3.wav', lang: null, reviewed: false, confidence: null,
}

function row(simple: boolean, line: ReviewLine = LINE, { queue = false, isPhone = false, active = true, retranscribe = null as RetranscribeState | null,
  timingSnap = null as TimingSuggestion | null, actions = {} as RowActions } = {}) {
  return renderToStaticMarkup(
    <LineRow
      dramaId={1} line={line} sourceLanguage="zh" active={active} selected={false} isPhone={isPhone} hasMedia jobRunning={false}
      limited={false} edit={null} ai={null} tm={null} issue={null} actions={actions} simple={simple} queue={queue}
      retranscribe={retranscribe} timingSnap={timingSnap}
    />,
  )
}

describe('Simple view line row', () => {
  it('drops the end time, dub name and the extra tools but keeps the ones most lines need', () => {
    const html = row(true)
    expect(html).not.toContain('Dub:')
    expect(html).not.toContain('Why this?')
    expect(html).not.toContain('Split…')
    expect(html).not.toContain('Merge ↓')
    expect(html).toContain('Edit details')
    expect(html).toContain('Improve translation')
    expect(html).toContain('Mark checked')
  })

  it('shows all of it in Full view', () => {
    const html = row(false)
    expect(html).toContain('Dub: take3.wav')
    expect(html).toContain('Why this?')
    expect(html).toContain('Split…')
    expect(html).toContain('Merge ↓')
  })
})

describe('check queue card', () => {
  const glossary: ReviewLine = { ...LINE, flag: 'glossary_missing', flag_note: 'Glossary: 夷陵老祖 -> Yiling Patriarch missing' }

  it('turns Mark checked into Looks right, next and keeps the other tools and their tips', () => {
    const html = row(true, LINE, { queue: true })
    expect(html).toContain('Looks right, next')
    expect(html).toContain('title="Mark checked and go to the next line (R)"')
    expect(html).not.toContain('>Mark checked<')
    expect(html).toContain('Edit details')
    expect(html).toContain('title="Improve translation (I)"')
  })

  it('keeps Mark checked outside the queue', () => {
    expect(row(true, LINE)).toContain('Mark checked')
    expect(row(true, LINE)).not.toContain('Looks right, next')
  })

  const GLOSSARY_SENTENCE = 'The glossary writes 夷陵老祖 as “Yiling Patriarch”, but the English doesn’t use that name.'

  it('offers Open glossary for a glossary flag, beside the reason in plain words', () => {
    const html = row(true, glossary, { queue: true })
    expect(html).toContain('Open glossary')
    expect(html).toContain('href="#/drama/1/translate?tab=glossary"')
    expect(html).toContain(GLOSSARY_SENTENCE)
    expect(html).not.toContain('-&gt;')
    expect(row(true, LINE, { queue: true })).not.toContain('Open glossary')
  })

  it('writes out why a line is listed on every card on a wide screen: the whole sentence open, a few words closed', () => {
    const unsure = { ...LINE, confidence: 0.72 }
    expect(row(true, unsure, { queue: true })).toContain('Whisper was unsure of this line (72% sure)')
    const closed = row(true, unsure, { queue: true, active: false })
    expect(closed).toContain('data-testid="queue-reason">Whisper was unsure</span>')
    expect(closed).not.toContain('72% sure')
    expect(row(true, unsure)).not.toContain('Whisper was unsure')
    // A closed card drops the full-width flag bar for its chip.
    const closedGlossary = row(true, glossary, { queue: true, active: false })
    expect(closedGlossary).toContain('Glossary name not used: 夷陵老祖')
    expect(closedGlossary).not.toContain('data-testid="line-flag"')
    expect(closedGlossary).not.toContain(GLOSSARY_SENTENCE)
  })

  it('on a phone says Why in a wrapped sentence instead of a cut-off chip, with the way to the lines around it under the English', () => {
    const html = row(true, glossary, { queue: true, isPhone: true })
    expect(html).toContain(`class="review-queue-why" data-testid="queue-reason">Why: ${GLOSSARY_SENTENCE}`)
    expect(html).not.toContain('review-flag-toggle')
    expect(html).toContain('See the lines around it')
    expect(html).toContain('Open glossary')
    expect(html).toContain('title="Improve translation (I)"')
    expect(html.indexOf('data-testid="line-en"')).toBeLessThan(html.indexOf('See the lines around it'))
    expect(row(true, glossary, { isPhone: true })).toContain('review-flag-toggle')
  })

  it('labels the English and draws it as a box on the open card only, still the button that opens the editor', () => {
    const open = row(true, glossary, { queue: true })
    expect(open).toMatch(/<div class="review-queue-en"><span class="review-queue-label">English<\/span><button type="button" class="review-en" data-testid="line-en"/)
    expect(row(true, glossary, { queue: true, isPhone: true })).toContain('<span class="review-queue-label">English</span>')
    expect(row(true, glossary, { queue: true, active: false })).not.toContain('review-queue-label')
    expect(row(true, glossary)).not.toContain('review-queue-label')
    expect(row(false, glossary)).not.toContain('review-queue-label')
  })

  it('orders the open card\'s buttons Play, the fitting action, Improve, Edit details, Dismiss flag, then a green Looks right, next', () => {
    const html = row(true, glossary, { queue: true })
    const at = ['▶ Play', 'Open glossary', 'Improve translation', 'Edit details', 'Dismiss flag', 'Looks right, next'].map((t) => html.indexOf(t))
    expect(at.every((n) => n > 0)).toBe(true)
    expect([...at].sort((a, b) => a - b)).toEqual(at)
    expect(html).toMatch(/class="btn btn-ok btn-sm review-queue-done"[^>]*>Looks right, next/)
    expect(html).not.toContain('btn-primary')
  })

  it('offers the existing action that fits each reason, with its gating, and none where nothing fits', () => {
    const suspect = { ...LINE, flag: 'source_suspect', flag_note: 'is mostly Latin letters' }
    const ready = row(true, suspect, { queue: true, retranscribe: { disabled: false, reason: null } })
    expect(ready).toMatch(/<button type="button" class="btn btn-secondary btn-sm">Re-transcribe…<\/button>/)
    const noAudio = row(true, { ...LINE, confidence: 0.4 }, { queue: true, retranscribe: { disabled: true, reason: 'Needs this title’s audio or video.' } })
    expect(noAudio).toMatch(/disabled="" aria-describedby="queue-retranscribe-7">Re-transcribe…/)
    expect(noAudio).toContain('id="queue-retranscribe-7">Needs this title’s audio or video.</span>')

    const changed = row(true, { ...LINE, flag: 'source_changed' }, { queue: true })
    expect(changed).toContain('title="Keep this English for the new source text (F)">Keep English</button>')
    expect(changed.match(/Keep English/g)).toHaveLength(1)

    const snap: TimingSuggestion = { line_id: 7, start: 1, end: 3, new_start: 1.2, new_end: 2.8 }
    const drift = { ...LINE, flag: 'timing_drift', flag_note: 'Ends 1.2 s after speech.' }
    const withSnap = row(true, drift, { queue: true, timingSnap: snap, actions: { snapToSpeech: () => {} } as unknown as RowActions })
    expect(withSnap.match(/Snap to speech/g)).toHaveLength(1)
    expect(withSnap.indexOf('Snap to speech')).toBeLessThan(withSnap.indexOf('Improve translation'))
    expect(row(true, drift, { queue: true })).not.toContain('Snap to speech')

    expect(row(true, { ...LINE, flag: 'content_blocked', flag_note: 'gemini: SAFETY' }, { queue: true })).toContain('data-testid="blocked-retry"')
    const speed = row(true, { ...LINE, flag: 'reading_speed' }, { queue: true, retranscribe: { disabled: false, reason: null } })
    for (const t of ['Re-transcribe', 'Open glossary', 'Keep English', 'Snap to speech', 'blocked-retry']) expect(speed).not.toContain(t)
  })
})

