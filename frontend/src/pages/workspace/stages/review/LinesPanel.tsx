import { useEffect, useMemo, useRef, useState } from 'react'

import { ApiError } from '../../../../api/client'
import type { MediaKind } from '../../../../api/media'
import { addLine, deleteLine, listAllLines, mergeLines, splitLine } from '../../../../api/restructure'
import { addNote, dismissFlag, listLines, patchLine, searchLines } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { readSectionOpen, writeSectionOpen } from '../../../../components/sectionStorage'
import { useMediaQuery } from '../../../../hooks/useMediaQuery'
import { useShortcut } from '../../../../hooks/useShortcut'
import { routeHref } from '../../../../router'
import type { RestructureResult } from '../../../../types/restructure'
import type { LineFilter, ReviewLine, ReviewLinesPage } from '../../../../types/review'
import type { NewLine } from './AddLineForm'
import { FindReplacePanel } from './FindReplacePanel'
import type { AiMode } from './LineAi'
import { LineActionsSheet, type SheetState, type SheetView } from './LineActionsSheet'
import { LineRow, type EditState, type NoteDraft, type RowActions, type RowIssue } from './LineRow'
import { Player, type PlayerHandle } from './Player'
import {
  adjacentRun,
  buildPatch,
  charCount,
  codePointOffset,
  draftFromLine,
  emptyMessage,
  initialActiveId,
  isDirty,
  lineRange,
  nextFlaggedId,
  PAGE_SIZE,
  pageCount,
  pageForPosition,
  pageStillMatches,
  stepFrom,
  structureErrorText,
  suggestionPatch,
  type LineDraft,
} from './reviewLogic'
import { Pager, ReviewToolbar } from './ReviewToolbar'
import { ShortcutSheet } from './ShortcutSheet'
import type { SplitChoice } from './SplitDialog'

interface Props {
  dramaId: number
  reloads: number
  onChanged: () => void
  jobRunning: boolean
  mediaKind: MediaKind | null
  // The drama's whole line count, whenever the "all" view reports it.
  onLineCount?: (n: number) => void
}

type Target = 'first' | 'last' | 'firstFlagged' | 'lastFlagged' | number
type Pending = { target: Target; edit?: boolean }

const PHONE = '(max-width: 640px)'
const SEARCH_DEBOUNCE_MS = 300
const STATUS_MS = 8000

function browserStorage() {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

const mismatch = () => new ApiError(409, { code: 'conflict', message: 'lines changed' })

function pick(lines: ReviewLine[], t: Target): ReviewLine | undefined {
  if (t === 'first') return lines[0]
  if (t === 'last') return lines[lines.length - 1]
  if (t === 'firstFlagged') return lines.find((l) => l.flag) ?? lines[0]
  if (t === 'lastFlagged') return [...lines].reverse().find((l) => l.flag) ?? lines[lines.length - 1]
  return lines.find((l) => l.id === t)
}

// The Review editor: one active line (roving tabIndex, arrows/J/K), a separate
// edit mode, the "⋯" line sheet with structure edits, a sticky toolbar with the
// player, and a phone action bar. Rows are stateless; every write goes through
// here so a dirty draft is saved (or kept, if the save fails) before moving on.
export function LinesPanel({ dramaId, reloads, onChanged, jobRunning, mediaKind, onLineCount }: Props) {
  const isPhone = useMediaQuery(PHONE)
  const [filter, setFilter] = useState<LineFilter>('all')
  const [page, setPage] = useState(1)
  const [input, setInput] = useState('')
  const [term, setTerm] = useState('')
  const [data, setData] = useState<ReviewLinesPage | null>(null)
  const [found, setFound] = useState<ReviewLine[] | null>(null)
  const [allTotal, setAllTotal] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [activeId, setActiveId] = useState<number | null>(null)
  const [edit, setEdit] = useState<EditState | null>(null)
  const [issue, setIssue] = useState<RowIssue | null>(null)
  const [ai, setAi] = useState<{ lineId: number; mode: AiMode } | null>(null)
  const [sheet, setSheet] = useState<SheetState | null>(null)
  const [busy, setBusy] = useState(false)
  const [structError, setStructError] = useState<unknown>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [flagCue, setFlagCue] = useState<1 | -1 | null>(null)
  const [keysOpen, setKeysOpen] = useState(false)
  const [replaceOpen, setReplaceOpen] = useState(() => readSectionOpen(browserStorage(), 'review.findreplace', false))

  const player = useRef<PlayerHandle>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)
  const pending = useRef<Pending | null>(null)
  const loadedOnce = useRef(false)
  const focusActive = useRef(false)

  const shown = useMemo(() => found ?? data?.lines ?? [], [found, data])
  const pages = data ? pageCount(data.total) : 1
  const searching = term !== ''

  useEffect(() => {
    if (allTotal !== null) onLineCount?.(allTotal)
  }, [allTotal, onLineCount])

  // Search as you type.
  useEffect(() => {
    const t = setTimeout(() => setTerm(input.trim()), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [input])

  useEffect(() => {
    if (!status) return
    const t = setTimeout(() => setStatus(null), STATUS_MS)
    return () => clearTimeout(t)
  }, [status])

  useEffect(() => {
    let cancelled = false
    const settle = (lines: ReviewLine[]) => {
      const p = pending.current
      pending.current = null
      const target = p ? pick(lines, p.target) : undefined
      if (target) {
        setActiveId(target.id)
        focusActive.current = true
        if (p?.edit) setEdit({ lineId: target.id, draft: draftFromLine(target), details: false, note: null })
      } else {
        const first = !loadedOnce.current
        setActiveId((cur) =>
          cur !== null && lines.some((l) => l.id === cur)
            ? cur
            : first
              ? initialActiveId(lines)
              : (lines[0]?.id ?? null),
        )
      }
      loadedOnce.current = true
    }
    const req = term
      ? searchLines(dramaId, term).then((r) => {
          if (cancelled) return
          setFound(r)
          settle(r)
        })
      : listLines(dramaId, page, PAGE_SIZE, filter).then((r) => {
          if (cancelled) return
          setFound(null)
          setData(r)
          if (filter === 'all') setAllTotal(r.total)
          settle(r.lines)
        })
    req.then(
      () => !cancelled && setError(null),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, page, filter, term, reloads])

  // Keyboard moves bring the active row into view and give it focus.
  useEffect(() => {
    if (!focusActive.current || activeId === null) return
    focusActive.current = false
    const el = listRef.current?.querySelector<HTMLElement>(`[data-line-id="${activeId}"]`)
    if (!el) return
    el.focus({ preventScroll: true })
    el.scrollIntoView?.({ block: 'nearest' })
  })

  // Everything the stable row callbacks need, read at call time.
  const st = useRef({ shown, edit, page, pages, filter, searching, onChanged, jobRunning, activeId })
  useEffect(() => {
    st.current = { shown, edit, page, pages, filter, searching, onChanged, jobRunning, activeId }
  })

  const ctl = useMemo(() => {
    const find = (id: number | null) => st.current.shown.find((l) => l.id === id) ?? null
    const replaceLine = (saved: ReviewLine) => {
      setData((d) => (d ? { ...d, lines: d.lines.map((l) => (l.id === saved.id ? saved : l)) } : d))
      setFound((f) => (f ? f.map((l) => (l.id === saved.id ? saved : l)) : f))
      st.current.shown = st.current.shown.map((l) => (l.id === saved.id ? saved : l))
    }
    const failLine = (lineId: number, e: unknown) => {
      if (e instanceof ApiError && e.status === 409) setIssue({ lineId, conflict: true })
      else setIssue({ lineId, error: e })
    }
    const setEditNow = (e: EditState | null) => {
      st.current.edit = e
      setEdit(e)
    }

    // Save the open draft; true when nothing is left unsaved. One save at a
    // time: a second request (a double Ctrl+S) waits, then re-checks the draft
    // against the saved line instead of sending a stale compare-and-set.
    const saving: { current: Promise<boolean> | null } = { current: null }
    const saveEdit = (): Promise<boolean> => {
      if (saving.current) return saving.current.then(() => saveEdit())
      const run = saveDraft().finally(() => {
        saving.current = null
      })
      saving.current = run
      return run
    }
    const saveDraft = async (): Promise<boolean> => {
      const cur = st.current.edit
      if (!cur) return true
      const line = find(cur.lineId)
      if (!line) return false
      const patch = buildPatch(line, cur.draft)
      if (typeof patch === 'string') {
        setIssue({ lineId: cur.lineId, problem: patch })
        setEditNow({ ...cur, details: true })
        return false
      }
      if (patch === null) return true
      try {
        const saved = await patchLine(dramaId, line.id, patch)
        replaceLine(saved)
        setIssue(null)
        st.current.onChanged()
        return true
      } catch (e) {
        failLine(cur.lineId, e)
        return false
      }
    }

    // Close the editor, saving a dirty draft first; false keeps it open.
    const leaveEdit = async (): Promise<boolean> => {
      const cur = st.current.edit
      if (!cur) return true
      const line = find(cur.lineId)
      if (line && isDirty(line, cur.draft) && !(await saveEdit())) return false
      setEditNow(null)
      return true
    }

    const focusTo = (id: number) => {
      focusActive.current = true
      setActiveId(id)
    }

    const activate = async (id: number, focus = false) => {
      const cur = st.current.edit
      if (cur && cur.lineId !== id && !(await leaveEdit())) return
      if (focus) focusTo(id)
      else setActiveId(id)
    }

    const openEdit = async (id: number, details = false): Promise<boolean> => {
      const cur = st.current.edit
      if (cur && cur.lineId === id) {
        if (details && !cur.details) setEditNow({ ...cur, details: true })
        setActiveId(id)
        return true
      }
      if (!(await leaveEdit())) return false
      const line = find(id)
      if (!line) return false
      setActiveId(id)
      setIssue(null)
      setEditNow({ lineId: id, draft: draftFromLine(line), details, note: null })
      return true
    }

    const goPage = async (p: number, target: Target) => {
      if (!(await leaveEdit())) return
      pending.current = { target }
      setFlagCue(null)
      setPage(p)
    }

    const move = (delta: 1 | -1) => {
      const { shown: lines, activeId: cur, page: pg, pages: n, searching: s } = st.current
      const step = stepFrom(lines, cur, delta, { next: !s && pg < n, prev: !s && pg > 1 })
      if (!step) return
      if ('id' in step) void activate(step.id, true)
      else void goPage(pg + delta, delta > 0 ? 'first' : 'last')
    }

    const moveFlagged = (delta: 1 | -1) => {
      const { shown: lines, page: pg, pages: n, searching: s } = st.current
      const row = (document.activeElement as HTMLElement | null)?.closest?.('[data-line-id]')
      const from = row ? lines.findIndex((l) => String(l.id) === row.getAttribute('data-line-id')) : -1
      const id = nextFlaggedId(lines, from, delta)
      if (id !== null) {
        setFlagCue(null)
        void activate(id, true)
      } else if (!s && (delta > 0 ? pg < n : pg > 1)) setFlagCue(delta)
      else setStatus('No more flagged lines.')
    }

    const saveAndNext = async () => {
      const cur = st.current.edit
      if (!cur || !(await saveEdit())) return
      const { shown: lines, page: pg, pages: n, searching: s } = st.current
      const i = lines.findIndex((l) => l.id === cur.lineId)
      const next = lines[i + 1]
      if (next) {
        setActiveId(next.id)
        setIssue(null)
        setEditNow({ lineId: next.id, draft: draftFromLine(next), details: false, note: null })
      } else if (!s && pg < n) {
        setEditNow(null)
        pending.current = { target: 'first', edit: true }
        setPage(pg + 1)
      } else {
        setEditNow(null)
        focusTo(cur.lineId)
        setStatus('Saved. That was the last line.')
      }
    }

    const openSheet = async (id: number | null, view: SheetView = 'menu', extra: Partial<SheetState> = {}) => {
      if (id !== null) {
        const cur = st.current.edit
        // Structure edits work on saved text: save (or keep) the draft first.
        if (cur && view !== 'menu' && !(await leaveEdit())) return
        if (cur && cur.lineId !== id && !(await leaveEdit())) return
        setActiveId(id)
      }
      setStructError(null)
      setSheet({ lineId: id, view, ...extra })
    }

    const actions: RowActions = {
      activate: (id) => void activate(id),
      openEdit: (id, details) => void openEdit(id, details),
      setDraft: (patch: Partial<LineDraft>) => {
        const cur = st.current.edit
        if (cur) setEditNow({ ...cur, draft: { ...cur.draft, ...patch } })
      },
      cancelEdit: () => {
        const cur = st.current.edit
        setEditNow(null)
        setIssue((i) => (i?.problem ? null : i))
        if (cur) focusTo(cur.lineId)
      },
      save: () => void saveEdit(),
      saveAndNext: () => void saveAndNext(),
      toggleDetails: (id) => {
        const cur = st.current.edit
        if (cur && cur.lineId === id && cur.details) {
          const line = find(id)
          if (line && isDirty(line, cur.draft)) setEditNow({ ...cur, details: false, note: null })
          else setEditNow(null)
        } else void openEdit(id, true)
      },
      setNote: (note: NoteDraft | null) => {
        const cur = st.current.edit
        if (cur) setEditNow({ ...cur, note })
      },
      saveNote: () => {
        const cur = st.current.edit
        if (!cur?.note) return
        const { term: t, type, text } = cur.note
        addNote(dramaId, { line_id: cur.lineId, term: t, note_type: type, note: text }).then(() => {
          const now = st.current.edit
          if (now) setEditNow({ ...now, note: null })
          setIssue(null)
          setStatus('Note saved.')
          st.current.onChanged()
        }, (e) => failLine(cur.lineId, e))
      },
      openSheet: (id) => void openSheet(id),
      openStructure: (id, view) => void openSheet(id, view),
      splitAtCursor: (id, field, offset) => {
        const cur = st.current.edit
        const line = find(id)
        if (!line) return
        const zh = cur?.lineId === id ? cur.draft.zh : line.zh
        const en = cur?.lineId === id ? cur.draft.en : line.en
        let at: number
        let enAt: number | null = null
        if (field === 'zh') at = codePointOffset(zh, offset)
        else {
          enAt = codePointOffset(en, offset)
          const enLen = charCount(en)
          at = enLen > 0 ? Math.round((charCount(zh) * enAt) / enLen) : Math.round(charCount(zh) / 2)
          if (enAt <= 0 || enAt >= enLen) enAt = null
        }
        void openSheet(id, 'split', { splitAt: at, splitEnAt: enAt })
      },
      setAi: (id, mode) => {
        setActiveId(id)
        setAi(mode ? { lineId: id, mode } : null)
      },
      useSuggestion: async (id, text) => {
        const line = find(id)
        if (!line) return false
        const patch = suggestionPatch(line, text)
        if (!patch) return true
        try {
          const saved = await patchLine(dramaId, id, patch)
          replaceLine(saved)
          setIssue(null)
          st.current.onChanged()
          return true
        } catch (e) {
          failLine(id, e)
          return false
        }
      },
      dismissFlag: (id) => {
        dismissFlag(dramaId, id).then((saved) => {
          replaceLine(saved)
          setIssue(null)
          st.current.onChanged()
        }, (e) => failLine(id, e))
      },
      playLine: (line) => player.current?.playLine(line),
      clearIssue: () => setIssue(null),
      reload: () => {
        setEditNow(null)
        setIssue(null)
        st.current.onChanged()
      },
    }

    return { actions, move, moveFlagged, goPage, leaveEdit, openEdit, openSheet, focusTo }
  }, [dramaId])

  const { actions } = ctl

  // ---- structure edits (sheet) ----
  const runStructure = async (
    call: (ids: number[]) => Promise<RestructureResult>,
    after: (r: RestructureResult, ids: number[]) => { id: number | null; message: string },
  ) => {
    if (busy) return
    setBusy(true)
    setStructError(null)
    try {
      const ids = (await listAllLines(dramaId)).map((l) => l.id)
      if (!pageStillMatches(ids, shown.map((l) => l.id), searching ? 'search' : filter, page)) throw mismatch()
      const r = await call(ids)
      const { id, message } = after(r, ids)
      setSheet(null)
      setEdit(null)
      setAi(null)
      if (id !== null) {
        pending.current = { target: id }
        if (!searching && filter === 'all') {
          const pos = r.line_ids.indexOf(id)
          if (pos !== -1) setPage(pageForPosition(pos))
        }
      }
      setStatus(message)
      onChanged()
    } catch (e) {
      setStructError(e)
    } finally {
      setBusy(false)
    }
  }

  const UNDO = ' Undo in Records → Line history.'
  const sheetLine = sheet ? (shown.find((l) => l.id === sheet.lineId) ?? null) : null
  const sheetRun = sheetLine ? shown.slice(shown.findIndex((l) => l.id === sheetLine.id)) : []

  const doSplit = (c: SplitChoice) => {
    const line = sheetLine
    if (!line) return
    void runStructure(
      (ids) => splitLine(dramaId, line.id, { expected_line_ids: ids, at_char: c.at_char, expected_zh: line.zh, at_time: c.at_time, en_at_char: c.en_at_char }),
      (r) => {
        const [a, b] = r.lines
        return { id: b?.id ?? a?.id ?? null, message: a && b ? `Split #${a.idx} into #${a.idx}–#${b.idx}.${UNDO}` : `Line split.${UNDO}` }
      },
    )
  }
  const doMerge = (lineIds: number[]) => {
    const chosen = shown.filter((l) => lineIds.includes(l.id))
    void runStructure(
      (ids) => {
        const run = adjacentRun(ids, lineIds[0], lineIds.length)
        if (!run || run.some((id, i) => id !== lineIds[i])) throw mismatch()
        return mergeLines(dramaId, lineIds, ids)
      },
      (r) => {
        const head = r.lines[0]
        return { id: head?.id ?? lineIds[0], message: `Merged ${lineRange(chosen)}${head ? ` into #${head.idx}` : ''}.${UNDO}` }
      },
    )
  }
  const doAdd = (nl: NewLine) => {
    const after = sheetLine
    void runStructure(
      (ids) => addLine(dramaId, { expected_line_ids: ids, after_line_id: after?.id ?? null, ...nl }),
      (r) => ({ id: r.lines[0]?.id ?? null, message: r.lines[0] ? `Added line #${r.lines[0].idx}.` : 'Line added.' }),
    )
  }
  const doDelete = () => {
    const line = sheetLine
    if (!line) return
    void runStructure(
      (ids) => deleteLine(dramaId, line.id, ids),
      (r, before) => {
        const pos = before.indexOf(line.id)
        const id = r.line_ids[pos] ?? r.line_ids[pos - 1] ?? null
        return { id, message: `Deleted #${line.idx}.${UNDO}` }
      },
    )
  }
  const closeSheetThen = (fn: () => void) => {
    setSheet(null)
    fn()
  }

  // ---- toolbar ----
  const changeFilter = async (f: LineFilter) => {
    if (!(await ctl.leaveEdit())) return
    setFlagCue(null)
    setFilter(f)
    setPage(1)
    setInput('')
    setTerm('')
  }
  const changeSearch = async (v: string) => {
    if (st.current.edit && !(await ctl.leaveEdit())) return
    setFlagCue(null)
    setInput(v)
    if (v === '') setTerm('')
  }
  const goToNumber = async (n: number) => {
    try {
      const all = await listAllLines(dramaId)
      const pos = all.findIndex((l) => l.idx === n)
      if (pos === -1) {
        setStatus(`No line #${n}.`)
        return
      }
      if (!(await ctl.leaveEdit())) return
      const id = all[pos].id
      const pg = pageForPosition(pos)
      if (!searching && filter === 'all' && page === pg) ctl.focusTo(id)
      else {
        pending.current = { target: id }
        setFilter('all')
        setInput('')
        setTerm('')
        setPage(pg)
      }
    } catch (e) {
      setError(e)
    }
  }
  const toggleReplace = () => {
    const next = !replaceOpen
    setReplaceOpen(next)
    writeSectionOpen(browserStorage(), 'review.findreplace', next)
  }

  // ---- shortcuts ----
  const active = activeId !== null ? shown.find((l) => l.id === activeId) ?? null : null
  useShortcut((combo, { inText, event }) => {
    if (sheet || keysOpen) return false
    if (combo === 'alt+ ') {
      if (!mediaKind) return false
      player.current?.togglePlay()
      return true
    }
    if (inText) return false
    const target = event.target as HTMLElement
    const inList = target === document.body || !!listRef.current?.contains(target)
    const onRow = target === document.body || target.matches?.('.review-line')
    const isControl = !!target.closest?.('input, select')
    switch (combo) {
      case 'arrowdown':
      case 'arrowup':
        if (!inList || isControl) return false
        ctl.move(combo === 'arrowdown' ? 1 : -1)
        return true
      case 'j':
      case 'k':
        ctl.move(combo === 'j' ? 1 : -1)
        return true
      case 'alt+arrowdown':
      case 'alt+arrowup':
        ctl.moveFlagged(combo === 'alt+arrowdown' ? 1 : -1)
        return true
      case ']':
      case '[': {
        const p = page + (combo === ']' ? 1 : -1)
        if (searching || p < 1 || p > pages) return false
        void ctl.goPage(p, 'first')
        return true
      }
      case '/':
        if (isPhone && !searchRef.current) return false
        searchRef.current?.focus()
        return true
      case '?':
        setKeysOpen(true)
        return true
    }
    if (!active) return false
    switch (combo) {
      case 'enter':
        if (!onRow) return false
        void ctl.openEdit(active.id)
        return true
      case 'e':
        void ctl.openEdit(active.id)
        return true
      case 'd':
        actions.toggleDetails(active.id)
        return true
      case ' ':
        if (!mediaKind || !onRow) return false
        player.current?.toggleLine(active)
        return true
      case 'l':
        if (!mediaKind) return false
        player.current?.toggleLoop()
        return true
      case 'm':
        void ctl.openSheet(active.id, 'merge')
        return true
      case 'a':
        void ctl.openSheet(active.id, 'add')
        return true
      case 'shift+delete':
        void ctl.openSheet(active.id, 'menu', { armDelete: true })
        return true
      case 'f':
        if (!active.flag) return false
        actions.dismissFlag(active.id)
        return true
      case 'i':
        if (!active.en) return false
        actions.setAi(active.id, 'improve')
        return true
      case 'w':
        actions.setAi(active.id, 'explain')
        return true
      case 'escape':
        if (!ai) return false
        setAi(null)
        return true
    }
    return false
  })

  const counts = data
    ? { all: allTotal, flagged: data.flagged_count, untranslated: data.untranslated_count }
    : null
  const loading = !searching && data === null && !error
  const showPager = !searching && !!data && pages > 1
  const editingActive = edit !== null && edit.lineId === activeId
  // Phones: the pager shares the player's row so the sticky toolbar stays short.
  const pagerInPlayer = isPhone && mediaKind !== null && showPager

  return (
    <section className="review-editor" aria-label="Lines">
      <ReviewToolbar
        isPhone={isPhone}
        filter={filter}
        counts={counts}
        onFilter={(f) => void changeFilter(f)}
        search={input}
        searchRef={searchRef}
        onSearch={(v) => void changeSearch(v)}
        resultCount={searching && found ? found.length : null}
        page={page}
        pages={pages}
        showPager={showPager && !pagerInPlayer}
        onPage={(p) => void ctl.goPage(p, 'first')}
        onGoTo={(n) => void goToNumber(n)}
        replaceOpen={replaceOpen}
        onToggleReplace={toggleReplace}
        onKeys={() => setKeysOpen(true)}
        player={
          mediaKind ? (
            <Player
              ref={player}
              dramaId={dramaId}
              kind={mediaKind}
              trailing={pagerInPlayer ? <Pager page={page} pages={pages} onPage={(p) => void ctl.goPage(p, 'first')} labelled /> : null}
            />
          ) : null
        }
      />
      {data && (
        <p className="sr-only" data-testid="line-counts">
          {data.total} in this view · {data.flagged_count} flagged · {data.untranslated_count} untranslated
        </p>
      )}
      {replaceOpen && <FindReplacePanel dramaId={dramaId} onChanged={onChanged} onClose={toggleReplace} />}
      <p className="review-status" role="status">
        {status}
        {flagCue && (
          <>
            No more flagged lines on this page.{' '}
            <button
              type="button"
              className="link"
              onClick={() => void ctl.goPage(page + flagCue, flagCue > 0 ? 'firstFlagged' : 'lastFlagged')}
            >
              {flagCue > 0 ? 'Next page ›' : '‹ Previous page'}
            </button>
          </>
        )}
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {loading && (
        <ul className="review-lines" aria-hidden="true">
          {Array.from({ length: 6 }, (_, i) => (
            <li key={i} className="review-line review-skeleton" />
          ))}
        </ul>
      )}
      {!loading && shown.length === 0 && !error && (
        <div className="review-empty">
          <p className="muted">{emptyMessage(filter, term)}</p>
          {filter === 'all' && !searching ? (
            <div className="actions">
              <a href={routeHref({ name: 'drama', id: dramaId, stage: 'source' })}>Go to Source</a>
              <button type="button" disabled={jobRunning} onClick={() => void ctl.openSheet(null, 'add')}>
                Add first line
              </button>
            </div>
          ) : (
            <button type="button" className="link" onClick={() => void changeFilter('all')}>
              All lines
            </button>
          )}
        </div>
      )}
      <ul className="review-lines" ref={listRef}>
        {shown.map((l) => (
          <LineRow
            key={l.id}
            dramaId={dramaId}
            line={l}
            active={l.id === activeId}
            isPhone={isPhone}
            hasMedia={mediaKind !== null}
            jobRunning={jobRunning}
            edit={edit?.lineId === l.id ? edit : null}
            ai={ai?.lineId === l.id ? ai.mode : null}
            issue={issue?.lineId === l.id ? issue : null}
            actions={actions}
          />
        ))}
      </ul>
      {showPager && (
        <div className="review-bottom-pager">
          <Pager page={page} pages={pages} onPage={(p) => void ctl.goPage(p, 'first')} />
        </div>
      )}

      {isPhone && active && (
        <div className="review-editbar" role="toolbar" aria-label="Line actions">
          {editingActive ? (
            <>
              <button type="button" onClick={actions.cancelEdit}>Cancel</button>
              <button type="button" onClick={actions.save}>Save</button>
              <button type="button" className="primary" onClick={actions.saveAndNext}>Save &amp; next</button>
            </>
          ) : (
            <>
              {mediaKind && (
                <button type="button" onClick={() => player.current?.toggleLine(active)}>▶ #{active.idx}</button>
              )}
              <button type="button" aria-label="Previous line" onClick={() => ctl.move(-1)}>‹ Prev</button>
              <button type="button" aria-label="Next line" onClick={() => ctl.move(1)}>Next ›</button>
              <button type="button" onClick={() => void ctl.openEdit(active.id)}>Edit</button>
            </>
          )}
        </div>
      )}

      <LineActionsSheet
        state={sheet}
        line={sheetLine}
        run={sheetRun}
        hasMedia={mediaKind !== null}
        jobRunning={jobRunning}
        busy={busy}
        error={structError}
        errorText={structureErrorText(structError)}
        onView={(view) => {
          setStructError(null)
          setSheet((s) => (s ? { ...s, view, armDelete: false } : s))
        }}
        onClose={() => setSheet(null)}
        onPlay={() => closeSheetThen(() => sheetLine && player.current?.playLine(sheetLine))}
        onEditDetails={() => closeSheetThen(() => sheetLine && void ctl.openEdit(sheetLine.id, true))}
        onImprove={() => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, 'improve'))}
        onWhy={() => closeSheetThen(() => sheetLine && actions.setAi(sheetLine.id, 'explain'))}
        onDismissFlag={() => closeSheetThen(() => sheetLine && actions.dismissFlag(sheetLine.id))}
        onAddNote={() =>
          closeSheetThen(() => {
            if (!sheetLine) return
            const id = sheetLine.id
            void ctl.openEdit(id, true).then((ok) => ok && actions.setNote({ term: '', type: 'translation', text: '' }))
          })
        }
        onSplit={doSplit}
        onMerge={doMerge}
        onAdd={doAdd}
        onDelete={doDelete}
      />
      <ShortcutSheet open={keysOpen} onClose={() => setKeysOpen(false)} />
    </section>
  )
}
