import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { DramaSummary } from '../../api/types'
import { AdminSection } from './AdminSection'
import type { AdminJob } from './useAdminJob'
import { SelectionBar } from './SelectionBar'
import {
  MAX_SELECTION, TRANSLATE_NEEDS, anyJobActive, describeBulkResult, describeClean, describeDeleteResult,
  describeScan, describeTranslateSkips, exportableCount, exportableIds, formatBytes, percent,
  pruneSelection, selectAllVisible, toggleId, translatableIds,
} from './libraryAdmin'

const d = (id: number, status = 'aligned', title = `T${id}`) =>
  ({ id, status, title_en: title, title_zh: null, custom_tags: [] }) as unknown as DramaSummary
const names = (id: number) => `T${id}`

describe('selection helpers', () => {
  it('toggles ids and never grows past the cap', () => {
    const s = toggleId(new Set([1]), 2)
    expect([...s]).toEqual([1, 2])
    expect([...toggleId(s, 1)]).toEqual([2])
    const full = new Set(Array.from({ length: MAX_SELECTION }, (_, i) => i + 1))
    expect(toggleId(full, 9999).size).toBe(MAX_SELECTION)
  })

  it('selects all visible, capped at 500', () => {
    expect(selectAllVisible([d(1), d(2)])).toEqual({ ids: new Set([1, 2]), capped: false })
    const many = Array.from({ length: 501 }, (_, i) => d(i + 1))
    const r = selectAllVisible(many)
    expect(r.ids.size).toBe(500)
    expect(r.capped).toBe(true)
  })

  it('prunes ids gone from the reloaded list, same set when unchanged', () => {
    const s = new Set([1, 2, 3])
    expect([...pruneSelection(s, [d(1), d(3)])]).toEqual([1, 3])
    expect(pruneSelection(s, [d(1), d(2), d(3), d(4)])).toBe(s)
  })

  it('counts translatable (aligned) and exportable dramas', () => {
    const items = [d(1, 'aligned'), d(2, 'translated'), d(3, 'dubbed'), d(4, 'not started')]
    expect(translatableIds(items)).toEqual([1])
    expect(exportableIds(items)).toEqual([2, 3])
    expect(exportableCount({ translated: 2, exported: 1, aligned: 5 })).toBe(3)
    expect(exportableCount(undefined)).toBe(0)
  })
})

describe('result lines', () => {
  it('bulk status/tags', () => {
    expect(describeBulkResult({ updated: 3, results: [1, 2, 3].map((id) => ({ drama_id: id, ok: true })) }))
      .toBe('Updated 3.')
    expect(describeBulkResult({
      updated: 2,
      results: [{ drama_id: 1, ok: true }, { drama_id: 2, ok: true }, { drama_id: 9, ok: false, error: 'not_found' }],
    })).toBe('2 updated, 1 not found.')
  })

  it('bulk delete names skipped dramas and keeps left-behind warnings', () => {
    expect(describeDeleteResult({
      deleted: 2,
      results: [
        { drama_id: 1, ok: true }, { drama_id: 2, ok: true, warning: 'Some files were left behind.' },
        { drama_id: 3, ok: false, error: 'job_running' },
      ],
    }, names)).toBe('Deleted 2. Skipped: T3 (a job is running). Some files were left behind.')
    expect(describeDeleteResult({ deleted: 1, results: [{ drama_id: 1, ok: true }] }, names)).toBe('Deleted 1.')
  })

  it('translate skips', () => {
    expect(describeTranslateSkips([], names)).toBeNull()
    expect(describeTranslateSkips([{ drama_id: 4, reason: 'not_aligned' }], names))
      .toBe('1 skipped: not aligned (T4)')
  })

  it('sizes, scan and clean', () => {
    expect(formatBytes(512)).toBe('512 B')
    expect(formatBytes(48_200_000_000)).toBe('48.2 GB')
    expect(formatBytes(3_100_000)).toBe('3.1 MB')
    expect(describeScan({
      preset: 'balanced', categories_to_clean: [], total_bytes: 48.2e9, reclaimable_bytes: 3.1e9,
      would_free_bytes: 2.4e9, categories: [], per_drama: [],
    })).toBe('Library 48.2 GB · 3.1 GB reclaimable · this preset frees 2.4 GB')
    expect(describeClean({
      preset: 'balanced', freed_bytes: 2.3e9,
      results: [{ drama_id: 1, ok: true }, { drama_id: 2, ok: false, error: 'job_running' }],
    })).toBe('Freed 2.3 GB. 1 drama skipped (job running).')
  })

  it('job activity and percent', () => {
    expect(anyJobActive([{ status: 'done' }, { status: 'queued' }])).toBe(true)
    expect(anyJobActive([{ status: 'done' }, { status: 'error' }])).toBe(false)
    expect(percent(0.4)).toBe('40%')
    expect(percent(null)).toBe('')
  })
})

const idleJob = (over: Partial<AdminJob> = {}): AdminJob => ({
  job: null, pollError: null, startError: null, active: false, done: false, info: null,
  start: () => Promise.resolve(null), cancel: () => {}, clearError: () => {}, ...over,
})

const bar = (pc: 'local' | 'remote', selected: DramaSummary[], phone = false, exporter = idleJob()) =>
  renderToStaticMarkup(createElement(SelectionBar, {
    selected, items: selected, pc, phone, exporter,
    onClear: () => {}, onDone: () => {}, onChanged: () => {}, onDeleted: () => {}, onResult: () => {},
  }))

describe('SelectionBar', () => {
  it('shows the count, Translate N and PC-only actions on the PC', () => {
    const out = bar('local', [d(1, 'aligned'), d(2, 'translated')])
    expect(out).toContain('2 selected')
    expect(out).toContain('Translate 1')
    expect(out).toContain('Export .zip (1)')
    expect(out).toContain('Delete…')
  })

  it('disables Translate with the reason when nothing selected is aligned', () => {
    const out = bar('local', [d(2, 'translated')])
    expect(out).toMatch(/<button[^>]*disabled=""[^>]*>Translate 0<\/button>/)
    expect(out).toContain(TRANSLATE_NEEDS.replace(/'/g, '&#x27;'))
  })

  it('disabled Export .zip explains itself', () => {
    const out = bar('local', [d(1, 'aligned')])
    expect(out).toMatch(/<button[^>]*disabled=""[^>]*aria-describedby="export-needs"[^>]*>Export .zip \(0\)/)
    expect(out).toContain('id="export-needs"')
  })

  it('relabelled list and status actions', () => {
    const out = bar('local', [d(1)])
    for (const l of ['>Set status<', '>Add to list<', '>Remove from list<']) expect(out).toContain(l)
  })

  it('remote: hides the export job line and download link', () => {
    const exporter = idleJob({ done: true, info: { kind: 'export', name: 'x.zip', size: 10 } })
    expect(bar('local', [d(2, 'translated')], false, exporter)).toContain('Download x.zip')
    expect(bar('remote', [d(2, 'translated')], false, exporter)).not.toContain('Download')
  })

  it('remote: no Delete or Export, a muted note instead', () => {
    const out = bar('remote', [d(1)])
    expect(out).not.toContain('Delete…')
    expect(out).not.toContain('Export .zip')
    expect(out).toContain('Delete and export are PC only.')
  })

  it('phone: count, an Actions menu and Done', () => {
    const out = bar('local', [d(1)], true)
    expect(out).toContain('<summary>Actions</summary>')
    expect(out).toContain('>Done</button>')
  })
})

describe('AdminSection', () => {
  it('remote: keeps the title, says PC only, renders no buttons', () => {
    const out = renderToStaticMarkup(createElement(AdminSection, { pc: 'remote', exportable: 3, exporter: idleJob() }))
    expect(out).toContain('Backup &amp; storage')
    expect(out).toContain('Run this on the main PC.')
    expect(out).not.toContain('<button')
  })

  it('on the PC: export, backup, restore and storage blocks', () => {
    const out = renderToStaticMarkup(createElement(AdminSection, { pc: 'local', exportable: 3, exporter: idleJob() }))
    for (const text of ['Export all translated (3)', 'Back up library', 'Database only',
      'Backup of just my stuff', 'Items owned at this PC', 'Back up just these items',
      'Site sign-ins are never included.', 'accept=".zip"', '>Scan</button>', 'Scan first.']) {
      expect(out).toContain(text)
    }
  })
})
