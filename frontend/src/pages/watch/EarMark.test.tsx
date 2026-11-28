import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { AssStyleOptions } from '../../types/export'
import type { ReviewLine } from '../../types/review'
import { buildAssRequest, emptyAssForm } from '../workspace/exportForm'
import { ExportAss } from '../workspace/stages/ExportAss'
import { EarMark } from './EarMark'
import { earMarkMap, earMarksNext } from './useEarMarks'
import { NowCard } from './NowCard'

const line: ReviewLine = {
  id: 7, idx: 0, start: 1, end: 2, zh: '在你耳边', en: 'In your ear', speaker: null, speaker_manual: false, sfx: false,
  flag: null, flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null, mark: null, unsure_words: null,
}

describe('EarMark', () => {
  it('names the ear for a screen reader and shows the arrow', () => {
    expect(renderToStaticMarkup(<EarMark side="left" />)).toContain('aria-label="Left ear"')
    expect(renderToStaticMarkup(<EarMark side="left" />)).toContain('◀')
    expect(renderToStaticMarkup(<EarMark side="right" />)).toContain('aria-label="Right ear"')
    expect(renderToStaticMarkup(<EarMark side={null} />)).toBe('')
  })

  it('shows on the line being spoken', () => {
    const html = renderToStaticMarkup(
      <NowCard line={line} ear="right" sourceLanguage="ja" canEdit={false} fix={null} busy={false} status=""
        onOpen={vi.fn()} onDraft={vi.fn()} onSave={vi.fn()} onCancel={vi.fn()} onAgain={vi.fn()} />,
    )
    expect(html).toContain('aria-label="Right ear"')
  })

  it('maps marks by line id, only once they are current', () => {
    const marks = [{ line_id: 7, side: 'left' as const }]
    expect(earMarkMap({ drama_id: 1, state: 'ready', marks, one_sided: 1, total_lines: 3 }).get(7)).toBe('left')
    expect(earMarkMap({ drama_id: 1, state: 'needed', marks, one_sided: 1, total_lines: 3 }).size).toBe(0)
  })

  it('asks the PC to work them out once, then waits while it works', () => {
    expect(earMarksNext('needed', true, false)).toBe('start')
    expect(earMarksNext('needed', true, true)).toBe('stop')
    expect(earMarksNext('needed', false, false)).toBe('stop')
    expect(earMarksNext('working', false, true)).toBe('poll')
    expect(earMarksNext('ready', true, false)).toBe('stop')
    expect(earMarksNext('unavailable', true, false)).toBe('stop')
  })
})

describe('Export ear placement', () => {
  const options = {
    presets: { Clean: {} }, default_preset: 'Clean', fonts: [], alignments: { 'bottom-center': 2 },
    size_range: [8, 96], outline_width_range: [0, 10], shadow_range: [0, 10],
  } as unknown as AssStyleOptions

  it('is offered only for a title with ear marks', () => {
    const form = emptyAssForm('Clean')
    const render = (earMarks: boolean) =>
      renderToStaticMarkup(<ExportAss form={form} setForm={vi.fn()} options={options} earMarks={earMarks} />)
    expect(render(true)).toContain('Place one-ear lines left or right')
    expect(render(false)).not.toContain('Place one-ear lines left or right')
  })

  it('is sent only when turned on', () => {
    expect(buildAssRequest(emptyAssForm('Clean'), options).request).not.toHaveProperty('ear_placement')
    expect(buildAssRequest({ ...emptyAssForm('Clean'), earPlacement: true }, options).request?.ear_placement).toBe(true)
  })
})
