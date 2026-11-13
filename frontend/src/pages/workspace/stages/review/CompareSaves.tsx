import { useEffect, useMemo, useRef, useState } from 'react'

import { getHistorySnapshot, patchLine } from '../../../../api/review'
import { listAllLines } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { isForbidden, LINES_READ_FORBIDDEN } from '../../../../components/errorMessages'
import { buttonClass } from '../../../../components/uiClasses'
import { useTitleTextLang } from '../../StageContext'
import type { HistoryItem, ReviewLine } from '../../../../types/review'
import { formatStamp } from './reviewLogic'
import { capFirst } from '../../../../labels'
import { afterLinesWrite, beforeLinesWrite } from './linesController'
import { compareSaves, isChanged, stepChanged, wordDiff, type SaveLine, type SaveRow } from './compareSavesLogic'

const CURRENT = 'current'

interface Props {
  dramaId: number
  history: HistoryItem[]
  jobRunning: boolean
  onChanged: () => void
}

function Words({ text, other, tag }: { text: string | null; other: string | null; tag: 'del' | 'ins' }) {
  if (text === null) return <span className="muted">(line not in this save)</span>
  if (other === null || text === other) return <>{text || '(blank)'}</>
  const Mark = tag
  return <>{wordDiff(text, other).map((p, i) => (p.changed ? <Mark key={i}>{p.text}</Mark> : <span key={i}>{p.text}</span>))}</>
}

// Read-only side-by-side of two saves of a title's lines (or a save and the lines
// now). Writes happen only through "Use this", one line at a time, and each is a
// compare-and-set on the English the line had when it was read.
export function CompareSaves({ dramaId, history, jobRunning, onChanged }: Props) {
  const textLang = useTitleTextLang()
  const [leftKey, setLeftKey] = useState<string>(history[0] ? String(history[0].id) : CURRENT)
  const [rightKey, setRightKey] = useState<string>(CURRENT)
  const [current, setCurrent] = useState<ReviewLine[] | null>(null)
  const [snaps, setSnaps] = useState<Record<string, SaveLine[]>>({})
  const [error, setError] = useState<unknown>(null)
  const [showAll, setShowAll] = useState(false)
  const [at, setAt] = useState(0)
  const [undo, setUndo] = useState<{ lineId: number; before: string; after: string; number: number } | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    listAllLines(dramaId).then((l) => { if (!cancelled) setCurrent(l) }, (e) => { if (!cancelled) setError(e) })
    return () => { cancelled = true }
  }, [dramaId])

  useEffect(() => {
    for (const key of [leftKey, rightKey]) {
      if (key === CURRENT || snaps[key]) continue
      getHistorySnapshot(dramaId, Number(key)).then(
        (s) => setSnaps((prev) => ({ ...prev, [key]: s.lines })),
        setError,
      )
    }
  }, [dramaId, leftKey, rightKey, snaps])

  const linesOf = (key: string): SaveLine[] | null => (key === CURRENT ? current : snaps[key] ?? null)
  const left = linesOf(leftKey)
  const right = linesOf(rightKey)
  const rows: SaveRow[] = useMemo(() => (left && right ? compareSaves(left, right) : []), [left, right])
  const changed = rows.filter(isChanged)
  const shown = showAll ? rows : changed
  const cursor = Math.min(at, Math.max(shown.length - 1, 0))
  const currentById = useMemo(() => new Map((current ?? []).map((l) => [l.id, l])), [current])

  const move = (dir: 1 | -1) => {
    if (shown.length === 0) return
    if (!showAll) setAt((cursor + dir + shown.length) % shown.length)
    else {
      const i = stepChanged(shown, cursor, dir)
      if (i >= 0) setAt(i)
    }
  }
  useEffect(() => {
    box.current?.querySelector('[data-active="true"]')?.scrollIntoView?.({ block: 'nearest' })
  }, [cursor, leftKey, rightKey])

  const onKey = (e: React.KeyboardEvent) => {
    const t = e.target as HTMLElement
    if (t.tagName === 'SELECT' || t.tagName === 'INPUT') return
    if (e.key === 'j' || e.key === 'J') { e.preventDefault(); move(1) }
    else if (e.key === 'k' || e.key === 'K') { e.preventDefault(); move(-1) }
  }

  const stepSave = (dir: 1 | -1) => {
    // history is newest first, so "older" is a higher index.
    const i = history.findIndex((h) => String(h.id) === leftKey)
    const next = history[i + dir]
    if (next) { setLeftKey(String(next.id)); setAt(0) }
  }

  const write = async (lineId: number, from: string, to: string, number: number, isUndo: boolean) => {
    setBusy(true)
    setStatus(null)
    const blocked = await beforeLinesWrite(dramaId, [lineId])
    if (blocked) { setStatus(blocked); setBusy(false); return }
    patchLine(dramaId, lineId, { en: to, expected: { en: from } })
      .then(
        () => {
          setCurrent((prev) => (prev ?? []).map((l) => (l.id === lineId ? { ...l, en: to } : l)))
          setUndo(isUndo ? null : { lineId, before: from, after: to, number })
          setStatus(isUndo ? `Put line ${number} back.` : `Line ${number} now uses that version.`)
          setError(null)
          afterLinesWrite(dramaId, [lineId])
          onChanged()
        },
        (e: unknown) => setError(e),
      )
      .finally(() => setBusy(false))
  }

  const name = (key: string) => {
    if (key === CURRENT) return 'Lines now'
    const h = history.find((x) => String(x.id) === key)
    return h ? `${capFirst(h.label ?? `Snapshot ${h.id}`)} (${formatStamp(h.created_at)})` : key
  }
  const options = [
    <option key={CURRENT} value={CURRENT}>Lines now</option>,
    ...history.map((h) => <option key={h.id} value={String(h.id)}>{name(String(h.id))}</option>),
  ]

  const side = (r: SaveRow, which: 'left' | 'right') => {
    const text = r[which]
    const cur = currentById.get(r.id)
    const usable = !jobRunning && !busy && text !== null && cur !== undefined && (cur.en ?? '') !== text
    return (
      <div className="compare-saves-side">
        <div className={which === 'left' ? 'compare-saves-text-old' : 'compare-saves-text-new'}>
          <span className="sr-only">{which === 'left' ? 'Left: ' : 'Right: '}</span>
          <Words text={text} other={r[which === 'left' ? 'right' : 'left']} tag={which === 'left' ? 'del' : 'ins'} />
        </div>
        {text !== null && (
          <button
            type="button"
            className={buttonClass('ghost', 'sm')}
            disabled={!usable}
            aria-label={`Use the ${which} version of line ${r.number}`}
            onClick={() => void write(r.id, cur?.en ?? '', text, r.number, false)}
          >
            {cur && (cur.en ?? '') === text ? 'Already in use' : 'Use this'}
          </button>
        )}
      </div>
    )
  }

  if (isForbidden(error)) return <p className="muted" role="status">{LINES_READ_FORBIDDEN}</p>
  const loading = !left || !right
  const li = history.findIndex((h) => String(h.id) === leftKey)

  return (
    <div className="compare-saves" data-testid="compare-saves" onKeyDown={onKey} ref={box}>
      <div className="review-edit-row">
        <label>
          Left{' '}
          <select value={leftKey} onChange={(e) => { setLeftKey(e.target.value); setAt(0) }} aria-label="Left save">{options}</select>
        </label>
        <label>
          Right{' '}
          <select value={rightKey} onChange={(e) => { setRightKey(e.target.value); setAt(0) }} aria-label="Right save">{options}</select>
        </label>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={li < 0 || li >= history.length - 1} onClick={() => stepSave(1)}>◀ Older left save</button>
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={li <= 0} onClick={() => stepSave(-1)}>Newer left save ▶</button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {loading ? (
        <p className="muted" role="status">Loading…</p>
      ) : leftKey === rightKey ? (
        <p className="muted" role="status">Pick two different saves.</p>
      ) : (
        <>
          <p role="status" data-testid="compare-saves-count">
            {changed.length === 0 ? 'No differences.' : `${changed.length} of ${rows.length} lines differ.`}
          </p>
          <div className="review-edit-row">
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={shown.length === 0} onClick={() => move(-1)}>Previous change (K)</button>
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={shown.length === 0} onClick={() => move(1)}>Next change (J)</button>
            <label>
              <input type="checkbox" checked={showAll} onChange={(e) => { setShowAll(e.target.checked); setAt(0) }} /> Show unchanged lines too
            </label>
            {undo && (
              <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy || jobRunning} onClick={() => void write(undo.lineId, undo.after, undo.before, undo.number, true)}>
                Undo last (line {undo.number})
              </button>
            )}
          </div>
          {status && <p role="status" data-testid="compare-saves-status">{status}</p>}
          <ul className="compare-saves-list" data-testid="compare-saves-list">
            {shown.map((r, i) => (
              <li key={r.id} data-active={i === cursor} className={i === cursor ? 'compare-saves-active' : undefined}>
                <div><span className="muted">#{r.number}</span> <span lang={textLang}>{r.zh}</span></div>
                <div className="compare-saves-pair">
                  {side(r, 'left')}
                  {side(r, 'right')}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
