import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import { LineRow, type RowActions } from './LineRow'

const LINE: ReviewLine = {
  id: 7, idx: 6, start: 1, end: 3, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: 'take3.wav', lang: null, reviewed: false, confidence: null,
}

function row(simple: boolean, line: ReviewLine = LINE) {
  return renderToStaticMarkup(
    <LineRow
      dramaId={1} line={line} sourceLanguage="zh" active selected={false} isPhone={false} hasMedia jobRunning={false}
      limited={false} edit={null} ai={null} tm={null} issue={null} actions={{} as RowActions} simple={simple}
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
