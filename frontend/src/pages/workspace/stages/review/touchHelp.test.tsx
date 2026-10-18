import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import { LineActionsSheet } from './LineActionsSheet'
import { LineRow, type RowActions } from './LineRow'

const LINE: ReviewLine = {
  id: 7, idx: 6, start: 1, end: 3, zh: '你好', en: 'Hello', speaker: null, speaker_manual: false,
  sfx: false, flag: 'timing_drift', flag_note: null, dub_filename: null, lang: null,
}

const noop = () => {}

function sheet(timingSnap: { new_start: number; new_end: number } | null) {
  return renderToStaticMarkup(
    <LineActionsSheet
      state={{ lineId: 7, view: 'menu' }} line={LINE} run={[LINE]} hasMedia jobRunning={false} limited={false} busy={false}
      error={null} errorText={null} onView={noop} onClose={noop} onPlay={noop} onEditDetails={noop} canRetranscribe={false}
      onRetranscribe={noop} onImprove={noop} onWhy={noop} onTool={noop} onDismissFlag={noop} onAddNote={noop} onSplit={noop}
      onMerge={noop} onAdd={noop} onDelete={noop} sourceLanguage="zh" onSetLanguage={noop}
      timingSnap={timingSnap as never} onSnapToSpeech={noop}
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
