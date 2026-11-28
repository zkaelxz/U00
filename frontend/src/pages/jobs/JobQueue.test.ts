import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { QueueItem } from '../../types/queue'
import { QueueRow } from './JobQueue'

const item = (p: Partial<QueueItem> = {}): QueueItem => ({
  id: 7, drama_id: 3, title: 'Signal', step: 'transcribe', status: 'waiting', reason: null, job_id: null,
  engine: null, paid: false, changed_settings: [], held: false, tonight: false, page_run: false, mine: true, added_at: 1, started_at: null, finished_at: null, ...p,
})
const noop = () => undefined
const row = (p: Partial<QueueItem>, first = false, last = false) => renderToStaticMarkup(
  createElement(QueueRow, { item: item(p), first, last, busy: false, monthlyCap: 5, onMove: noop, onRemove: noop, onRetry: noop, onHold: noop, onResume: noop, onCancel: noop, onTonight: noop }),
)

describe('QueueRow', () => {
  it('says what a queued transcribe step changes from the title', () => {
    const html = row({ changed_settings: [{ label: 'Beam size', value: '8', engine: null }, { label: 'Groq (paid)', value: 'On', engine: null }] })
    expect(html).toContain('Changed for this step: Beam size 8, Groq (paid) On')
  })

  it('says nothing when the step uses the title as saved', () => {
    expect(row({})).not.toContain('Changed for this step')
  })

  it('shows a skipped item with its reason and a Retry', () => {
    const html = row({ status: 'skipped', reason: 'Skipped: The title was deleted.' })
    expect(html).toContain('Skipped</span>')
    expect(html).toContain('Skipped: The title was deleted.')
    expect(html).toContain('aria-label="Retry Transcribe Signal"')
  })

  it('shows an interrupted item with Retry and Remove', () => {
    const html = row({ status: 'interrupted', reason: 'Interrupted: Baihe stopped while this ran.' })
    expect(html).toContain('Interrupted</span>')
    expect(html).toContain('aria-label="Remove Transcribe Signal"')
  })

  it('never offers Remove on a running item, and points at Cancel instead', () => {
    const html = row({ status: 'running' })
    expect(html).not.toContain('Remove Transcribe Signal')
    expect(html).toContain('Cancel…')
  })

  it('disables Move up on the first item and Move down on the last', () => {
    const html = row({}, true, true)
    expect(html).toMatch(/aria-label="Move up Transcribe Signal" disabled=""/)
    expect(html).toMatch(/aria-label="Move down Transcribe Signal" disabled=""/)
  })

  it('says a waiting paid Translate uses your key and costs money', () => {
    expect(row({ step: 'translate', engine: 'claude', paid: true })).toContain('Uses your API key and costs money.')
  })

  it("shows no controls on someone else's item", () => {
    expect(row({ mine: false, status: 'failed' })).not.toContain('<button')
  })
})

describe('Remove on a queued item', () => {
  it('asks first: the first step is "Remove…", not an immediate remove', () => {
    const html = renderToStaticMarkup(
      createElement(QueueRow, { item: item({ status: 'waiting' }), first: true, last: true, busy: false, monthlyCap: 5, onMove: noop, onRemove: noop, onRetry: noop, onHold: noop, onResume: noop, onCancel: noop, onTonight: noop }),
    )
    expect(html).toContain('Remove…')
    expect(html).not.toContain('Confirm remove')
  })
})

describe('Tonight on a queued item', () => {
  it('offers Run tonight on a waiting item, and Run now with a Tonight badge once it waits for the night', () => {
    const plain = row({ status: 'waiting' })
    expect(plain).toContain('Run tonight Transcribe Signal')
    expect(plain).not.toContain('Tonight</span>')
    const night = row({ status: 'waiting', tonight: true, reason: 'Waits for tonight, 1:00-7:00.' })
    expect(night).toContain('Run now Transcribe Signal')
    expect(night).toContain('Tonight</span>')
    expect(night).toContain('Waits for tonight, 1:00-7:00.')
  })
  it('offers no Run tonight on a step of a run started on a title page', () => {
    expect(row({ status: 'waiting', page_run: true })).not.toContain('Run tonight')
  })
  it('offers neither on a running or finished item', () => {
    for (const status of ['running', 'done'] as const) {
      const html = row({ status, tonight: true })
      expect(html).not.toContain('Run now')
      expect(html).not.toContain('Run tonight')
    }
  })
})

describe('Pause and Cancel on a queued item', () => {
  it('offers Pause and Cancel on a waiting item, and Resume once it is paused', () => {
    expect(row({ status: 'waiting' })).toContain('Pause Transcribe Signal')
    expect(row({ status: 'waiting' })).toContain('Cancel…')
    const paused = row({ status: 'waiting', held: true })
    expect(paused).toContain('Resume Transcribe Signal')
    expect(paused).toContain('Paused')
  })
  it('offers Cancel but not Pause on a running item', () => {
    const html = row({ status: 'running' })
    expect(html).toContain('Cancel…')
    expect(html).not.toContain('Pause Transcribe')
  })
})
