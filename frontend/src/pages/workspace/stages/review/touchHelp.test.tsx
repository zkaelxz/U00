import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import { LineActionsSheet } from './LineActionsSheet'
import { LineRow, type RowActions } from './LineRow'

const LINE: ReviewLine = {
  id: 7, idx: 6, start: 1, end: 3, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: 'timing_drift', flag_note: null, dub_filename: null, lang: null, reviewed: false, confidence: null,
}

const noop = () => {}

function sheet(timingSnap: { new_start: number; new_end: number } | null) {
  return renderToStaticMarkup(
    <LineActionsSheet
      state={{ lineId: 7, view: 'menu' }} line={LINE} run={[LINE]} hasMedia jobRunning={false} limited={false} busy={false}
      error={null} errorText={null} canRetranscribe={false} sourceLanguage="zh" timingSnap={timingSnap as never}
      actions={{
        onView: noop, onClose: noop, onPlay: noop, onEditDetails: noop, onRetranscribe: noop, onImprove: noop, onWhy: noop,
        onTool: noop, onDismissFlag: noop, onAddNote: noop, onSplit: noop, onMerge: noop, onAdd: noop, onDelete: noop,
        onSetLanguage: noop, onSnapToSpeech: noop, onSetReviewed: noop,
      }}
    />,
  )
}

describe('phone line sheet', () => {
  it('offers Snap to speech with the suggested times as visible text when the check found one', () => {
    const html = sheet({ new_start: 1, new_end: 2 })
    expect(html).toContain('Snap to speech')
    expect(html).toContain('saves a history snapshot first')
  })

  it('leaves it out when there is no suggestion', () => {
    expect(sheet(null)).not.toContain('Snap to speech')
  })
})

describe('desktop line toolbar reasons', () => {
  const actions = new Proxy({}, { get: () => vi.fn() }) as RowActions
  const render = (jobRunning: boolean) =>
    renderToStaticMarkup(
      <LineRow
        dramaId={1} line={LINE} sourceLanguage="zh" active selected={false} isPhone={false} hasMedia jobRunning={jobRunning}
        limited={false} edit={null} ai={null} tm={null} issue={null} actions={actions}
      />,
    )

  it('describes the disabled Split and Merge buttons with the visible reason', () => {
    const html = render(true)
    const id = `review-reason-${LINE.id}`
    expect(html).toContain(`id="${id}"`)
    expect(html.match(new RegExp(`aria-describedby="${id}"`, 'g'))).toHaveLength(2)
  })

  it('points at nothing when no job runs', () => {
    expect(render(false)).not.toContain(`review-reason-${LINE.id}`)
  })
})

describe('novel line row', () => {
  const actions = new Proxy({}, { get: () => vi.fn() }) as RowActions
  const render = (prose: boolean) =>
    renderToStaticMarkup(
      <LineRow
        dramaId={1} line={{ ...LINE, speaker: 'Narrator' }} prose={prose} sourceLanguage="zh" active selected={false}
        isPhone={false} hasMedia={false} jobRunning={false} limited={false} edit={null} ai={null} tm={null} issue={null}
        actions={actions}
      />,
    )

  it('leaves out the timestamp and speaker', () => {
    expect(render(false)).toContain('review-speaker')
    const html = render(true)
    expect(html).not.toContain('review-speaker')
    expect(html).not.toContain('review-time')
  })
})

describe('help that would otherwise be hover-only', () => {
  const actions = new Proxy({}, { get: () => vi.fn() }) as RowActions
  const render = (line: ReviewLine, active: boolean, isPhone: boolean, timingSnap: { new_start: number; new_end: number } | null = null) =>
    renderToStaticMarkup(
      <LineRow
        dramaId={1} line={line} sourceLanguage="zh" active={active} selected={false} isPhone={isPhone} hasMedia jobRunning={false}
        limited={false} edit={null} ai={null} tm={null} issue={null} actions={actions} timingSnap={timingSnap as never}
      />,
    )
  const shaky = { ...LINE, flag: null, confidence: 0.4 }

  it('spells the Shaky reason out under the active row on a phone, whose meta line has no room for a button', () => {
    const html = render(shaky, true, true)
    expect(html).toMatch(/data-testid="badge-reasons">Whisper was unsure of this line\.<\/p>/)
    expect(html).not.toContain('Help: Shaky')
  })

  it('gives the active row a help tip for the Shaky badge on a wider screen', () => {
    expect(render(shaky, true, false)).toContain('aria-label="Help: Shaky"')
  })

  it('adds neither to other rows, so only the active row has Tab stops', () => {
    for (const isPhone of [true, false]) {
      const html = render(shaky, false, isPhone)
      expect(html).not.toContain('Help: Shaky')
      expect(html).not.toContain('badge-reasons')
    }
  })

  it('shows the Snap to speech times as text on a phone', () => {
    const html = render({ ...LINE, flag: 'timing_uncertain' }, true, true, { new_start: 1, new_end: 2 })
    expect(html).toContain('Snap to speech')
    expect(html).toMatch(/<span class="muted" id="timing-snap-times-7">Sets .*saves a history snapshot first<\/span>/)
  })
})
