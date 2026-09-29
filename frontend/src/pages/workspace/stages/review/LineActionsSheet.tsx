import { useEffect, useRef, useState } from 'react'

import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Sheet } from '../../../../components/Sheet'
import type { ReviewLine } from '../../../../types/review'
import { AddLineForm, type NewLine } from './AddLineForm'
import { MergeConfirm } from './MergeConfirm'
import { JOB_RUNNING_MESSAGE } from './reviewLogic'
import { SplitDialog, type SplitChoice } from './SplitDialog'
import { lineNumber } from '../../../../lineNumber'

export type SheetView = 'menu' | 'split' | 'merge' | 'add'

export interface SheetState {
  lineId: number | null
  view: SheetView
  // Shift+Delete opens the menu with delete already armed.
  armDelete?: boolean
  splitAt?: number
  splitEnAt?: number | null
}

interface Props {
  state: SheetState | null
  // null for "Add first line" on an empty drama.
  line: ReviewLine | null
  // The line and the ones after it on this page (merge candidates).
  run: ReviewLine[]
  hasMedia: boolean
  jobRunning: boolean
  // Filtered or search view: merge and add need the true neighbour.
  limited: boolean
  busy: boolean
  error: unknown
  errorText: string | null
  onView: (view: SheetView) => void
  onClose: () => void
  onPlay: () => void
  onEditDetails: () => void
  onImprove: () => void
  onWhy: () => void
  onDismissFlag: () => void
  onAddNote: () => void
  onSplit: (choice: SplitChoice) => void
  onMerge: (ids: number[]) => void
  onAdd: (line: NewLine) => void
  onDelete: () => void
}

const DELETE_TIMEOUT_MS = 5000

// The "⋯" sheet for one line: every line action in 48px rows, and the split,
// merge and add forms. Deleting is two-step: the button turns into
// "Confirm delete #n" for five seconds.
// Two-step delete: "Delete line…" turns into "Confirm delete #n" for five
// seconds. Keyed by the sheet's line/view, so it starts fresh each time.
function DeleteButton({ idx, initialArmed, busy, blocked, onDelete }: {
  idx: number
  initialArmed: boolean
  busy: boolean
  blocked: boolean
  onDelete: () => void
}) {
  const [armed, setArmed] = useState(initialArmed)
  const confirmRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!armed) return
    confirmRef.current?.focus()
    const t = setTimeout(() => setArmed(false), DELETE_TIMEOUT_MS)
    return () => clearTimeout(t)
  }, [armed])
  return armed ? (
    <button ref={confirmRef} type="button" className="danger" disabled={busy || blocked} onClick={onDelete}>
      {busy ? 'Deleting…' : `Confirm delete #${idx}`}
    </button>
  ) : (
    <button type="button" className="danger" disabled={blocked} onClick={() => setArmed(true)}>
      Delete line…
    </button>
  )
}

export function LineActionsSheet(p: Props) {
  const { state, line } = p
  const key = state ? `${state.lineId}-${state.view}-${state.armDelete ? 1 : 0}` : ''
  const blocked = p.jobRunning ? JOB_RUNNING_MESSAGE : null
  const view = state?.view ?? 'menu'
  const title = !line
    ? 'Add first line'
    : view === 'split'
      ? `Split line #${lineNumber(line.idx)}`
      : view === 'merge'
        ? `Merge #${lineNumber(line.idx)} with next`
        : view === 'add'
          ? `Add a line after #${lineNumber(line.idx)}`
          : `Line #${lineNumber(line.idx)}`
  const next = line ? p.run[1] ?? null : null
  const back = () => p.onView('menu')

  return (
    <Sheet open={!!state} title={title} onClose={p.onClose}>
      {state && (
        <>
          {p.errorText ? (
            <p className="error" role="alert" data-testid="structure-error">{p.errorText}</p>
          ) : (
            <ErrorBanner error={p.error} />
          )}
          {(!line || view === 'add') && (
            <AddLineForm
              key={line?.id ?? 'first'}
              after={line}
              next={next}
              busy={p.busy}
              blocked={blocked}
              onAdd={p.onAdd}
              onCancel={line ? back : undefined}
            />
          )}
          {line && view === 'split' && (
            <SplitDialog
              key={`${line.id}-${state.splitAt ?? ''}`}
              line={line}
              initialAt={state.splitAt}
              initialEnAt={state.splitEnAt}
              busy={p.busy}
              blocked={blocked}
              onSplit={p.onSplit}
              onCancel={back}
            />
          )}
          {line && view === 'merge' && (
            <MergeConfirm key={line.id} run={p.run} busy={p.busy} blocked={blocked} onMerge={p.onMerge} onCancel={back} />
          )}
          {line && view === 'menu' && line.flag && (
            <p className="review-flag-full" data-testid="sheet-flag">
              <span aria-hidden="true">⚑ </span>Flagged: {line.flag}
              {line.flag_note ? ` · ${line.flag_note}` : ''}
            </p>
          )}
          {line && view === 'menu' && (
            <ul className="sheet-menu">
              {p.hasMedia && (
                <li><button type="button" onClick={p.onPlay}>▶ Play line</button></li>
              )}
              <li><button type="button" onClick={p.onEditDetails}>Edit details</button></li>
              <li>
                <button type="button" disabled={!line.en} onClick={p.onImprove}>
                  Improve translation (AI)
                  {!line.en && <span className="sheet-reason">Needs a translation first.</span>}
                </button>
              </li>
              <li><button type="button" onClick={p.onWhy}>Why this? (AI)</button></li>
              <li>
                <button type="button" disabled={!!blocked} onClick={() => p.onView('split')}>
                  Split line…
                  {blocked && <span className="sheet-reason">{blocked}</span>}
                </button>
              </li>
              <li>
                <button type="button" disabled={!!blocked || p.limited} onClick={() => p.onView('merge')}>
                  Merge with next…
                  {p.limited && !blocked && <span className="sheet-reason">Only in the All lines view.</span>}
                </button>
              </li>
              <li>
                <button type="button" disabled={!!blocked || p.limited} onClick={() => p.onView('add')}>
                  Add line after…
                  {p.limited && !blocked && <span className="sheet-reason">Only in the All lines view.</span>}
                </button>
              </li>
              <li>
                <DeleteButton
                  key={key}
                  idx={lineNumber(line.idx)}
                  initialArmed={!!state.armDelete}
                  busy={p.busy}
                  blocked={!!blocked}
                  onDelete={p.onDelete}
                />
              </li>
              {line.flag && (
                <li><button type="button" onClick={p.onDismissFlag}>Dismiss flag</button></li>
              )}
              <li><button type="button" onClick={p.onAddNote}>Add note</button></li>
            </ul>
          )}
        </>
      )}
    </Sheet>
  )
}
