import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import type { TimingSuggestion } from '../../../../types/timingCheck'
import { LineActionsSheet } from './LineActionsSheet'
import { LineRow, type RetranscribeState, type RowActions } from './LineRow'

const LINE: ReviewLine = {
  id: 7, idx: 6, start: 1, end: 3, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: 'take3.wav', lang: null, reviewed: false, confidence: null,
}

function row(simple: boolean, line: ReviewLine = LINE, { queue = false, isPhone = false, active = true, retranscribe = null as RetranscribeState | null,
  timingSnap = null as TimingSuggestion | null, actions = {} as RowActions, linePanel = false } = {}) {
  return renderToStaticMarkup(
    <LineRow
      dramaId={1} line={line} sourceLanguage="zh" active={active} selected={false} isPhone={isPhone} hasMedia jobRunning={false}
      limited={false} edit={null} ai={null} tm={null} issue={null} actions={actions} simple={simple} queue={queue}
      retranscribe={retranscribe} timingSnap={timingSnap} linePanel={linePanel}
    />,
  )
}

const noop = () => {}
const sheet = (line: ReviewLine) => renderToStaticMarkup(
  <LineActionsSheet
    state={{ lineId: line.id, view: 'menu' }} line={line} run={[line]} hasMedia jobRunning={false} limited busy={false}
    error={null} errorText={null} canRetranscribe={false} sourceLanguage="zh" timingSnap={{ line_id: 7, start: 1, end: 3, new_start: 1.2, new_end: 2.8 }}
    actions={{
      onView: noop, onClose: noop, onPlay: noop, onEditDetails: noop, onRetranscribe: noop, onImprove: noop, onWhy: noop,
      onTool: noop, onDismissFlag: noop, onAddNote: noop, onSplit: noop, onMerge: noop, onAdd: noop, onDelete: noop,
      onSetLanguage: noop, onSnapToSpeech: noop, onSetReviewed: noop,
    }}
  />,
)

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

  it('writes a queue row\'s time to the tenth, cut not rounded, open or closed, on a wide screen or a phone; other rows keep the hundredths', () => {
    const timed: ReviewLine = { ...LINE, start: 62.57, end: 64 }
    expect(row(true, timed, { queue: true })).toContain('<span class="review-time">1:02.5</span>')
    expect(row(true, timed, { queue: true, active: false })).toContain('<span class="review-time">1:02.5</span>')
    expect(row(true, timed, { queue: true, isPhone: true })).toContain('<span class="review-time">1:02.5</span>')
    expect(row(true, timed)).toContain('<span class="review-time">1:02.57</span>')
  })

  it('turns Mark checked into Looks right, next and keeps Improve and its tip; Edit details is left to More and D', () => {
    const html = row(true, LINE, { queue: true })
    expect(html).toContain('Looks right, next')
    expect(html).toContain('title="Mark checked and go to the next line (R)"')
    expect(html).not.toContain('>Mark checked<')
    expect(html).not.toContain('Edit details')
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
    // The closed card's chip keeps the score: on a wide screen it leaves out the Shaky badge that also says it.
    expect(closed).toContain('data-testid="queue-reason">Whisper was unsure of this line (72% sure)</span>')
    expect(row(true, unsure)).not.toContain('Whisper was unsure')
    // A closed card drops the full-width flag bar for its chip.
    const closedGlossary = row(true, glossary, { queue: true, active: false })
    expect(closedGlossary).toContain('data-testid="queue-reason">The English does not use the glossary name for 夷陵老祖</span>')
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

  it('gives the open card a quiet ⋯ for its menu and a wide closed row none, since the row opens on a click', () => {
    const more = '<button type="button" class="btn btn-ghost btn-sm review-more" aria-label="More actions for line 7" aria-haspopup="dialog">⋯</button>'
    expect(row(true, glossary, { queue: true })).toContain(more)
    expect(row(true, glossary, { queue: true })).not.toContain('>More<')
    const closed = row(true, glossary, { queue: true, active: false })
    expect(closed).not.toContain('More actions for line 7')
    expect(closed).not.toContain('⋯')
    // Outside the queue the row keeps its menu button, and the open row's says More.
    expect(row(true, glossary, { active: false })).toContain('⋯')
    expect(row(true, glossary)).toContain('>More<')
    expect(row(true, glossary, { queue: true, isPhone: true })).toContain('⋯')
  })

  it('keeps the number and the time in one cell, so a closed row can give them a column', () => {
    expect(row(true, glossary, { queue: true, active: false })).toContain('<span class="review-line-ref"><span class="review-idx">#7</span><span class="review-time">0:01.0</span></span>')
  })

  it('orders the open card\'s buttons as drawn: Play, the fitting action, Improve, then a green Looks right, next', () => {
    const html = row(true, glossary, { queue: true, linePanel: true })
    const at = ['Play line', 'Open glossary', 'Improve translation', 'Looks right, next'].map((t) => html.indexOf(t))
    expect(at.every((n) => n > 0)).toBe(true)
    expect([...at].sort((a, b) => a - b)).toEqual(at)
    expect(html).toMatch(/class="btn btn-ok btn-sm review-queue-done"[^>]*>Looks right, next/)
    expect(html).not.toContain('btn-primary')
    for (const gone of ['Edit details', 'Dismiss flag', 'See the lines around it']) expect(html).not.toContain(gone)
    const snap: TimingSuggestion = { line_id: 7, start: 1, end: 3, new_start: 1.2, new_end: 2.8 }
    const withSnap = row(true, glossary, { queue: true, timingSnap: snap, actions: { snapToSpeech: () => {} } as unknown as RowActions })
    expect(withSnap).not.toContain('Snap to speech')
    expect(row(true, { ...glossary, flag: 'source_changed' }, { queue: true }).match(/Keep English/g)).toHaveLength(1)
  })

  it('with the This line panel beside it, the open card says its reason in a few words; without it, in full with See the lines around it', () => {
    const beside = row(true, glossary, { queue: true, linePanel: true })
    expect(beside).toContain('data-testid="queue-reason">The English does not use the glossary name for 夷陵老祖</span>')
    expect(beside).not.toContain(GLOSSARY_SENTENCE)
    const alone = row(true, glossary, { queue: true })
    expect(alone).toContain(GLOSSARY_SENTENCE)
    expect(alone).toMatch(/<button type="button" class="btn btn-ghost btn-sm">See the lines around it<\/button>/)
    expect(alone.indexOf('Improve translation')).toBeLessThan(alone.indexOf('See the lines around it'))
    expect(alone.indexOf('See the lines around it')).toBeLessThan(alone.indexOf('Looks right, next'))
  })

  it('still offers Edit details, Snap to speech and Dismiss flag or Keep English in the card\'s More sheet', () => {
    const html = sheet(glossary)
    for (const t of ['>Edit details<', 'Snap to speech', '>Dismiss flag<']) expect(html).toContain(t)
    expect(sheet({ ...LINE, flag: 'source_changed' })).toContain('>Keep English<')
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


describe('a flagged line outside the check queue', () => {
  const speed = { ...LINE, flag: 'reading_speed', flag_note: '25.2 characters/second -- more than ~17/s is hard to read in time. The speech is this fast: shorten the text.' }
  const SPEED_SENTENCE = 'This subtitle is too fast to read: 25.2 characters a second, and more than about 17 is hard to read in time. Shorten the text.'
  const free = { ...LINE, flag: 'uncertain_translation', flag_note: 'the pun on 饺子 may not land' }
  const FREE_SENTENCE = 'The English may not say what the source says, or may read awkwardly. Read both and fix the English if needed. Note: The pun on 饺子 may not land'
  const unknown = { ...LINE, flag: 'uncertain', flag_note: 'check' }

  it('says why in the check card\'s sentence under the meta line in Full and under All, with the same words in its title', () => {
    for (const simple of [false, true]) {
      for (const [line, said] of [[speed, SPEED_SENTENCE], [free, FREE_SENTENCE], [unknown, 'Uncertain · Check']] as const) {
        expect(row(simple, line)).toContain(`data-testid="line-flag" title="${said}"><span aria-hidden="true">⚑</span> ${said}</p>`)
      }
    }
    expect(row(false, speed)).not.toContain('Reading speed')
  })

  it('on a phone reads the sentence to a screen reader and shows the few words on the active row, the sentence in its tooltip', () => {
    const closed = row(false, speed, { isPhone: true, active: false })
    expect(closed).toContain(`<span class="sr-only"> Flagged: ${SPEED_SENTENCE}</span>`)
    expect(closed).not.toContain('review-flag-toggle')
    const html = row(false, speed, { isPhone: true })
    expect(html).toContain(`title="Flagged: ${SPEED_SENTENCE}" aria-expanded="false">Flagged: Too fast to read, 25 characters a second</button>`)
    expect(row(true, free, { isPhone: true })).toContain('>Flagged: Translation may be off</button>')
    expect(row(true, unknown, { isPhone: true })).toContain('>Flagged: Uncertain</button>')
  })

  it('writes the sentence at the top of the More sheet', () => {
    expect(sheet(speed)).toContain(`data-testid="sheet-flag"><span aria-hidden="true">⚑ </span>Flagged: ${SPEED_SENTENCE}</p>`)
    expect(sheet(free)).toContain(`data-testid="sheet-flag"><span aria-hidden="true">⚑ </span>Flagged: ${FREE_SENTENCE}</p>`)
    expect(sheet(unknown)).toContain('data-testid="sheet-flag"><span aria-hidden="true">⚑ </span>Flagged: Uncertain · Check</p>')
  })
})
