import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { QueueItem } from '../../types/queue'
import { QueueRow } from './JobQueue'

const item = (p: Partial<QueueItem> = {}): QueueItem => ({
  id: 7, drama_id: 3, title: 'Signal', step: 'transcribe', status: 'waiting', reason: null, job_id: null,
  engine: null, paid: false, mine: true, added_at: 1, started_at: null, finished_at: null, ...p,
})
const noop = () => undefined
const row = (p: Partial<QueueItem>, first = false, last = false) => renderToStaticMarkup(
  createElement(QueueRow, { item: item(p), first, last, busy: false, monthlyCap: 5, onMove: noop, onRemove: noop, onRetry: noop }),
)

describe('QueueRow', () => {
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
    expect(html).toContain('cancel its job in the list below')
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
