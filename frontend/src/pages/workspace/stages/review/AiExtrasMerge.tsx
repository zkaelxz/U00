import { useState } from 'react'

import { listAllLines, restoreSnapshot } from '../../../../api/restructure'
import { applyMergeShort, previewMergeShort } from '../../../../api/reviewExtras'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { buttonClass } from '../../../../components/uiClasses'
import { TypedConfirm } from '../../../../components/TypedConfirm'
import { lineNumber } from '../../../../lineNumber'
import type { MergeShortOptions, MergeShortPreview } from '../../../../types/reviewExtras'
import { mergeFormDefaults, mergeSummary, parseMergeForm, type MergeForm } from './aiExtrasLogic'
import { JOB_RUNNING_MESSAGE, undoErrorText, undoHandleOf, UNDO_DONE_MESSAGE, type UndoHandle } from './reviewLogic'
import { UndoNotice } from './UndoNotice'

const SHOWN = 8

interface Props {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
}

// Merge short adjacent lines: a read-only preview, then a typed "merge" to
// apply it. The server re-checks the line ids and the merge groups (409 if
// either changed) and saves a Line history snapshot first.
export function AiExtrasMerge({ dramaId, jobRunning, onChanged }: Props) {
  const [form, setForm] = useState<MergeForm>(mergeFormDefaults)
  const [preview, setPreview] = useState<{ p: MergeShortPreview; opts: MergeShortOptions } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState<string | null>(null)
  const [undo, setUndo] = useState<UndoHandle | null>(null)
  const parsed = parseMergeForm(form)

  const set = (key: keyof MergeForm) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setForm((f) => ({ ...f, [key]: e.target.value }))
    setPreview(null)
  }

  const load = () => {
    if (!parsed.options) return
    const opts = parsed.options
    setBusy(true)
    setError(null)
    setDone(null)
    setUndo(null)
    previewMergeShort(dramaId, opts)
      .then((p) => setPreview({ p, opts }), setError)
      .finally(() => setBusy(false))
  }

  const apply = () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    applyMergeShort(dramaId, {
      expected_line_ids: preview.p.source_line_ids,
      expected_groups: preview.p.groups,
      ...preview.opts,
    })
      .then((r) => {
        setPreview(null)
        setDone(`Merged ${r.merged_groups} group${r.merged_groups === 1 ? '' : 's'}.${undoHandleOf(r) ? '' : ' The previous lines are in Records → Line history.'}`)
        setUndo(undoHandleOf(r))
        onChanged()
      }, setError)
      .finally(() => setBusy(false))
  }

  const doUndo = async () => {
    if (!undo) return
    setBusy(true)
    setError(null)
    try {
      const lines = await listAllLines(dramaId)
      await restoreSnapshot(dramaId, undo.historyId, lines.map((l) => l.id), undo.fingerprint)
      setUndo(null)
      setDone(UNDO_DONE_MESSAGE)
      onChanged()
    } catch (e) {
      const text = undoErrorText(e)
      if (text) {
        setUndo(null)
        setDone(text)
      } else setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section storageKey="review.aiExtras.merge" title="Merge short lines" summary="Join short same-speaker lines">
      <p className="muted">
        Joins consecutive short lines from the same speaker when the gap between them is small. Changes the line count.
      </p>
      <div className="review-edit-row">
        <Field label="Short line" unit="s" help="Only lines shorter than this are merged." error={parsed.errors.min_duration}>
          <input type="number" inputMode="decimal" step="0.1" value={form.min_duration} onChange={set('min_duration')} />
        </Field>
        <Field label="Max gap" unit="s" help="Largest pause between two lines that can still merge." error={parsed.errors.max_gap}>
          <input type="number" inputMode="decimal" step="0.1" value={form.max_gap} onChange={set('max_gap')} />
        </Field>
        <Field label="Max length" unit="chars" help="Longest merged line, source and translation each." error={parsed.errors.max_chars}>
          <input type="number" inputMode="numeric" step="1" value={form.max_chars} onChange={set('max_chars')} />
        </Field>
      </div>
      <div className="actions">
        <button type="button" className={buttonClass(preview ? 'secondary' : 'primary')} disabled={busy || !parsed.options} onClick={load}>
          {busy && !preview ? 'Checking…' : preview ? 'Preview again' : 'Preview merge'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {done && undo && <UndoNotice message={done} busy={busy} onUndo={() => void doUndo()} onDismiss={() => setUndo(null)} />}
      {done && !undo && (
        <p role="status" className="muted">
          {done}
        </p>
      )}
      {preview && (
        <div className="stack" data-testid="merge-short-preview">
          <p>{mergeSummary(preview.p)}</p>
          {preview.p.merges.length > 0 && (
            <>
              <ul className="review-matches">
                {preview.p.merges.slice(0, SHOWN).map((m) => (
                  <li key={m.line_id}>
                    <span className="muted">
                      #{lineNumber(m.idx)} +{m.merged_line_ids.length}
                    </span>{' '}
                    <span lang="zh">{m.zh}</span>
                    {m.en && <span className="muted"> · {m.en}</span>}
                  </li>
                ))}
                {preview.p.merges.length > SHOWN && <li className="muted">and {preview.p.merges.length - SHOWN} more</li>}
              </ul>
              <TypedConfirm word="merge" action="Merge lines" blocked={jobRunning ? JOB_RUNNING_MESSAGE : null} busy={busy} onConfirm={apply}>
                <p className="muted">Your current lines are saved as a snapshot first (Records → Line history).</p>
              </TypedConfirm>
            </>
          )}
        </div>
      )}
    </Section>
  )
}
