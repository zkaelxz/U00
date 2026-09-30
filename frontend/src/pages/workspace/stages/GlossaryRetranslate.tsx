import { useId, useState } from 'react'

import { ApiError } from '../../../api/client'
import { getGlossaryAffected, startGlossaryAffectedRun } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { humanize } from '../../../components/labels'
import { buttonClass } from '../../../components/uiClasses'
import { lineNumber } from '../../../lineNumber'
import type { GlossaryAffectedPreview } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import type { RunForm } from '../translateForm'
import {
  affectedParams,
  buildAffectedRunBody,
  chosenIds,
  costText,
  defaultSelection,
  selectionEstimate,
} from './glossaryRetranslateModel'

function isStalePreview(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409
    && (e.details as { reason?: string } | undefined)?.reason === 'stale_preview'
}

// After glossary edits: re-translate only the lines the glossary affects, with
// the Translate form's settings. Hand-edited lines stay unticked (and locked)
// unless the person includes them. Closing the list changes nothing.
export function GlossaryRetranslate({ f, busy, onStarted }: { f: RunForm; busy: boolean; onStarted: (jobId: string) => void }) {
  const { dramaId } = useStage()
  const ids = useId()
  const [open, setOpen] = useState(false)
  const [termIds, setTermIds] = useState<number[]>([])
  const [preview, setPreview] = useState<GlossaryAffectedPreview | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [includeHand, setIncludeHand] = useState(false)
  const [loading, setLoading] = useState(false)
  const [pending, setPending] = useState(false)
  const [stale, setStale] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  const load = (terms: number[]) => {
    const params = affectedParams(f, terms)
    if (!params) return setProblem('Cost cap must be a number of dollars, 0 or more.')
    setProblem(null)
    setLoading(true)
    getGlossaryAffected(dramaId, params).then(
      (p) => {
        setError(null)
        setStale(false)
        setPreview(p)
        setSelected(defaultSelection(p.lines))
        setIncludeHand(false)
      },
      setError,
    ).finally(() => setLoading(false))
  }

  const close = () => {
    setOpen(false)
    setPreview(null)
    setStale(false)
    setError(null)
    setProblem(null)
  }

  if (!open) {
    return (
      <div className="check-row">
        <button
          type="button"
          className={`${buttonClass('secondary')} glossary-retranslate-open`}
          onClick={() => {
            setOpen(true)
            load(termIds)
          }}
        >
          Re-translate lines affected by the glossary…
        </button>
      </div>
    )
  }

  const chosen = preview ? chosenIds(preview.lines, selected, includeHand) : []
  const est = preview ? selectionEstimate(preview, chosen, includeHand) : null
  const toggle = (id: number, on: boolean) =>
    setSelected((s) => {
      const next = new Set(s)
      if (on) next.add(id)
      else next.delete(id)
      return next
    })
  const toggleTerm = (id: number, on: boolean) =>
    setTermIds((t) => (on ? [...t, id] : t.filter((x) => x !== id)))

  const start = () => {
    if (!preview || !chosen.length) return
    setPending(true)
    startGlossaryAffectedRun(dramaId, buildAffectedRunBody(f, preview, chosen, includeHand)).then(
      (r) => {
        close()
        onStarted(r.job_id)
      },
      (e: unknown) => {
        if (isStalePreview(e)) {
          setError(null)
          setStale(true)
        } else setError(e)
      },
    ).finally(() => setPending(false))
  }

  return (
    <section className="glossary-retranslate" aria-labelledby={`${ids}-title`} data-testid="glossary-retranslate">
      <h4 id={`${ids}-title`}>Lines affected by the glossary</h4>
      <p className="muted">
        Lines whose source uses a glossary term (or one of its aliases), and lines whose English uses a banned
        translation. Only the lines you tick are translated again, with the settings above.
      </p>
      {preview && preview.terms.length > 0 && (
        <details className="glossary-retranslate-terms">
          <summary>Only some terms changed? {termIds.length ? `(${termIds.length} chosen)` : '(all terms)'}</summary>
          <fieldset>
            <legend className="muted">Tick the terms you changed; none ticked means every term.</legend>
            {preview.terms.map((t) => (
              <label key={t.id} className="inline">
                <input type="checkbox" checked={termIds.includes(t.id)} onChange={(e) => toggleTerm(t.id, e.target.checked)} />{' '}
                {t.term_original} → {t.term_translation || '(no translation)'}
              </label>
            ))}
          </fieldset>
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={loading} onClick={() => load(termIds)}>
            Update list
          </button>
        </details>
      )}
      {loading && <p className="muted" role="status">Finding affected lines…</p>}
      {problem && <p className="error" role="alert">{problem}</p>}
      {stale && (
        <p className="error" role="alert">
          Some of these lines or the glossary changed since this list was made, so nothing was started.{' '}
          <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => load(termIds)}>Refresh list</button>
        </p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {preview && !preview.has_glossary && <p className="muted">This drama's series has no glossary terms yet.</p>}
      {preview && preview.has_glossary && !preview.lines.length && (
        <p className="muted">No translated line uses {termIds.length ? 'these terms' : 'the glossary'}.</p>
      )}
      {preview && preview.lines.length > 0 && (
        <>
          <p role="status">
            {preview.lines.length} line{preview.lines.length === 1 ? '' : 's'} affected
            {preview.hand_edited_count > 0 && `, ${preview.hand_edited_count} hand-edited`}.
          </p>
          {preview.hand_edited_count > 0 && (
            <div className="glossary-retranslate-include">
              <label className="inline stage-ack">
                <input type="checkbox" checked={includeHand} onChange={(e) => setIncludeHand(e.target.checked)} />{' '}
                Include hand-edited lines
              </label>
              <p className={includeHand ? 'warn' : 'muted'} id={`${ids}-hand`}>
                {includeHand
                  ? 'The hand-edited lines you tick will lose your edits. A snapshot is saved first, so you can restore them from History.'
                  : 'Hand-edited lines are left alone. A line counts as hand-edited unless its English is exactly what a translate run made.'}
              </p>
            </div>
          )}
          <ul className="glossary-retranslate-list">
            {preview.lines.map((l) => {
              const locked = l.hand_edited && !includeHand
              return (
                <li key={l.id}>
                  <label className="glossary-retranslate-pick">
                    <input
                      type="checkbox"
                      checked={selected.has(l.id) && !locked}
                      disabled={locked}
                      aria-describedby={l.hand_edited ? `${ids}-hand` : undefined}
                      onChange={(e) => toggle(l.id, e.target.checked)}
                    />{' '}
                    Line {lineNumber(l.idx)}
                    {l.hand_edited && <>{' '}<span className="badge">hand-edited</span></>}
                  </label>
                  <span className="glossary-retranslate-src" lang="zh">{l.zh}</span>
                  <span className="glossary-retranslate-en">Now: {l.en}</span>
                  <span className="muted">
                    {l.matched_terms.map((m) =>
                      m.reason === 'banned'
                        ? `uses a banned translation of ${m.term_original}`
                        : `${m.term_original} → ${m.term_translation || '(no translation)'}`).join(' · ')}
                  </span>
                </li>
              )
            })}
          </ul>
          {est && (
            <p className="translate-estimate" data-testid="glossary-retranslate-estimate">
              {chosen.length} line{chosen.length === 1 ? '' : 's'} with {humanize('engine', est.estimate.engine)}
              {est.estimate.model ? ` (${est.estimate.model})` : ''}: {costText(est.estimate, est.exact)}
              {est.estimate.monthly_refusal && (
                <span className="error" role="alert">The monthly cap would be exceeded, so this run would be refused.</span>
              )}
              {est.estimate.estimate_above_cap && !est.estimate.monthly_refusal && (
                <span className="error" role="alert">The estimate is above the cap, so the run could stop early.</span>
              )}
            </p>
          )}
          {f.bulk && <p className="muted">Runs as a normal translation: Bulk can't take a list of lines.</p>}
        </>
      )}
      <div className="check-row">
        {preview && preview.lines.length > 0 && (
          <button type="button" className="primary" disabled={busy || pending || loading || !chosen.length} onClick={start}>
            Re-translate {chosen.length} line{chosen.length === 1 ? '' : 's'}
          </button>
        )}
        <button type="button" className={buttonClass('ghost')} onClick={close}>{preview?.lines.length ? 'Cancel' : 'Close'}</button>
        {busy && <span className="muted">A translate job is running.</span>}
      </div>
    </section>
  )
}
