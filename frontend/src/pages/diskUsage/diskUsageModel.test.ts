import { describe, expect, it } from 'vitest'

import type { DiskUsageItem } from '../../types/diskUsage'
import {
  barPercent, cellLabel, clearBlock, clearConfirmLabel, crumbs, describeCleared, describeEmptied, diskLine,
  CLIP_BATCH_SIZE, clipBatches, clipsDoneBeforeError, describeClipsStopped, sumClipResults,
  clipLine, clipTitle, clipsInUseText, describeClipsMoved, filesText, formatBytes, itemTone, linkedText, moveBlock, percentText, scanLinkedText, sizeLine, trashItemName, trashLine, trashSizeLine, trashedOn,
} from './diskUsageModel'

const item = (over: Partial<DiskUsageItem> = {}): DiskUsageItem => ({
  name: 'tmp', path: 'library/tmp', kind: 'folder', size_bytes: 1_500_000, file_count: 12,
  percent_of_parent: 40, modified_at: null, is_link: false, contains_link: false, linked_bytes: null, linked_files: null, linked_complete: null,
  complete: true, protected: false,
  protected_reason: null, regenerable: null, irreplaceable: false, irreplaceable_note: null,
  movable: { supported: false, reason: 'Fixed place.', what: null }, ...over,
})
const scan = { busy_reason: null }

describe('size formatting', () => {
  it('uses the shared decimal byte formatter', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(999)).toBe('999 B')
    expect(formatBytes(1_500_000)).toBe('1.5 MB')
    expect(formatBytes(48_200_000_000)).toBe('48.2 GB')
  })
  it('builds the size line and file counts', () => {
    expect(filesText(1)).toBe('1 file')
    expect(filesText(12345)).toBe('12,345 files')
    expect(sizeLine(item())).toBe('1.5 MB · 12 files')
    expect(sizeLine(item({ complete: false }))).toBe('at least 1.5 MB · 12 files')
  })
  it('shows percentages and bars sensibly', () => {
    expect(percentText(0)).toBe('0.0%')
    expect(percentText(0.04)).toBe('<0.1%')
    expect(percentText(33.33)).toBe('33.3%')
    expect(barPercent(item({ percent_of_parent: 0.01 }))).toBe(1.5)
    expect(barPercent(item({ size_bytes: 0, percent_of_parent: 0 }))).toBe(0)
    expect(barPercent(item({ percent_of_parent: 100.4 }))).toBe(100)
  })
  it('describes free space', () => {
    expect(diskLine({ disk_free_bytes: 2_800_000_000, disk_total_bytes: 252_000_000_000 })).toBe(
      '2.8 GB free of 252.0 GB on this drive')
    expect(diskLine({ disk_free_bytes: null, disk_total_bytes: null })).toBe('')
  })
})

describe('linked folders', () => {
  const linked = { linked_bytes: 7_200_000_000, linked_files: 2, linked_complete: true }
  it('labels a link with its target size and never calls it counted', () => {
    expect(linkedText(item({ is_link: true, ...linked }))).toBe(
      'Linked folder, stored elsewhere: 7.2 GB · 2 files')
    expect(linkedText(item({ is_link: true, ...linked, linked_complete: false }))).toBe(
      'Linked folder, stored elsewhere: at least 7.2 GB · 2 files')
  })
  it('tells a folder holding a link that the linked size is extra', () => {
    expect(linkedText(item({ contains_link: true, ...linked }))).toBe(
      'Plus 7.2 GB · 2 files in linked folders stored elsewhere, not counted in the size above.')
  })
  it('says nothing when no linked folder was measured, and never names a place', () => {
    expect(linkedText(item({ is_link: true }))).toBeNull()
    expect(linkedText(item({ is_link: true, ...linked }))).not.toMatch(/[\\/]/)
  })
  it('adds the folder total line', () => {
    expect(scanLinkedText({ linked_bytes: 7_200_000_000, linked_complete: true })).toBe('plus 7.2 GB in linked folders')
    expect(scanLinkedText({ linked_bytes: 7_200_000_000, linked_complete: false })).toBe('plus at least 7.2 GB in linked folders')
    expect(scanLinkedText({ linked_bytes: 0, linked_complete: true })).toBe('')
  })
  it('keeps a link or a folder holding one blocked from Trash', () => {
    expect(clearBlock(item({ is_link: true, protected: true, protected_reason: 'A link.', ...linked }), scan)).toBe('A link.')
    expect(clearBlock(item({ contains_link: true, ...linked }), scan)).toMatch(/link or junction/)
  })
})

describe('breadcrumbs', () => {
  it('starts at the data folder and opens each level', () => {
    expect(crumbs('')).toEqual([{ label: 'Data folder', path: '' }])
    expect(crumbs('library/dramas/1')).toEqual([
      { label: 'Data folder', path: '' }, { label: 'library', path: 'library' },
      { label: 'dramas', path: 'library/dramas' }, { label: '1', path: 'library/dramas/1' },
    ])
  })
})

describe('what can be done', () => {
  it('protected, busy and partly counted all block moving to Trash, with a reason', () => {
    expect(clearBlock(item(), scan)).toBeNull()
    expect(clearBlock(item({ protected: true, protected_reason: 'The Baihe database.' }), scan)).toBe('The Baihe database.')
    expect(clearBlock(item(), { ...scan, busy_reason: 'A job is running.' })).toBe('A job is running.')
    expect(clearBlock(item({ complete: false }), scan)).toMatch(/fully counted/)
  })
  it('refuses a folder holding a link, and Move works while the library is idle', () => {
    expect(moveBlock(item({ movable: { supported: true, reason: null, what: 'backups' } }), scan)).toBeNull()
    expect(clearBlock(item({ contains_link: true }), scan)).toMatch(/link or junction/)
  })
  it('move needs support and an idle library', () => {
    expect(moveBlock(item(), { busy_reason: null })).toBe('Fixed place.')
    const ok = item({ movable: { supported: true, reason: null, what: 'backups' } })
    expect(moveBlock(ok, { busy_reason: null })).toBeNull()
    expect(moveBlock(ok, { busy_reason: 'Busy.' })).toBe('Busy.')
  })
  it('tones: protected beats irreplaceable beats regenerable', () => {
    const regen = { label: 'x', note: 'y' }
    expect(itemTone(item({ protected: true, irreplaceable: true }))).toBe('protected')
    expect(itemTone(item({ irreplaceable: true }))).toBe('irreplaceable')
    expect(itemTone(item({ regenerable: regen }))).toBe('regenerable')
    expect(itemTone(item())).toBe('plain')
  })
  it('names the item and size in the confirm and the result', () => {
    expect(clearConfirmLabel(item())).toBe('Confirm: move tmp (1.5 MB) to Trash')
    const done = describeCleared({ moved_bytes: 1_500_000, file_count: 12, kind: 'folder', name: 'tmp', trash_id: 'x' })
    expect(done).toContain('Moved tmp (1.5 MB) to Trash')
    expect(done).toContain('Nothing is freed until you delete it from Trash')
  })
  it('says what Trash holds and that nothing is freed until deleting from it', () => {
    expect(trashLine(2_400_000_000)).toBe('Trash uses 2.4 GB; nothing is freed until you delete from it.')
    expect(trashLine(5, true)).toMatch(/^Trash uses at least 5 B/)
    expect(trashItemName({ original_path_relative: 'library/tmp' })).toBe('library/tmp')
    expect(trashItemName({ original_path_relative: null })).toMatch(/Unknown item/)
    expect(trashedOn({ trashed_at: '2026-10-03T10:00:00+00:00' })).toBe('2026-10-03')
    expect(trashedOn({ trashed_at: null })).toBe('')
    expect(trashSizeLine({ size_bytes: null, file_count: null })).toBe('Size unknown')
    expect(describeEmptied({ freed_bytes: 10, removed: 2, failed: 0 })).toBe('Emptied Trash: 2 items deleted, 10 B freed.')
    expect(describeEmptied({ freed_bytes: 10, removed: 1, failed: 1 })).toMatch(/1 could not be deleted and is still in Trash/)
  })
  it('labels treemap cells by how much room they have', () => {
    expect(cellLabel(item(), 5, 5)).toBe('')
    expect(cellLabel(item(), 10, 10)).toBe('tmp')
    expect(cellLabel(item(), 30, 20)).toBe('tmp\n1.5 MB')
  })
})

describe('unused voice clips text', () => {
  it('describes a clip by type, size and date only', () => {
    expect(clipLine({ file_type: 'wav', size_bytes: 1_500_000, modified_at: '2026-09-30T08:00:00+00:00' })).toBe('WAV clip · 1.5 MB · 2026-09-30')
    expect(clipLine({ file_type: 'mp3', size_bytes: 900, modified_at: null })).toBe('MP3 clip · 900 B')
  })

  it('names an untitled title and the left-out titles', () => {
    expect(clipTitle('  ')).toBe('Untitled')
    expect(clipTitle('Show')).toBe('Show')
    expect(clipsInUseText(1)).toContain('1 title is left out')
    expect(clipsInUseText(2)).toContain('2 titles are left out')
  })

  it('says what moved and what was skipped', () => {
    expect(describeClipsMoved({ moved_count: 2, moved_bytes: 3000, skipped: [] }))
      .toBe('Moved 2 clips (3.0 KB) to Trash. Nothing is freed until you delete them from Trash; you can restore them from there.')
    expect(describeClipsMoved({ moved_count: 0, moved_bytes: 0, skipped: [{ id: 'x', reason: 'changed' }] }))
      .toBe('No clips were moved. 1 clip skipped: it changed or a speaker started using it.')
  })
})

describe('clip batches and partial failures', () => {
  it('splits 501 clips into 500 + 1 and keeps the order', () => {
    const batches = clipBatches(Array.from({ length: 501 }, (_, i) => i))
    expect(batches.map((b) => b.length)).toEqual([CLIP_BATCH_SIZE, 1])
    expect(batches[1]).toEqual([500])
    expect(clipBatches([])).toEqual([])
  })

  it('adds batch results up and joins the skipped lists', () => {
    expect(sumClipResults([
      { moved_count: 2, moved_bytes: 20, skipped: [{ id: 'a', reason: 'changed' }] },
      { moved_count: 1, moved_bytes: 5, skipped: [{ id: 'b', reason: 'no_longer_unused' }] },
    ])).toEqual({ moved_count: 3, moved_bytes: 25, skipped: [{ id: 'a', reason: 'changed' }, { id: 'b', reason: 'no_longer_unused' }] })
  })

  it('reads what an error says was done, and tolerates details without it', () => {
    expect(clipsDoneBeforeError({ reason: 'busy', moved_count: 4, moved_bytes: 40, skipped: [] }).moved_count).toBe(4)
    expect(clipsDoneBeforeError(undefined)).toEqual({ moved_count: 0, moved_bytes: 0, skipped: [] })
    expect(clipsDoneBeforeError({ moved_count: 'x' }).moved_count).toBe(0)
  })

  it('says "Moved N of M" and why it stopped', () => {
    expect(describeClipsStopped({ moved_count: 502, moved_bytes: 2000, skipped: [{ id: 'a', reason: 'changed' }] }, 1001, 'A job is running.'))
      .toBe('Moved 502 of 1,001 clips (2.0 KB) to Trash; stopped because A job is running. 1 clip skipped. You can restore them from Trash.')
  })
})
