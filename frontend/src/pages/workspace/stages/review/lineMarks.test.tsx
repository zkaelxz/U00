import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import * as review from '../../../../api/review'
import type { ReviewLine } from '../../../../types/review'
import { LineMarkBadge, LineMarkButtons } from './LineMarkButtons'
import { chipLabel, emptyMessage, inFilter } from './reviewLogic'

const LINE: ReviewLine = {
  id: 7, idx: 6, start: 1, end: 3, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, mark: null,
}

describe('the Marked view', () => {
  it('lists a line that carries a mark, and says so when there is none', () => {
    expect(inFilter({ ...LINE, mark: 'wrong' }, 'marked')).toBe(true)
    expect(inFilter(LINE, 'marked')).toBe(false)
    // The server ranks a marked, unchecked line in Need a look even with no flag and a sure transcript.
    expect(inFilter({ ...LINE, mark: 'mixed' }, 'risk')).toBe(true)
    expect(inFilter(LINE, 'risk')).toBe(false)
    expect(chipLabel('marked', 2, false)).toBe('Marked 2')
    expect(emptyMessage('marked', '', 'Transcribe')).toBe('No lines marked wrong or mixed.')
  })
})

describe('setMark', () => {
  it('posts the mark with the English it was judged on, and null to clear', async () => {
    const calls: { url: string; body: unknown }[] = []
    const f = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), body: JSON.parse(String(init?.body)) })
      return new Response(JSON.stringify(LINE), { status: 200 })
    }) as typeof fetch
    await review.setMark(3, 7, 'wrong', 'Hello', f)
    await review.setMark(3, 7, null, 'Hello', f)
    expect(calls[0].url).toMatch(/\/dramas\/3\/lines\/7\/mark$/)
    expect(calls[0].body).toEqual({ mark: 'wrong', expected_en: 'Hello' })
    expect(calls[1].body).toEqual({ mark: null, expected_en: 'Hello' })
  })
})

describe('LineMarkButtons', () => {
  const html = (line: ReviewLine) => renderToStaticMarkup(<LineMarkButtons line={line} onMark={() => {}} />)

  it('offers Wrong and Mixed, shows the one the line has as pressed, and is off for an untranslated line', () => {
    expect(html(LINE)).toMatch(/aria-pressed="false"[^>]*aria-label="Wrong".*aria-pressed="false"[^>]*aria-label="Mixed"/s)
    const mixed = html({ ...LINE, mark: 'mixed' })
    expect(mixed).toMatch(/review-mark-wrong[^>]*aria-pressed="false"/)
    expect(mixed).toMatch(/review-mark-mixed[^>]*aria-pressed="true"[^>]*title="Clear the mixed mark"/)
    expect(html({ ...LINE, en: '' }).match(/disabled/g)).toHaveLength(2)
  })

  it('shows the tag only for a mark', () => {
    expect(renderToStaticMarkup(<LineMarkBadge line={LINE} />)).toBe('')
    expect(renderToStaticMarkup(<LineMarkBadge line={{ ...LINE, mark: 'wrong' }} />)).toContain('Wrong')
  })
})
