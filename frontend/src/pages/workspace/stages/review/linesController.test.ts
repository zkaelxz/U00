import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../../api/client'
import type { LinePatch, ReviewLine } from '../../../../types/review'
import type { EditState } from './LineRow'
import { afterLinesWrite, beforeLinesWrite, createLinesController, registerLinesWriter, type LinesState, type Pending } from './linesController'
import { draftFromLine } from './reviewDraft'

const api = vi.hoisted(() => ({
  patchLine: vi.fn(),
  acceptTm: vi.fn(),
}))
vi.mock('../../../../api/review', () => ({
  patchLine: api.patchLine,
  acceptTm: api.acceptTm,
  addNote: vi.fn(),
  dismissFlag: vi.fn(),
  flaggedAdjacent: vi.fn(),
}))
vi.mock('../../../../api/timingCheck', () => ({ snapToSpeech: vi.fn() }))

const line = (id: number, en = `en ${id}`): ReviewLine => ({
  id, idx: id - 1, start: id, end: id + 0.5, zh: `zh ${id}`, en, speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null,
})

const conflict = () => new ApiError(409, { code: 'conflict', message: 'changed' })

function setup(lines: ReviewLine[], edit: EditState | null = null) {
  const st = {
    current: {
      shown: lines, edit, page: 1, pages: 1, filter: 'all', searching: false,
      onChanged: vi.fn(), jobRunning: false, activeId: null,
    } as LinesState,
  }
  const status = vi.fn()
  const setIssue = vi.fn()
  const pending = { current: null as Pending | null }
  const setPage = vi.fn()
  const ctl = createLinesController({
    dramaId: 7, st, pending, focusActive: { current: null }, player: { current: null },
    selectRef: { current: vi.fn() }, showOnPageRef: { current: vi.fn() },
    setData: vi.fn(), setFound: vi.fn(), setIssue, setEdit: vi.fn(), setStatus: status,
    setActiveId: vi.fn(), setPage, setFilter: vi.fn(), setError: vi.fn(), setSheet: vi.fn(),
    setStructError: vi.fn(), setAi: vi.fn(), setRetranscribeFocusId: vi.fn(),
  })
  return { ctl, st, status, setIssue, pending, setPage }
}

const editing = (base: ReviewLine, en: string): EditState =>
  ({ lineId: base.id, base, draft: { ...draftFromLine(base), en }, details: false, note: null })

beforeEach(() => {
  api.patchLine.mockReset()
  api.acceptTm.mockReset()
})

describe('writes that replace lines under an open edit', () => {
  it('save the dirty draft and close the editor before the write goes ahead', async () => {
    const a = line(1)
    const { ctl, st } = setup([a], editing(a, 'typed'))
    api.patchLine.mockImplementation(async (_d: number, id: number, p: LinePatch) => ({ ...a, id, en: p.en as string }))
    expect(await ctl.beforeWrite('all')).toBeNull()
    expect(api.patchLine).toHaveBeenCalledWith(7, 1, { en: 'typed', expected: { en: 'en 1' } })
    expect(st.current.edit).toBeNull()
  })

  it('wait, keeping the draft, when it cannot be saved', async () => {
    const a = line(1)
    const { ctl, st } = setup([a], editing(a, 'typed'))
    api.patchLine.mockRejectedValue(conflict())
    expect(await ctl.beforeWrite([1])).toBe('Save or discard your edit to #1 first.')
    expect(st.current.edit?.draft.en).toBe('typed')
  })

  it('leave an edit of another line alone', async () => {
    const [a, b] = [line(1), line(2)]
    const { ctl, st } = setup([a, b], editing(a, 'typed'))
    expect(await ctl.beforeWrite([2])).toBeNull()
    expect(api.patchLine).not.toHaveBeenCalled()
    expect(st.current.edit?.lineId).toBe(1)
  })

  it('afterwards close a clean edit opened meanwhile, but never one with typing', () => {
    const a = line(1)
    const { ctl, st } = setup([a], editing(a, 'en 1'))
    ctl.afterWrite('all')
    expect(st.current.edit).toBeNull()
    st.current.edit = editing(a, 'typed')
    ctl.afterWrite([1])
    expect(st.current.edit?.draft.en).toBe('typed')
  })
})

describe('Use this (AI suggestion) during an edit of the same line', () => {
  it('saves the draft first, then writes the suggestion against the saved text', async () => {
    const a = line(1)
    const { ctl, st } = setup([a], { ...editing(a, 'en 1'), draft: { ...draftFromLine(a), zh: 'zh typed' } })
    api.patchLine.mockImplementation(async (_d: number, id: number, p: LinePatch) => {
      const cur = st.current.shown.find((l) => l.id === id) as ReviewLine
      return { ...cur, ...(p.zh !== undefined ? { zh: p.zh } : {}), ...(p.en !== undefined ? { en: p.en } : {}) }
    })
    expect(await ctl.actions.useSuggestion(1, 'better')).toBe(true)
    expect(api.patchLine.mock.calls.map((c) => c[2])).toEqual([
      { zh: 'zh typed', expected: { zh: 'zh 1' } },
      { en: 'better', expected: { en: 'en 1' } },
    ])
    expect(st.current.edit).toBeNull()
    expect(st.current.shown[0]).toMatchObject({ zh: 'zh typed', en: 'better' })
  })

  it('writes nothing when the draft cannot be saved', async () => {
    const a = line(1)
    const { ctl, status } = setup([a], editing(a, 'typed'))
    api.patchLine.mockRejectedValue(conflict())
    expect(await ctl.actions.useSuggestion(1, 'better')).toBe(false)
    expect(api.patchLine).toHaveBeenCalledTimes(1)
    expect(status).toHaveBeenCalledWith('Save or discard your edit to #1 first.')
  })
})

describe('TM Accept on the row', () => {
  it('saves a dirty draft of that line before accepting, so no edit keeps a stale base', async () => {
    const a = line(1)
    const { ctl, st } = setup([a], { ...editing(a, 'en 1'), draft: { ...draftFromLine(a), zh: 'zh typed' } })
    api.patchLine.mockResolvedValue({ ...a, zh: 'zh typed' })
    api.acceptTm.mockResolvedValue({ ...a, zh: 'zh typed', en: 'from tm' })
    await ctl.actions.acceptTm(1, 5, 'en 1')
    await vi.waitFor(() => expect(api.acceptTm).toHaveBeenCalledWith(7, 1, 5, 'en 1'))
    expect(api.patchLine).toHaveBeenCalledBefore(api.acceptTm)
    expect(st.current.edit).toBeNull()
  })
})

describe('restoreDraft', () => {
  it('opens the kept draft, but never over an edit already open', () => {
    const [a, b] = [line(1), line(2)]
    const { ctl, st, status } = setup([a, b])
    expect(ctl.restoreDraft(a, { ...draftFromLine(a), en: 'kept' })).toBe(true)
    expect(st.current.edit).toMatchObject({ lineId: 1, base: a, draft: { en: 'kept' } })
    expect(status).toHaveBeenCalledWith('Your unsaved edit to #1 is back. Save or discard it.')
    expect(ctl.restoreDraft(b, draftFromLine(b))).toBe(false)
    expect(st.current.edit?.lineId).toBe(1)
  })
})

describe('the Records route to the open edit', () => {
  it('goes through the registered controller of that drama only', async () => {
    const a = line(1)
    const { ctl } = setup([a], editing(a, 'typed'))
    api.patchLine.mockRejectedValue(conflict())
    expect(await beforeLinesWrite(7, 'all')).toBeNull()
    const unregister = registerLinesWriter(ctl)
    expect(await beforeLinesWrite(8, 'all')).toBeNull()
    expect(await beforeLinesWrite(7, 'all')).toBe('Save or discard your edit to #1 first.')
    expect(() => afterLinesWrite(7, 'all')).not.toThrow()
    unregister()
    expect(await beforeLinesWrite(7, 'all')).toBeNull()
  })
})

describe('cancelEdit on an open edit', () => {
  it('closes a clean draft at once', () => {
    const a = line(1)
    const { ctl, st } = setup([a], editing(a, a.en))
    ctl.actions.cancelEdit()
    expect(st.current.edit).toBeNull()
  })

  it('keeps a dirty draft open with a warning, and discards on the second press', () => {
    const a = line(1)
    const { ctl, st } = setup([a], editing(a, 'typed'))
    ctl.actions.cancelEdit()
    expect(st.current.edit?.draft.en).toBe('typed')
    expect(st.current.edit?.discardWarning).toBe(true)
    ctl.actions.cancelEdit()
    expect(st.current.edit).toBeNull()
  })

  it('drops the warning when typing continues', () => {
    const a = line(1)
    const { ctl, st } = setup([a], editing(a, 'typed'))
    ctl.actions.cancelEdit()
    ctl.actions.setDraft({ en: 'typed more' })
    expect(st.current.edit?.discardWarning).toBe(false)
    ctl.actions.cancelEdit()
    expect(st.current.edit?.discardWarning).toBe(true)
  })
})

describe('saving a draft', () => {
  it('lets fields the draft left alone follow the saved line, so a second save never sends them back', async () => {
    const a = line(1, '')
    const draft = { ...draftFromLine(a), zh: 'fixed' }
    const { ctl, st } = setup([a], { lineId: 1, base: a, draft, details: false, note: null })
    // A translate job filled the English while the editor was open.
    api.patchLine.mockResolvedValue({ ...a, zh: 'fixed', en: 'from the job' })
    expect(await ctl.leaveEdit()).toBe(true)
    expect(api.patchLine).toHaveBeenCalledTimes(1)
    expect(api.patchLine).toHaveBeenCalledWith(7, 1, { zh: 'fixed', expected: { zh: 'zh 1' } })
    expect(st.current.edit).toBeNull()
  })

  it('keeps text typed while the save was in flight', async () => {
    const a = line(1)
    const { ctl, st } = setup([a], editing(a, 'first'))
    api.patchLine.mockImplementation(async () => {
      st.current.edit = { ...st.current.edit!, draft: { ...st.current.edit!.draft, en: 'first and more' } }
      return { ...a, en: 'first' }
    })
    expect(await ctl.saveEdit()).toBe(true)
    expect(st.current.edit?.draft.en).toBe('first and more')
    expect(st.current.edit?.base.en).toBe('first')
  })
})

describe('Save & next at the end of a filtered page', () => {
  it('stays on the page when the saved line leaves the filter, so the next one is not skipped', async () => {
    const a = { ...line(1), flag: 'check' }
    const { ctl, st, pending, setPage } = setup([a], editing(a, 'better'))
    Object.assign(st.current, { filter: 'flagged', page: 1, pages: 2 })
    api.patchLine.mockResolvedValue({ ...a, en: 'better', flag: null })
    ctl.actions.saveAndNext()
    await vi.waitFor(() => expect(pending.current).toEqual({ target: 'last', edit: true }))
    expect(setPage).not.toHaveBeenCalled()
  })

  it('goes to the next page when the saved line is still listed', async () => {
    const a = { ...line(1), flag: 'check' }
    const { ctl, st, pending, setPage } = setup([a], editing(a, 'better'))
    Object.assign(st.current, { filter: 'flagged', page: 1, pages: 2 })
    api.patchLine.mockResolvedValue({ ...a, en: 'better' })
    ctl.actions.saveAndNext()
    await vi.waitFor(() => expect(setPage).toHaveBeenCalledWith(2))
    expect(pending.current).toEqual({ target: 'first', edit: true })
  })
})
