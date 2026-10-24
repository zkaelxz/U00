import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../../../../api/client'
import type { ReviewLine, ReviewMatch } from '../../../../types/review'
import { applyAfterEdit } from './FindReplacePanel'
import { HiddenEditBanner } from './HiddenEditBanner'
import { isComposingKey, type EditState } from './LineRow'
import { createLinesController, registerLinesWriter, type LinesController, type LinesState } from './linesController'
import { buildLinesNavigation } from './linesNavigation'
import { draftFromLine } from './reviewDraft'
import { openPendingEdit, rebaseAfterReload } from './useLinesLoad'

vi.mock('../../../../api/review', () => ({
  patchLine: vi.fn(), acceptTm: vi.fn(), addNote: vi.fn(), dismissFlag: vi.fn(), flaggedAdjacent: vi.fn(),
  applyFindReplace: vi.fn(), previewFindReplace: vi.fn(), listLines: vi.fn(), searchLines: vi.fn(),
}))
vi.mock('../../../../api/timingCheck', () => ({ snapToSpeech: vi.fn() }))
vi.mock('../../../../api/restructure', () => ({ listAllLines: vi.fn() }))

const line = (id: number, over: Partial<ReviewLine> = {}): ReviewLine => ({
  id, idx: id - 1, start: id, end: id + 0.5, zh: `zh ${id}`, en: `en ${id}`, speaker: null, speaker_manual: false,
  sfx: false, flag: null, flag_note: null, dub_filename: null, lang: null, ...over,
})
const editing = (base: ReviewLine, en: string): EditState =>
  ({ lineId: base.id, base, draft: { ...draftFromLine(base), en }, details: false, note: null })

function controller(edit: EditState | null) {
  const st = {
    current: {
      shown: [], edit, page: 1, pages: 1, filter: 'all', searching: false,
      onChanged: vi.fn(), jobRunning: false, activeId: null,
    } as LinesState,
  }
  const ctl = createLinesController({
    dramaId: 7, st, pending: { current: null }, focusActive: { current: null }, player: { current: null },
    selectRef: { current: vi.fn() }, showOnPageRef: { current: vi.fn() },
    setData: vi.fn(), setFound: vi.fn(), setIssue: vi.fn(), setEdit: vi.fn(), setStatus: vi.fn(),
    setActiveId: vi.fn(), setPage: vi.fn(), setFilter: vi.fn(), setError: vi.fn(), setSheet: vi.fn(),
    setStructError: vi.fn(), setAi: vi.fn(), setRetranscribeFocusId: vi.fn(),
  })
  return { ctl, st }
}

describe('Reload after a conflict', () => {
  it('keeps the typing and moves it onto the line as reloaded', () => {
    const old = line(1)
    const { ctl, st } = controller(editing(old, 'typed'))
    ctl.actions.reload()
    expect(st.current.edit?.draft.en).toBe('typed')
    expect(st.current.onChanged).toHaveBeenCalled()
    const fresh = line(1, { zh: 'zh changed elsewhere', en: 'en changed elsewhere' })
    const after = rebaseAfterReload([fresh])(st.current.edit)
    expect(after?.base).toBe(fresh)
    expect(after?.draft.en).toBe('typed')
    expect(after?.draft.zh).toBe('zh changed elsewhere')
    expect(after?.rebaseOnLoad).toBe(false)
  })

  it('still closes an edit with nothing typed', () => {
    const a = line(1)
    const { ctl, st } = controller(editing(a, a.en))
    ctl.actions.reload()
    expect(st.current.edit).toBeNull()
  })

  it('leaves an ordinary edit alone on a load', () => {
    const e = editing(line(1), 'typed')
    expect(rebaseAfterReload([line(1, { en: 'other' })])(e)).toBe(e)
  })
})

describe('Save & next onto a page that is still loading', () => {
  it('does not replace an edit the user opened meanwhile', () => {
    const mine = editing(line(3), 'typing here')
    expect(openPendingEdit(line(41))(mine)).toBe(mine)
    expect(openPendingEdit(line(41))(null)?.lineId).toBe(41)
  })
})

describe('Find and replace Apply', () => {
  const matches = [{ id: 1, old_text: 'en 1', new_text: 'x' }] as ReviewMatch[]

  it('saves an open edit of a matched line first, and applies nothing when it cannot', async () => {
    const fake = { dramaId: 9, beforeWrite: vi.fn(async () => 'Save or discard your edit to #1 first.'), afterWrite: vi.fn() }
    const unregister = registerLinesWriter(fake as unknown as LinesController)
    const apply = vi.fn()
    expect(await applyAfterEdit(9, matches, apply)).toBe('Save or discard your edit to #1 first.')
    expect(fake.beforeWrite).toHaveBeenCalledWith([1])
    expect(apply).not.toHaveBeenCalled()
    unregister()
  })

  it('applies, then lets the editor close a clean edit left on the old text', async () => {
    const fake = { dramaId: 9, beforeWrite: vi.fn(async () => null), afterWrite: vi.fn() }
    const unregister = registerLinesWriter(fake as unknown as LinesController)
    const result = { applied: 1, stale: 0, applied_ids: [1], stale_ids: [] }
    expect(await applyAfterEdit(9, matches, vi.fn(async () => result))).toBe(result)
    expect(fake.afterWrite).toHaveBeenCalledWith([1])
    unregister()
  })
})

describe('the outside-this-view edit banner', () => {
  const e = editing(line(5), 'typed')
  const render = (issue: Parameters<typeof HiddenEditBanner>[0]['issue']) => renderToStaticMarkup(
    <HiddenEditBanner edit={e} issue={issue} onSave={() => {}} onShow={() => {}} onDiscard={() => {}} />)

  it('says why the save failed', () => {
    expect(render({ lineId: 5, problem: 'Enter a number for start.' })).toContain('Enter a number for start.')
    const html = render({ lineId: 5, error: new ApiError(404, { code: 'not_found', message: 'No line with id 5 in this title.' }) })
    expect(html).toContain('error-banner')
  })

  it("ignores another line's problem", () => {
    expect(render({ lineId: 6, problem: 'Enter a number for start.' })).not.toContain('Enter a number')
  })
})

describe('Enter that confirms an IME word', () => {
  it('is recognised on Safari (keyCode 229) as well as by isComposing', () => {
    expect(isComposingKey({ nativeEvent: { isComposing: false }, keyCode: 229 })).toBe(true)
    expect(isComposingKey({ nativeEvent: { isComposing: true }, keyCode: 13 })).toBe(true)
    expect(isComposingKey({ nativeEvent: { isComposing: false }, keyCode: 13 })).toBe(false)
  })
})

describe('typing in Search with an unsaved draft', () => {
  it('takes every keystroke at once, while the draft is still saving', () => {
    const setInput = vi.fn()
    const ctl = { leaveEdit: vi.fn(() => new Promise<boolean>(() => {})) }
    const nav = buildLinesNavigation({
      dramaId: 7, ctl: ctl as never, st: { current: { edit: editing(line(1), 'typed') } as LinesState },
      pending: { current: null }, listRef: { current: null }, searching: false, filter: 'all', page: 1,
      setFilter: vi.fn(), setPage: vi.fn(), setInput, setTerm: vi.fn(), setStatus: vi.fn(), setError: vi.fn(),
    })
    void nav.changeSearch('a')
    void nav.changeSearch('ab')
    expect(setInput.mock.calls).toEqual([['a'], ['ab']])
  })
})
