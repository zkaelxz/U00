import { useEffect, useRef, useState } from 'react'

import { ApiError } from '../../../api/client'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import { useStage } from '../StageContext'
import {
  applySummary,
  chosenTerms,
  defaultTermSelection,
  formatElapsed,
  isActiveStatus,
  toggleTerm,
} from './autotuneGlossary'
import {
  applyRequest,
  buildOverrides,
  editProposal,
  extractionProgressText,
  glossaryApplyErrorText,
  missingTranslationText,
  missingTranslations,
  reviewSource,
  startTranslationLabel,
  type Edits,
  type GlossarySource,
  type ProposalValues,
} from './glossaryExtract'
import { GlossaryProposals } from './GlossaryProposals'
import {
  GLOSSARY_API,
  bumpGlossaryRun,
  bumpGlossaryTerms,
  startExtraction,
  useGlossaryCatalogues,
  useGlossaryRun,
  useHasNovel,
  useRunScoped,
} from './useGlossaryRun'
import './autotuneGlossary.css'
import { buttonClass } from '../../../components/uiClasses'

interface Props {
  // The engine the scan will really use: the title's saved one.
  engine: string
  // Start the translate run; note summarizes what was added to the glossary.
  onStart: (note: string | null) => void
  onCancel: () => void
  // Attach to the scan the server already holds instead of starting one.
  resume?: boolean
}

// Parity X28, "Review glossary before translating": the Translate button
// first runs a glossary extraction (from the novel when one is attached,
// else from the source lines), shows the proposals, and then either adds
// the checked terms and starts the run, or cancels. Mounted once per press
// (keyed by the caller), so each press extracts afresh.
export function GlossaryReview({ onStart, onCancel, engine, resume }: Props) {
  const { dramaId, drama } = useStage()
  const hasNovel = useHasNovel(dramaId, drama)
  return (
    <section className="panel glossary-review" aria-label="Review glossary">
      <h4>Review glossary before translating</h4>
      {hasNovel === null ? (
        <p className="muted" role="status">Checking for a novel…</p>
      ) : (
        <ReviewBody source={reviewSource(hasNovel)} engine={engine} onStart={onStart} onCancel={onCancel} resume={resume} />
      )}
    </section>
  )
}

function ReviewBody({ source, engine, onStart, onCancel, resume }: Props & { source: GlossarySource }) {
  const { dramaId } = useStage()
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, clearError } = useGlossaryRun(dramaId, source)
  // The run this press started (or attached to): only its status is shown,
  // never an earlier run's proposals. failed: it could not be started.
  const [started, setStarted] = useState<{ runId: string | null; failed: boolean } | 'pending'>('pending')
  const [startProblem, setStartProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const startedRef = useRef(false)
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    if (resume) {
      // A scan outlives this panel; a missing run reads as a failed start.
      GLOSSARY_API[source].get(dramaId).then(
        (s) => setStarted({ runId: s.run_id, failed: !s.run_id }),
        (e: unknown) => {
          setError(e)
          setStarted({ runId: null, failed: true })
        },
      )
      return
    }
    startExtraction(dramaId, source).then((r) => {
      setStartProblem(r.problem)
      setError(r.error)
      setStarted({ runId: r.runId, failed: r.problem !== null || r.error !== null || !r.runId })
    })
  }, [dramaId, source, resume])

  const failed = started !== 'pending' && started.failed
  const run = started !== 'pending' && !started.failed ? started.runId : null
  const cur = run && status?.run_id === run ? status : null
  const fresh = failed || cur !== null
  // Selection and edits belong to the run shown.
  const [picked, setPicked] = useRunScoped<Set<string> | null>(run, null)
  const [edits, setEdits] = useRunScoped<Edits>(run, {})
  const active = isActiveStatus(cur?.status)
  const proposals: NovelGlossaryProposal[] = cur?.status === 'done' ? cur.proposals ?? [] : []
  const sel = picked ?? defaultTermSelection(proposals)
  const chosen = chosenTerms(proposals, sel)
  const missing = missingTranslations(proposals, chosen, edits)
  const catalogues = useGlossaryCatalogues(proposals.length > 0)
  // The scan is one call with no progress, so a ticking clock shows it is alive.
  const [elapsed, setElapsed] = useState<number | null>(null)
  useEffect(() => {
    if (!active) return setElapsed(null)
    const t0 = Date.now()
    setElapsed(0)
    const id = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000)
    return () => window.clearInterval(id)
  }, [active])
  const from = source === 'novel' ? 'the attached novel' : "this title's source lines"

  const cancel = () => {
    // Stop a paid extraction nobody is waiting for. Closing waits for the answer, so a
    // cancel that failed (the scan keeps running and billing) is shown, not hidden.
    // Run-scoped: a newer run started meanwhile is refused (409), not cancelled.
    if (!active || !cur?.run_id) return onCancel()
    setBusy(true)
    GLOSSARY_API[source].cancel(dramaId, cur.run_id).then(
      () => {
        bumpGlossaryRun(source)
        onCancel()
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }

  const startTranslation = () => {
    if (!chosen.length) return onStart(null)
    setBusy(true)
    setProblem(null)
    const overrides = buildOverrides(proposals, chosen, edits)
    GLOSSARY_API[source]
      .apply(dramaId, applyRequest(chosen, run, overrides))
      .then(
        (r) => {
          bumpGlossaryTerms()
          onStart(applySummary(r))
        },
        (e: unknown) => {
          const t = glossaryApplyErrorText(e)
          if (t) setProblem(t)
          else setError(e)
          setBusy(false)
          // Replaced by another run (another tab or panel): review that one.
          if (e instanceof ApiError && e.status === 409) adoptLatestRun()
        },
      )
  }

  const adoptLatestRun = () =>
    GLOSSARY_API[source].get(dramaId).then(
      (s) => {
        if (s.run_id) setStarted({ runId: s.run_id, failed: false })
        bumpGlossaryRun(source)
      },
      () => undefined,
    )

  const edit = <K extends keyof ProposalValues>(p: NovelGlossaryProposal, field: K, value: ProposalValues[K]) =>
    setEdits((c) => editProposal(c, p, field, value))

  return (
    <div className="novel-glossary" data-testid="glossary-review">
      <p className="muted">
        Proposes terms from {from} using this title's saved engine ({engine}), whatever the engine list above shows. Uncheck anything wrong; the checked terms are
        added to the series glossary before the run starts.
      </p>
      {!fresh && <p className="muted" role="status">Starting…</p>}
      {active && cur && (
        <p role="status" data-testid="glossary-review-running">
          {extractionProgressText(source, cur.status, cur.progress)}
          {' '}This can take a few minutes{elapsed !== null ? ` (${formatElapsed(elapsed)} elapsed)` : ''}.
        </p>
      )}
      {resume && cur?.status === 'done' && proposals.length > 0 && (
        <p role="status" data-testid="glossary-review-resumed">
          Found {proposals.length} {proposals.length === 1 ? 'term' : 'terms'} from your last scan. They stay here until you add or dismiss them, or the app restarts.
        </p>
      )}
      {startProblem && <p className="error" role="alert">{startProblem}</p>}
      {cur?.status === 'error' && (
        <p className="error" role="alert">
          {safeDetail(cur.message) ?? 'Extraction failed. Details are in the app log.'}
        </p>
      )}
      {cur?.status === 'cancelled' && <p className="muted">Extraction was cancelled.</p>}
      {cur?.status === 'done' && proposals.length === 0 && (
        <p className="muted">No new terms proposed; the run will use the glossary as it is.</p>
      )}
      {proposals.length > 0 && (
        <GlossaryProposals
          proposals={proposals}
          selected={sel}
          onToggle={(t) => setPicked(toggleTerm(sel, t))}
          edits={edits}
          onEdit={edit}
          catalogues={catalogues}
          isPhone={isPhone}
          testId="glossary-review"
        />
      )}
      <div className="novel-glossary-apply">
        <div className="actions">
          {fresh && !active && (
            <button
              type="button"
              className="primary"
              disabled={busy || missing.length > 0}
              onClick={startTranslation}
            >
              {startTranslationLabel(chosen.length)}
            </button>
          )}
          <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={cancel}>
            Cancel
          </button>
          {missing.length > 0 && <span className="muted">{missingTranslationText(missing)}</span>}
        </div>
        {failed && !startProblem && (
          <p className="muted">The extraction could not start; you can still start the translation.</p>
        )}
      </div>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner
        error={error ?? loadError}
        onDismiss={() => {
          setError(null)
          clearError()
        }}
      />
    </div>
  )
}
