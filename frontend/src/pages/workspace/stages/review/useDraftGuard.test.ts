import { describe, expect, it, vi } from 'vitest'

import type { ReviewLine } from '../../../../types/review'
import type { EditState } from './LineRow'
import type { LinesController } from './linesController'
import { draftFromLine } from './reviewDraft'
import { leaveWithDraft, offerParkedDraft, parkDraft, readParkedDraft, rebaseDraft, unparkDraft } from './useDraftGuard'

vi.mock('../../../../api/restructure', () => ({ listAllLines: vi.fn() }))

class MemoryStore {
  private m = new Map<string, string>()
  get length() { return this.m.size }
  key(i: number) { return [...this.m.keys()][i] ?? null }
  getItem(k: string) { return this.m.get(k) ?? null }
  setItem(k: string, v: string) { this.m.set(k, v) }
  removeItem(k: string) { this.m.delete(k) }
}

const line = (id: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start: id, end: id + 0.5, zh: `zh ${id}`, en: `en ${id}`, speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, ...over,
})

const editing = (base: ReviewLine, en: string): EditState =>
  ({ lineId: base.id, base, draft: { ...draftFromLine(base), en }, details: false, note: null })

function fakeCtl(save: () => Promise<boolean>, restored = true) {
  return {
    dramaId: 7,
    stillDirty: () => true,
    saveEdit: vi.fn(save),
    setStatus: vi.fn(),
    restoreDraft: vi.fn(() => restored),
  } as unknown as LinesController & { saveEdit: ReturnType<typeof vi.fn>; setStatus: ReturnType<typeof vi.fn>; restoreDraft: ReturnType<typeof vi.fn> }
}

describe('parked drafts', () => {
  it('are kept per drama and line, and read back only for their drama', () => {
    const store = new MemoryStore()
    parkDraft(7, editing(line(1), 'typed'), store)
    expect(readParkedDraft(8, store)).toBeNull()
    expect(readParkedDraft(7, store)?.draft.en).toBe('typed')
    unparkDraft(7, 1, store)
    expect(readParkedDraft(7, store)).toBeNull()
  })

  it('drops an unreadable entry instead of offering it', () => {
    const store = new MemoryStore()
    store.setItem('baihe.review.draft.7.1', '{"base":null}')
    expect(readParkedDraft(7, store)).toBeNull()
    expect(store.length).toBe(0)
  })

  it('work without storage', () => {
    expect(() => parkDraft(7, editing(line(1), 'x'), null)).not.toThrow()
    expect(readParkedDraft(7, null)).toBeNull()
  })
})

describe('rebaseDraft', () => {
  it('keeps typed fields and lets untouched ones follow the saved line', () => {
    const old = line(1)
    const fresh = line(1, { zh: 'zh changed elsewhere', end: 3 })
    const draft = { ...draftFromLine(old), en: 'typed' }
    expect(rebaseDraft(old, fresh, draft)).toEqual({ ...draftFromLine(fresh), en: 'typed' })
  })
})

describe('leaving Review with a dirty draft', () => {
  it('clears the parked copy once the save succeeds', async () => {
    const store = new MemoryStore()
    const ctl = fakeCtl(async () => {
      expect(readParkedDraft(7, store)?.draft.en).toBe('typed')
      return true
    })
    await leaveWithDraft(ctl, editing(line(1), 'typed'), store)
    expect(ctl.saveEdit).toHaveBeenCalledTimes(1)
    expect(readParkedDraft(7, store)).toBeNull()
  })

  it('keeps the parked copy when the save fails', async () => {
    const store = new MemoryStore()
    await leaveWithDraft(fakeCtl(async () => false), editing(line(1), 'typed'), store)
    expect(readParkedDraft(7, store)?.draft.en).toBe('typed')
  })

  it('does nothing without an edit', () => {
    const ctl = fakeCtl(async () => true)
    expect(leaveWithDraft(ctl, null, new MemoryStore())).toBeNull()
    expect(ctl.saveEdit).not.toHaveBeenCalled()
  })
})

describe('opening Review again', () => {
  it('offers the failed draft back, rebased on the line as it is now', async () => {
    const store = new MemoryStore()
    const old = line(1)
    parkDraft(7, editing(old, 'typed'), store)
    const fresh = line(1, { zh: 'zh changed' })
    const ctl = fakeCtl(async () => true)
    await offerParkedDraft(ctl, () => false, store, async () => [fresh])
    expect(ctl.restoreDraft).toHaveBeenCalledWith(fresh, { ...draftFromLine(fresh), en: 'typed' })
    expect(readParkedDraft(7, store)).toBeNull()
  })

  it('keeps it for later when an edit is already open', async () => {
    const store = new MemoryStore()
    parkDraft(7, editing(line(1), 'typed'), store)
    await offerParkedDraft(fakeCtl(async () => true, false), () => false, store, async () => [line(1)])
    expect(readParkedDraft(7, store)).not.toBeNull()
  })

  it('drops it silently when the line already says the same', async () => {
    const store = new MemoryStore()
    parkDraft(7, editing(line(1), 'typed'), store)
    const ctl = fakeCtl(async () => true)
    await offerParkedDraft(ctl, () => false, store, async () => [line(1, { en: 'typed' })])
    expect(ctl.restoreDraft).not.toHaveBeenCalled()
    expect(readParkedDraft(7, store)).toBeNull()
  })

  it('says so when the line is gone', async () => {
    const store = new MemoryStore()
    parkDraft(7, editing(line(1), 'typed'), store)
    const ctl = fakeCtl(async () => true)
    await offerParkedDraft(ctl, () => false, store, async () => [line(2)])
    expect(ctl.setStatus).toHaveBeenCalledWith('An unsaved edit could not be brought back: its line no longer exists.')
    expect(readParkedDraft(7, store)).toBeNull()
  })

  it('keeps it when the lines cannot be read, or the panel closed meanwhile', async () => {
    const store = new MemoryStore()
    parkDraft(7, editing(line(1), 'typed'), store)
    await offerParkedDraft(fakeCtl(async () => true), () => false, store, async () => { throw new Error('down') })
    const ctl = fakeCtl(async () => true)
    await offerParkedDraft(ctl, () => true, store, async () => [line(1)])
    expect(ctl.restoreDraft).not.toHaveBeenCalled()
    expect(readParkedDraft(7, store)).not.toBeNull()
  })

  it('waits for the save still running from the last visit', async () => {
    const store = new MemoryStore()
    let finish: (ok: boolean) => void = () => {}
    const leaving = fakeCtl(() => new Promise<boolean>((r) => { finish = r }))
    const run = leaveWithDraft(leaving, editing(line(1), 'typed'), store)
    const loadLines = vi.fn(async () => [line(1, { en: 'typed' })])
    const ctl = fakeCtl(async () => true)
    const offer = offerParkedDraft(ctl, () => false, store, loadLines)
    await Promise.resolve()
    expect(loadLines).not.toHaveBeenCalled()
    finish(true)
    await run
    await offer
    expect(loadLines).not.toHaveBeenCalled()
    expect(ctl.restoreDraft).not.toHaveBeenCalled()
  })
})
