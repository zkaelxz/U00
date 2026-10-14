import { useState, type ReactNode } from 'react'

import {
  editCaseReference, getCaseEdits, setCaseExcluded,
  type BenchmarkCase, type CaseEdit,
} from '../../api/benchmark'
import { Badge } from '../../components/Badge'
import { humanize } from '../../components/labels'
import { buttonClass } from '../../components/uiClasses'
import { formatWhen, plainError } from './benchmarkForm'

type Props = {
  c: BenchmarkCase
  /** False on a remote viewer: the controls that change a case are hidden. */
  canEdit: boolean
  /** The delete button, rendered at the row's right edge. */
  remove: ReactNode
  /** The case list changed (an edit also changes a clip's other case): reload it. */
  onChanged: () => void
}

const FIELD_LABEL: Record<string, string> = { reference_text: 'Reference', source_text: 'Source' }

/** One case in a set's list, with its exclude, edit-reference and history controls. */
export function CaseRow({ c, canEdit, remove, onChanged }: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [history, setHistory] = useState<CaseEdit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const name = c.label || `case ${c.id}`

  const run = async (work: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await work()
      onChanged()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(false)
    }
  }

  const toggleHistory = async () => {
    if (history) {
      setHistory(null)
      return
    }
    try {
      setHistory((await getCaseEdits(c.id)).edits)
    } catch (e) {
      setError(plainError(e))
    }
  }

  return (
    <li className={c.excluded ? 'bench-case-excluded' : undefined}>
      <div className="bench-case-text">
        <strong>{c.label || `Case ${c.id}`}</strong>
        <span className="muted"> · {humanize('language', c.source_language)}</span>
        <span className="pill-row">
          {c.excluded && <Badge tone="warn">Excluded</Badge>}
          {c.ref_version > 1 && <Badge tone="neutral">Reference v{c.ref_version}</Badge>}
          {c.title && <Badge tone="neutral">{c.title}{c.scene_order ? ` · scene ${c.scene_order}` : ''}</Badge>}
          {c.tags.map((t) => (
            <Badge key={t} tone="neutral">{t}</Badge>
          ))}
        </span>
        {c.source_text ? (
          <p lang={c.source_language}>{c.source_text}</p>
        ) : (
          <p className="muted">{c.has_input_file ? 'An audio or image file on the PC.' : 'No source text.'}</p>
        )}
        {editing ? (
          <div className="bench-case-edit">
            <textarea aria-label={`Reference of ${name}`} rows={3} maxLength={4000} lang={c.source_language} value={draft} onChange={(e) => setDraft(e.target.value)} />
            <p className="muted">Saving keeps this case's past results and notes the change below, so a score from before the fix can be told apart.</p>
            <div className="actions">
              <button
                type="button"
                className={buttonClass('primary', 'sm')}
                disabled={busy || !draft.trim() || draft.trim() === (c.reference_text ?? '')}
                onClick={() => void run(async () => {
                  await editCaseReference(c.id, draft.trim())
                  setEditing(false)
                  setHistory(null)
                })}
              >
                Save reference
              </button>
              <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setEditing(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <p className={c.reference_text ? undefined : 'muted'}>
            <span className="muted">Reference: </span>
            {c.reference_text || 'None (this case is run but not scored)'}
          </p>
        )}
        <div className="bench-case-actions">
          {canEdit && !editing && (
            <>
              <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} aria-label={`${c.excluded ? 'Include' : 'Exclude'} ${name}`} onClick={() => void run(() => setCaseExcluded(c.id, !c.excluded))}>
                {c.excluded ? 'Include again' : 'Exclude'}
              </button>
              {c.reference_text && (
                <button
                  type="button"
                  className={buttonClass('ghost', 'sm')}
                  aria-label={`Edit reference of ${name}`}
                  onClick={() => {
                    setDraft(c.reference_text ?? '')
                    setEditing(true)
                  }}
                >
                  Edit reference
                </button>
              )}
            </>
          )}
          {c.ref_version > 1 && (
            <button type="button" className={buttonClass('ghost', 'sm')} aria-expanded={!!history} aria-label={`Changes to the reference of ${name}`} onClick={() => void toggleHistory()}>
              {history ? 'Hide changes' : 'Changes'}
            </button>
          )}
        </div>
        {history && (
          <ol className="bench-case-history" aria-label={`Changes to ${name}`}>
            {history.map((e) => (
              <li key={`${e.version}:${e.field}`}>
                <span className="muted num">v{e.version} · {formatWhen(e.edited_at)}</span>{' '}
                {FIELD_LABEL[e.field] ?? e.field}: <del>{e.old_text}</del> → <ins>{e.new_text}</ins>
              </li>
            ))}
          </ol>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
      {remove}
    </li>
  )
}
