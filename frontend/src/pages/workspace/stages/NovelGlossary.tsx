import { useEffect, useState } from 'react'

import { applyNovelGlossary, getNovelGlossary, startNovelGlossary } from '../../../api/autotuneGlossary'
import { ApiError } from '../../../api/client'
import { cancelJob } from '../../../api/jobs'
import { getSourceConfig } from '../../../api/source'
import { getNovelStatus } from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { Section } from '../../../components/Section'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import { useStage } from '../StageContext'
import {
  ENGINE_CHANGED_TEXT,
  addTermsLabel,
  applySummary,
  chosenTerms,
  countExisting,
  defaultTermSelection,
  isActiveStatus,
  novelGlossaryBlocker,
  novelGlossaryProgressText,
  novelGlossaryStartErrorText,
  toggleTerm,
} from './autotuneGlossary'
import { useRunStatus } from './useRunStatus'
import './autotuneGlossary.css'

interface Props {
  // Called after terms were added so the glossary table reloads.
  onApplied: () => void
}

// Glossary → "From novel": asks the drama's translation engine to propose
// terms from the attached novel, then adds the checked ones to the series
// glossary (matched by term text, never by position).
export function NovelGlossary({ onApplied }: Props) {
  const { dramaId, drama } = useStage()
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, refresh } = useRunStatus(dramaId, getNovelGlossary)
  const [hasNovel, setHasNovel] = useState<boolean | null>(null)
  // null = the default selection for the current proposals.
  const [picked, setPicked] = useState<Set<string> | null>(null)
  const [overwrite, setOverwrite] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      getNovelStatus(dramaId).then((s) => s.has_novel_text, () => false),
      getSourceConfig(dramaId).then((c) => c.has_raw_novel_context, () => false),
    ]).then(([text, raw]) => !cancelled && setHasNovel(text || raw))
    return () => {
      cancelled = true
    }
  }, [dramaId])

  const active = isActiveStatus(status?.status)
  const blocker = novelGlossaryBlocker(dramaId, drama.series_id, hasNovel !== false)
  const proposals: NovelGlossaryProposal[] = status?.status === 'done' ? status.proposals ?? [] : []
  const sel = picked ?? defaultTermSelection(proposals)
  const chosen = chosenTerms(proposals, sel)
  const existing = overwrite ? countExisting(proposals, chosen) : 0
  const engine = drama.translation_engine || 'claude'

  const start = () => {
    setBusy(true)
    setError(null)
    setProblem(null)
    setNote(null)
    setPicked(null)
    setConfirming(false)
    startNovelGlossary(dramaId)
      .then(
        () => refresh(),
        (e: unknown) => {
          if (e instanceof ApiError && e.status === 409) {
            // Either a run is already going (attach to it) or the engine changed.
            return getNovelGlossary(dramaId).then(
              (s) => (isActiveStatus(s.status) ? refresh() : setProblem(ENGINE_CHANGED_TEXT)),
              () => setProblem(ENGINE_CHANGED_TEXT),
            )
          }
          const text = novelGlossaryStartErrorText(e)
          if (text) setProblem(text)
          else setError(e)
        },
      )
      .finally(() => setBusy(false))
  }

  const cancel = () => {
    if (status) cancelJob(status.job_id).then(refresh, setError)
  }

  const apply = () => {
    setBusy(true)
    setConfirming(false)
    setProblem(null)
    applyNovelGlossary(
      dramaId,
      overwrite ? { terms: chosen, overwrite_existing: true, confirm: true } : { terms: chosen },
    )
      .then(
        (r) => {
          setError(null)
          setNote(applySummary(r))
          setPicked(new Set(chosen.filter((t) => !r.added.includes(t) && !r.overwritten.includes(t))))
          onApplied()
        },
        setError,
      )
      .finally(() => setBusy(false))
  }

  const onAdd = () => (existing > 0 ? setConfirming(true) : apply())
  const toggle = (term: string) => {
    setConfirming(false)
    setPicked(toggleTerm(sel, term))
  }

  const summary = active
    ? 'running'
    : proposals.length
      ? `${proposals.length} proposed`
      : `uses ${engine}`

  const checkbox = (p: NovelGlossaryProposal) => (
    <input type="checkbox" aria-label={`Select ${p.term}`} checked={sel.has(p.term)} onChange={() => toggle(p.term)} />
  )
  const inGlossary = <span className="badge">already in glossary</span>

  return (
    <Section storageKey="translate.glossary.novel" title="From novel" summary={summary}>
      <div className="novel-glossary" data-testid="novel-glossary">
        <p className="muted">
          Proposes names and terms from the attached novel using this drama's translation engine ({engine}). Nothing
          is added until you choose.
        </p>
        {active && status ? (
          <p className="actions" role="status" data-testid="novel-glossary-running">
            <span>{novelGlossaryProgressText(status.status, status.progress)}</span>
            <button type="button" onClick={cancel}>Cancel</button>
          </p>
        ) : (
          <div className="actions">
            <button type="button" disabled={!!blocker || hasNovel === null || busy} onClick={start}>
              {proposals.length ? 'Extract terms again' : 'Extract terms'}
            </button>
            {blocker && (
              <span className="muted">
                {blocker.text} (<a href={blocker.href}>{blocker.link}</a>).
              </span>
            )}
          </div>
        )}
        {status?.status === 'error' && (
          <p className="error" role="alert">
            {safeDetail(status.message) ?? 'Extraction failed. Details are in the app log.'}
          </p>
        )}
        {status?.status === 'cancelled' && <p className="muted">Extraction was cancelled.</p>}
        {status?.status === 'done' && proposals.length === 0 && <p className="muted">No new terms were found.</p>}
        {proposals.length > 0 &&
          (isPhone ? (
            <ul className="novel-glossary-cards" data-testid="novel-glossary-proposals">
              {proposals.map((p) => (
                <li key={p.term}>
                  <label>
                    {checkbox(p)} <strong>{p.term}</strong> → {p.suggested_translation}
                  </label>
                  <div className="muted">
                    {[p.category, p.policy].filter(Boolean).join(' · ')} {p.already_in_glossary && inGlossary}
                  </div>
                  {p.reason && <div className="muted novel-glossary-reason">{p.reason}</div>}
                </li>
              ))}
            </ul>
          ) : (
            <div className="table-scroll">
              <table data-testid="novel-glossary-proposals">
                <thead>
                  <tr>
                    <th />
                    <th>Original</th>
                    <th>Translation</th>
                    <th>Category</th>
                    <th>Policy</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {proposals.map((p) => (
                    <tr key={p.term}>
                      <td>{checkbox(p)}</td>
                      <td>
                        {p.term} {p.already_in_glossary && inGlossary}
                      </td>
                      <td>{p.suggested_translation}</td>
                      <td>{p.category ?? ''}</td>
                      <td>{p.policy ?? ''}</td>
                      <td className="novel-glossary-reason">{p.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        {proposals.length > 0 && (
          <>
            <label className="check">
              <input
                type="checkbox"
                checked={overwrite}
                onChange={(e) => {
                  setConfirming(false)
                  setOverwrite(e.target.checked)
                }}
              />{' '}
              Overwrite existing terms
            </label>
            {!confirming ? (
              <div className="actions">
                <button type="button" className="primary" disabled={chosen.length === 0 || busy} onClick={onAdd}>
                  {addTermsLabel(chosen.length)}
                </button>
                {chosen.length === 0 && <span className="muted">Select terms to add.</span>}
              </div>
            ) : (
              <div className="actions" role="alert">
                <span>
                  Replace {existing} existing term{existing === 1 ? '' : 's'} in the series glossary?
                </span>
                <button type="button" className="danger" disabled={busy} onClick={apply}>
                  Yes, overwrite
                </button>
                <button type="button" onClick={() => setConfirming(false)}>Cancel</button>
              </div>
            )}
          </>
        )}
        {note && <p role="status">{note}</p>}
        {problem && <p className="error" role="alert">{problem}</p>}
        <ErrorBanner error={error ?? loadError} onDismiss={() => setError(null)} />
      </div>
    </Section>
  )
}
