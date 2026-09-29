import { memo, useEffect, useState, type KeyboardEvent, type MouseEvent } from 'react'

import { retryBlockedLine } from '../../../../api/review'
import { translateApi } from '../../../../api/translate'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import type { ReviewLine, TmSuggestion } from '../../../../types/review'
import type { TranslateEngine } from '../../../../types/translate'
import { LineAi } from './LineAi'
import { LineOrigin } from './LineOrigin'
import { LineTools } from './LineTools'
import { buildPatch, CONFLICT_MESSAGE, formatTime, isToolMode, JOB_RUNNING_MESSAGE, type LineDraft, type PanelMode } from './reviewLogic'
import { lineNumber } from '../../../../lineNumber'

export interface NoteDraft {
  term: string
  type: string
  text: string
}

export interface EditState {
  lineId: number
  // The line as it was when editing began (or last saved): the compare-and-set
  // base, kept so a draft can still be saved after its row leaves the view.
  base: ReviewLine
  draft: LineDraft
  details: boolean
  note: NoteDraft | null
}

export interface RowIssue {
  lineId: number
  error?: unknown
  conflict?: boolean
  problem?: string
}

// Stable callbacks from the editor (LinesPanel); rows never own line state.
export interface RowActions {
  activate: (id: number) => void
  openEdit: (id: number, details?: boolean) => void
  setDraft: (patch: Partial<LineDraft>) => void
  cancelEdit: () => void
  save: () => void
  saveAndNext: () => void
  toggleDetails: (id: number) => void
  setNote: (note: NoteDraft | null) => void
  saveNote: () => void
  openSheet: (id: number) => void
  openStructure: (id: number, view: 'split' | 'merge') => void
  splitAtCursor: (id: number, field: 'zh' | 'en', utf16Offset: number) => void
  setAi: (id: number, mode: PanelMode | null) => void
  useSuggestion: (id: number, text: string) => Promise<boolean>
  dismissFlag: (id: number) => void
  applyLine: (saved: ReviewLine, closeEdit: boolean) => void
  playLine: (line: ReviewLine) => void
  acceptTm: (id: number, entryId: number) => void
  dismissTm: (s: TmSuggestion) => void
  clearIssue: () => void
  reload: () => void
}

interface Props {
  dramaId: number
  line: ReviewLine
  active: boolean
  isPhone: boolean
  hasMedia: boolean
  jobRunning: boolean
  // Filtered or search view: merge needs the true next line.
  limited: boolean
  edit: EditState | null
  ai: PanelMode | null
  // A translation-memory suggestion for this line (R11), if any.
  tm: TmSuggestion | null
  issue: RowIssue | null
  actions: RowActions
}

const INTERACTIVE = 'button, a, input, textarea, select, label, summary, dialog'

// One line: meta, source and translation. The active row (roving tabIndex)
// carries a toolbar on wider screens; editing happens in place. Details and
// the AI panel are only rendered while open, so a long list stays light.
function LineRowImpl({ dramaId, line, active, isPhone, hasMedia, jobRunning, limited, edit, ai, tm, issue, actions }: Props) {
  const draft = edit?.draft ?? null

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'Escape') {
      e.preventDefault()
      actions.cancelEdit()
    } else if ((e.key === 's' || e.key === 'S') && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      actions.save()
    } else if (e.key === 'Enter' && e.altKey) {
      e.preventDefault()
      const field = e.currentTarget.dataset.field === 'zh' ? 'zh' : 'en'
      actions.splitAtCursor(line.id, field, e.currentTarget.selectionStart ?? 0)
    } else if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault()
      actions.saveAndNext()
    }
  }

  const onRowClick = (e: MouseEvent<HTMLLIElement>) => {
    if ((e.target as Element).closest(INTERACTIVE)) return
    actions.activate(line.id)
  }

  const className = ['review-line', active && 'is-active', draft && 'is-editing'].filter(Boolean).join(' ')

  return (
    <li
      className={className}
      data-testid={`line-${line.id}`}
      data-line-id={line.id}
      data-flagged={line.flag ? 'true' : undefined}
      aria-current={active ? 'true' : undefined}
      tabIndex={active ? 0 : -1}
      onClick={onRowClick}
    >
      <div className="review-line-meta">
        <span className="review-idx">#{lineNumber(line.idx)}</span>
        <span className="review-time">
          {formatTime(line.start)}
          {!isPhone && <>–{formatTime(line.end)}</>}
        </span>
        {line.speaker && <span className="review-speaker">{line.speaker}</span>}
        {line.sfx && <span>sound cue</span>}
        {line.dub_filename && !isPhone && <span>dub: {line.dub_filename}</span>}
        {line.flag && (
          <span className="review-flag" data-testid="line-flag" title={line.flag_note ?? undefined}>
            <span aria-hidden="true">⚑</span>
            <span className={isPhone ? 'sr-only' : undefined}>
              {' '}Flagged: {line.flag}
              {line.flag_note ? ` · ${line.flag_note}` : ''}
            </span>
          </span>
        )}
        <button
          type="button"
          className="review-more"
          aria-label={`More actions for line ${lineNumber(line.idx)}`}
          aria-haspopup="dialog"
          onClick={() => actions.openSheet(line.id)}
        >
          {active && !isPhone ? 'More' : '⋯'}
        </button>
      </div>
      {/* Phones show only ⚑ in the meta line; the active row spells the reason out. */}
      {isPhone && active && line.flag && (
        <div className="review-flag review-flag-line" aria-hidden="true">
          Flagged: {line.flag}
          {line.flag_note ? ` · ${line.flag_note}` : ''}
        </div>
      )}
      <div className="review-body">
        <div lang="zh" className="review-zh">{line.zh}</div>
        {draft ? (
          <div className="review-en-edit">
            <textarea
              aria-label="Translation"
              autoFocus
              enterKeyHint="next"
              data-field="en"
              value={draft.en}
              onChange={(e) => actions.setDraft({ en: e.target.value })}
              onKeyDown={onKey}
              rows={2}
            />
            {!isPhone && (
              <div className="review-actions">
                <button type="button" className="primary" onClick={actions.saveAndNext}>Save &amp; next</button>
                <button type="button" onClick={actions.save}>Save</button>
                <button type="button" onClick={actions.cancelEdit}>Cancel</button>
                <span className="muted review-keys">Enter save &amp; next · Shift+Enter new line · Esc cancel</span>
              </div>
            )}
          </div>
        ) : (
          <button
            type="button"
            className="review-en"
            data-testid="line-en"
            title="Click to edit the translation"
            onClick={() => (isPhone && !active ? actions.activate(line.id) : actions.openEdit(line.id))}
          >
            {line.en || <span className="muted">(not translated)</span>}
          </button>
        )}
      </div>

      {tm && (
        <div className="review-tm" data-testid="line-tm">
          <span>
            <span className="muted">Memory ({tm.exact ? 'exact' : `${Math.round(tm.similarity * 100)}% similar`}):</span>{' '}
            {tm.suggestion}
          </span>
          <span className="review-actions">
            <button type="button" onClick={() => actions.acceptTm(line.id, tm.entry_id)}>Use</button>
            <button type="button" onClick={() => actions.dismissTm(tm)}>Dismiss</button>
          </span>
        </div>
      )}

      {active && !isPhone && (
        <div className="review-tools" role="toolbar" aria-label={`Line ${lineNumber(line.idx)} actions`}>
          {hasMedia && (
            <button type="button" onClick={() => actions.playLine(line)} title="Play the line (Space)">
              ▶ Play
            </button>
          )}
          <button
            type="button"
            aria-expanded={!!edit?.details}
            onClick={() => actions.toggleDetails(line.id)}
            title="Timing, speaker, source (D)"
          >
            Edit details
          </button>
          <button
            type="button"
            disabled={!line.en}
            aria-pressed={ai === 'improve'}
            onClick={() => actions.setAi(line.id, ai === 'improve' ? null : 'improve')}
            title="Improve translation (I)"
          >
            {line.en ? 'Improve translation' : 'Improve (needs a translation first)'}
          </button>
          <button
            type="button"
            aria-pressed={ai === 'explain'}
            onClick={() => actions.setAi(line.id, ai === 'explain' ? null : 'explain')}
            title="Why this? (W)"
          >
            Why this?
          </button>
          <button type="button" disabled={jobRunning} onClick={() => actions.openStructure(line.id, 'split')} title="Split line (Alt+Enter while editing)">
            Split…
          </button>
          <button
            type="button"
            disabled={jobRunning || limited}
            onClick={() => actions.openStructure(line.id, 'merge')}
            title={limited ? 'Merge works in the All lines view' : 'Merge with next (M)'}
          >
            Merge ↓
          </button>
          {line.flag && (
            <button type="button" onClick={() => actions.dismissFlag(line.id)} title="Dismiss flag (F)">
              Dismiss flag
            </button>
          )}
          {jobRunning && <span className="muted review-reason">{JOB_RUNNING_MESSAGE}</span>}
        </div>
      )}

      {edit?.details && draft && (
        <div className="review-edit">
          <Field label="Source">
            <textarea
              lang="zh"
              data-field="zh"
              value={draft.zh}
              onChange={(e) => actions.setDraft({ zh: e.target.value })}
              onKeyDown={onKey}
              rows={2}
            />
          </Field>
          <div className="review-edit-row">
            <Field label="Speaker">
              <input value={draft.speaker} onChange={(e) => actions.setDraft({ speaker: e.target.value })} />
            </Field>
            <Field label="Start (s)">
              <input inputMode="decimal" value={draft.start} onChange={(e) => actions.setDraft({ start: e.target.value })} />
            </Field>
            <Field label="End (s)">
              <input inputMode="decimal" value={draft.end} onChange={(e) => actions.setDraft({ end: e.target.value })} />
            </Field>
          </div>
          <label className="review-check">
            <input type="checkbox" checked={draft.sfx} onChange={(e) => actions.setDraft({ sfx: e.target.checked })} /> Sound cue
            (no dialogue)
          </label>
          {issue?.problem && <p className="error" role="alert">{issue.problem}</p>}
          <div className="review-actions">
            <button
              type="button"
              onClick={() => actions.setNote(edit.note ? null : { term: '', type: 'translation', text: '' })}
            >
              Add note
            </button>
          </div>
          {line.flag === BLOCKED_FLAG && (
            <BlockedRetry
              dramaId={dramaId}
              lineId={line.id}
              dirty={buildPatch(edit.base, draft) !== null}
              jobRunning={jobRunning}
              onApplied={actions.applyLine}
            />
          )}
          <LineOrigin
            dramaId={dramaId}
            lineId={line.id}
            // "Use this" wrote only zh: show it now; close the editor unless
            // it holds unsaved changes (those stay, and a stale zh edit gets a 409).
            onChanged={(applied) =>
              actions.applyLine({ ...line, zh: applied.zh }, buildPatch(edit.base, draft) === null)
            }
            onRestored={(saved) => actions.applyLine(saved, buildPatch(edit.base, draft) === null)}
          />
        </div>
      )}
      {edit?.note && (
        <div className="review-edit">
          <div className="review-edit-row">
            <Field label="Term">
              <input value={edit.note.term} onChange={(e) => edit.note && actions.setNote({ ...edit.note, term: e.target.value })} />
            </Field>
            <Field label="Type">
              <input value={edit.note.type} onChange={(e) => edit.note && actions.setNote({ ...edit.note, type: e.target.value })} />
            </Field>
          </div>
          <Field label="Note">
            <textarea value={edit.note.text} onChange={(e) => edit.note && actions.setNote({ ...edit.note, text: e.target.value })} rows={2} />
          </Field>
          <div className="review-actions">
            <button type="button" disabled={!edit.note.term.trim() || !edit.note.text.trim()} onClick={actions.saveNote}>
              Save note
            </button>
          </div>
        </div>
      )}
      {ai && (isToolMode(ai) ? (
        <LineTools
          key={`${line.id}-${ai}`}
          dramaId={dramaId}
          line={line}
          mode={ai}
          onClose={() => actions.setAi(line.id, null)}
          onUse={(text) => actions.useSuggestion(line.id, text)}
        />
      ) : (
        <LineAi
          key={`${line.id}-${ai}`}
          dramaId={dramaId}
          line={line}
          mode={ai}
          onClose={() => actions.setAi(line.id, null)}
          onUse={(text) => actions.useSuggestion(line.id, text)}
        />
      ))}
      {issue?.conflict && (
        <div className="banner error-banner" role="alert" data-testid="line-conflict">
          <span>{CONFLICT_MESSAGE}</span>
          <button type="button" className="link" onClick={actions.reload}>
            Reload
          </button>
        </div>
      )}
      {issue?.error ? <ErrorBanner error={issue.error} onDismiss={actions.clearIssue} /> : null}
    </li>
  )
}

export const LineRow = memo(LineRowImpl)

const BLOCKED_FLAG = 'content_blocked'
const RETRY_DEFAULT_ENGINE = 'ollama'

// A line an engine's content filter refused: retry just this line with
// another engine (default Ollama, local, no cloud moderation). The drama's
// own engine is unchanged. The server's saved line is applied in place: on
// success the flag clears and the (clean) edit closes; a second block keeps
// the flag, updates its note and shows that engine's reason here. Retry is
// off while the edit has unsaved changes or a job runs on the drama.
function BlockedRetry({ dramaId, lineId, dirty, jobRunning, onApplied }: {
  dramaId: number
  lineId: number
  dirty: boolean
  jobRunning: boolean
  onApplied: (saved: ReviewLine, closeEdit: boolean) => void
}) {
  const [engines, setEngines] = useState<TranslateEngine[] | null>(null)
  const [engine, setEngine] = useState(RETRY_DEFAULT_ENGINE)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [blockedAgain, setBlockedAgain] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    translateApi.engines().then(
      (items) => {
        if (cancelled) return
        setEngines(items)
        if (!items.some((e) => e.name === RETRY_DEFAULT_ENGINE) && items[0]) setEngine(items[0].name)
      },
      () => !cancelled && setEngines([]),
    )
    return () => {
      cancelled = true
    }
  }, [])

  const retry = () => {
    setBusy(true)
    setError(null)
    setBlockedAgain(null)
    retryBlockedLine(dramaId, lineId, engine)
      .then(
        (r) => {
          if (r.retried) {
            onApplied(r.line, true)
            return
          }
          setBlockedAgain(r.reason ? `${r.engine} also blocked this line: ${r.reason}` : `${r.engine} also blocked this line.`)
          onApplied(r.line, false)
        },
        setError,
      )
      .finally(() => setBusy(false))
  }

  const reason = dirty ? 'Save or discard your edit first.' : jobRunning ? JOB_RUNNING_MESSAGE : null
  const options = engines && engines.length > 0 ? engines : [{ name: RETRY_DEFAULT_ENGINE, label: RETRY_DEFAULT_ENGINE, free: true }]
  return (
    <div className="review-retry" data-testid="blocked-retry">
      <p className="muted">Blocked by the engine's content filter. Retry just this line with another engine:</p>
      <div className="review-actions">
        <select aria-label="Retry engine" value={engine} disabled={busy} onChange={(e) => setEngine(e.target.value)}>
          {options.map((e) => (
            <option key={e.name} value={e.name}>
              {e.name}
              {e.free ? ' (free)' : ''}
            </option>
          ))}
        </select>
        <button type="button" disabled={busy || reason !== null} aria-busy={busy || undefined} onClick={retry}>
          {busy ? 'Retrying…' : 'Retry line'}
        </button>
        {reason && <span className="muted review-reason" data-testid="retry-reason">{reason}</span>}
      </div>
      {blockedAgain && (
        <p className="error" role="status" data-testid="blocked-again">
          {blockedAgain}
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
