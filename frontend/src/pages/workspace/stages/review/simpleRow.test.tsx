import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import { LineRow, type RowActions } from './LineRow'

const LINE: ReviewLine = {
  id: 7, idx: 6, start: 1, end: 3, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: 'take3.wav', lang: null, reviewed: false, confidence: null,
}

function row(simple: boolean, line: ReviewLine = LINE, { queue = false, isPhone = false, active = true } = {}) {
  return renderToStaticMarkup(
    <LineRow
      dramaId={1} line={line} sourceLanguage="zh" active={active} selected={false} isPhone={isPhone} hasMedia jobRunning={false}
      limited={false} edit={null} ai={null} tm={null} issue={null} actions={{} as RowActions} simple={simple} queue={queue}
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

  it('offers Open glossary for a glossary flag, beside the flag text', () => {
    const html = row(true, glossary, { queue: true })
    expect(html).toContain('Open glossary')
    expect(html).toContain('href="#/drama/1/translate?tab=glossary"')
    expect(html).toContain('Glossary missing · Glossary: 夷陵老祖 -&gt; Yiling Patriarch missing')
    expect(row(true, LINE, { queue: true })).not.toContain('Open glossary')
  })

  it('writes out why an unflagged line is listed, on every card on a wide screen', () => {
    const unsure = { ...LINE, confidence: 0.72 }
    expect(row(true, unsure, { queue: true, active: false })).toContain('Whisper was unsure of this line (72% sure)')
    expect(row(true, unsure)).not.toContain('Whisper was unsure of this line (72% sure)')
  })

  it('on a phone shows the whole reason wrapped instead of a cut-off chip, and the way to the lines around it', () => {
    const html = row(true, glossary, { queue: true, isPhone: true })
    expect(html).toContain('class="review-flag-note"')
    expect(html).not.toContain('review-flag-toggle')
    expect(html).toContain('See the lines around it')
    expect(html).toContain('Open glossary')
    expect(row(true, glossary, { isPhone: true })).toContain('review-flag-toggle')
  })
})

