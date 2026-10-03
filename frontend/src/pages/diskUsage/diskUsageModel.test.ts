import { describe, expect, it } from 'vitest'

import type { DiskUsageItem } from '../../types/diskUsage'
import {
  barPercent, cellLabel, clearBlock, clearConfirmLabel, crumbs, describeCleared, diskLine, filesText,
  formatBytes, itemTone, moveBlock, percentText, sizeLine,
} from './diskUsageModel'

const item = (over: Partial<DiskUsageItem> = {}): DiskUsageItem => ({
  name: 'tmp', path: 'library/tmp', kind: 'folder', size_bytes: 1_500_000, file_count: 12,
  percent_of_parent: 40, modified_at: null, is_link: false, complete: true, protected: false,
  protected_reason: null, regenerable: null, irreplaceable: false, irreplaceable_note: null,
  movable: { supported: false, reason: 'Fixed place.', what: null }, ...over,
})
const scan = { busy_reason: null, recycle_available: true }

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
  it('protected, busy, no bin and partly counted all block clearing, with a reason', () => {
    expect(clearBlock(item(), scan)).toBeNull()
    expect(clearBlock(item({ protected: true, protected_reason: 'The Baihe database.' }), scan)).toBe('The Baihe database.')
    expect(clearBlock(item(), { ...scan, busy_reason: 'A job is running.' })).toBe('A job is running.')
    expect(clearBlock(item(), { ...scan, recycle_available: false })).toMatch(/Recycle Bin/)
    expect(clearBlock(item({ complete: false }), scan)).toMatch(/fully counted/)
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
    expect(clearConfirmLabel(item())).toBe('Confirm: send tmp (1.5 MB) to the Recycle Bin')
    expect(describeCleared({ freed_bytes: 1_500_000, file_count: 12, kind: 'folder', name: 'tmp' })).toContain('Freed 1.5 MB')
  })
  it('labels treemap cells by how much room they have', () => {
    expect(cellLabel(item(), 5, 5)).toBe('')
    expect(cellLabel(item(), 10, 10)).toBe('tmp')
    expect(cellLabel(item(), 30, 20)).toBe('tmp\n1.5 MB')
  })
})
