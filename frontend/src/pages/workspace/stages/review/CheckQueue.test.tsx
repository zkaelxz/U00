import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { ReviewLine, ReviewLinesPage } from '../../../../types/review'
import { CheckLeadAction, CheckQueueFooter, CheckQueueSwitch, QueueFine } from './CheckQueue'
import type { QueueCounts } from './checkQueueModel'

const line = (id: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start: id, end: id + 1, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: 'reading_speed', flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, mark: null, ...over,
})
const page = (lines: ReviewLine[]): ReviewLinesPage => ({
  lines, page: 1, page_size: 40, total: lines.length, flagged_count: 0, untranslated_count: 0, unreviewed_count: 0, shaky_count: 0, risk_count: lines.length, marked_count: 0, no_speaker_count: 0,
})
const counts: QueueCounts = { all: 24, risk: 3, untranslated: 0, unreviewed: 24, marked: 0 }

function footer(queuePage: ReviewLinesPage, { settled = true, jobRunning = false, c = counts, why = false } = {}) {
  return renderToStaticMarkup(
    <CheckQueueFooter dramaId={1} queuePage={queuePage} counts={c} jobRunning={jobRunning} settled={settled} why={why} onReadAll={() => {}} onUntranslated={() => {}} onChanged={() => {}} />,
  )
}

describe('the check queue footer', () => {
  const clean = page([line(1), line(2), line(3)])

  it('is a sentence and two links on one line, with the reason hidden until Mark the rest is pressed', () => {
    const html = footer(page([line(1), line(2, { flag: null })]))
    expect(html).toContain('<span>The other 21 lines have no warnings.</span>')
    expect(html).toMatch(/<button type="button" class="check-queue-link">Read them all<\/button>/)
    expect(html).toMatch(/<button type="button" class="check-queue-link" aria-label="Mark the rest as checked">Mark the rest as checked…<\/button>/)
    expect(html).not.toContain('mark-rest-reason')
    expect(html).not.toMatch(/class="check-queue-link"[^>]*aria-describedby/)
    expect(html).not.toContain('Confirm')
    expect(html).not.toMatch(/class="btn /)
  })

  it('carries the (i) "Why these lines" beside its sentence on a phone only, where no "look fine" row has it', () => {
    const lines = page([line(1), line(2, { flag: null })])
    expect(footer(lines, { why: true })).toMatch(/<span>The other 21 lines have no warnings\.<\/span><span class="field-help">[\s\S]*Why these lines/)
    expect(footer(lines)).not.toContain('Why these lines')
  })

  it('keeps Mark the rest as checked… as the two-step button, drawn as a link, when nothing blocks it', () => {
    const html = footer(clean)
    expect(html).toMatch(/<button type="button" class="check-queue-link" aria-label="Mark the rest as checked">Mark the rest as checked…<\/button>/)
    expect(html).not.toContain('mark-rest-reason')
  })

  it('disables Mark the rest as checked… while the list is still loading, blocked or not', () => {
    expect(footer(clean, { settled: false })).toMatch(/class="check-queue-link"[^>]*disabled=""[^>]*>Mark the rest as checked…/)
    expect(footer(page([line(1, { flag: null })]), { settled: false })).toMatch(/class="check-queue-link"[^>]*disabled=""[^>]*>Mark the rest as checked…/)
  })

  it('puts the untranslated lines on their own line under it, as a link', () => {
    const html = footer(clean, { c: { ...counts, untranslated: 2 } })
    expect(html).toContain('<p class="check-queue-untranslated">2 lines have no English yet. <button type="button" class="check-queue-link">Show untranslated</button></p>')
  })
})

describe('the first row', () => {
  it('is two radios named with their counts in one group named Lines to show', () => {
    const html = renderToStaticMarkup(<CheckQueueSwitch queue marked={false} counts={counts} onPick={() => {}} onMarked={() => {}} />)
    expect(html).toContain('<legend class="visually-hidden">Lines to show</legend>')
    expect(html).toContain('<span>Need a look 3</span>')
    expect(html).toContain('<span>All 24</span>')
    expect(html.match(/type="radio" name="check-queue"/g)).toHaveLength(2)
    expect(html).toMatch(/name="check-queue" checked=""[^>]*\/><span>Need a look 3/)
  })

  it('has a Marked radio between the two once a line is marked, and while Marked is the view', () => {
    const draw = (marked: boolean, n: number) =>
      renderToStaticMarkup(<CheckQueueSwitch queue={false} marked={marked} counts={{ ...counts, marked: n }} onPick={() => {}} onMarked={() => {}} />)
    const some = draw(false, 2)
    expect(some.match(/<span>[^<]*<\/span>/g)).toEqual(['<span>Need a look 3</span>', '<span>Marked 2</span>', '<span>All 24</span>'])
    expect(some).toMatch(/name="check-queue" checked=""[^>]*\/><span>All 24/)
    const showing = draw(true, 0)
    expect(showing).toMatch(/name="check-queue" checked=""[^>]*\/><span>Marked 0/)
    expect(showing.match(/checked=""/g)).toHaveLength(1)
  })

  it('puts "look fine" and its (i) together at the far end', () => {
    expect(renderToStaticMarkup(<QueueFine counts={counts} />)).toMatch(/^<span class="check-queue-fine-group"><span class="muted check-queue-fine" data-testid="queue-fine">21 of 24 look fine<\/span><span class="field-help">/)
  })

  it('draws the main button only once it is known, so nothing in the row moves when it arrives', () => {
    const action = <a href="#c">Check the 3 lines</a>
    const noop = () => {}
    expect(renderToStaticMarkup(<CheckLeadAction lead={{ action, ready: false, onShown: noop }} />)).toBe('')
    expect(renderToStaticMarkup(<CheckLeadAction lead={{ action, ready: true, onShown: noop }} />)).toBe('<a href="#c">Check the 3 lines</a>')
  })
})
