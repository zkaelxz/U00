import { useEffect, useRef, useState } from 'react'

import { cancelJob } from '../../../api/jobs'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import { useStage } from '../StageContext'
import {
  applySummary,
  chosenTerms,
  defaultTermSelection,
  isActiveStatus,
  novelGlossaryApplyErrorText,
  toggleTerm,
} from './autotuneGlossary'
import {
  buildOverrides,
  editProposal,
  extractionProgressText,
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
} from './useGlossaryRun'
import './autotuneGlossary.css'

interface Props {
  // Start the translate run; note summarizes what was added to the glossary.
  onStart: (note: string | null) => void
  onCancel: () => void
}

// Parity X28, "Review glossary before translating": the Translate button
// first runs a glossary extraction (from the novel when one is attached,
// else from the source lines), shows the proposals, and then either adds
// the checked terms and starts the run, or cancels. Mounted once per press
// (keyed by the caller), so each press extracts afresh.
export function GlossaryReview({ onStart, onCancel }: Props) {
  const { dramaId, drama } = useStage()
  const hasNovel = useHasNovel(dramaId, drama)
  return (
    <section className="panel glossary-review" aria-label="Review glossary">
      <h4>Review glossary before translating</h4>
      {hasNovel === null ? (
        <p className="muted" role="status">Checking for a novel…</p>
      ) : (
        <ReviewBody source={reviewSource(hasNovel)} onStart={onStart} onCancel={onCancel} />
      )}
    </section>
  )
}

function ReviewBody({ source, onStart, onCancel }: Props & { source: GlossarySource }) {
  const { dramaId, drama } = useStage()
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, clearError } = useGlossaryRun(dramaId, source)
  // The status read when the start settled: a result is only shown once a
  // newer read arrives, never an earlier run's proposals.
  const [startedAt, setStartedAt] = useState<{ status: object | null } | 'pending'>('pending')
  const [startProblem, setStartProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [picked, setPicked] = useState<Set<string> | null>(null)
  const [edits, setEdits] = useState<Edits>({})
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const statusRef = useRef(status)
  useEffect(() => {
    statusRef.current = status
  })
  const startedRef = useRef(false)
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    startExtraction(dramaId, source).then((r) => {
      setStartProblem(r.problem)
      setError(r.error)
      setStartedAt({ status: statusRef.current })
    })
  }, [dramaId, source])

  const failed = startProblem !== null || error !== null
  const fresh = startedAt !== 'pending' && (failed || status !== startedAt.status)
  const cur = fresh && !failed ? status : null
  const active = isActiveStatus(cur?.status)
  const proposals: NovelGlossaryProposal[] = cur?.status === 'done' ? cur.proposals ?? [] : []
  const sel = picked ?? defaultTermSelection(proposals)
  const chosen = chosenTerms(proposals, sel)
  const missing = missingTranslations(proposals, chosen, edits)
  const catalogues = useGlossaryCatalogues(proposals.length > 0)
  const engine = drama.translation_engine || 'claude'
  const from = source === 'novel' ? 'the attached novel' : "this drama's source lines"

  const cancel = () => {
    // Stop a paid extraction nobody is waiting for.
    if (active && cur) cancelJob(cur.job_id).then(() => bumpGlossaryRun(source), () => undefined)
    onCancel()
  }

  const startTranslation = () => {
    if (!chosen.length) return onStart(null)
    setBusy(true)
    setProblem(null)
    const overrides = buildOverrides(proposals, chosen, edits)
    GLOSSARY_API[source]
      .apply(dramaId, { terms: chosen, ...(overrides ? { overrides } : {}) })
      .then(
        (r) => {
          bumpGlossaryTerms()
          onStart(applySummary(r))
        },
        (e: unknown) => {
          const t = novelGlossaryApplyErrorText(e)
          if (t) setProblem(t)
          else setError(e)
          setBusy(false)
        },
      )
  }

  const edit = <K extends keyof ProposalValues>(p: NovelGlossaryProposal, field: K, value: ProposalValues[K]) =>
    setEdits((c) => editProposal(c, p, field, value))

  return (
    <div className="novel-glossary" data-testid="glossary-review">
      <p className="muted">
        Proposes terms from {from} using this drama's engine ({engine}). Uncheck anything wrong; the checked terms are
        added to the series glossary before the run starts.
      </p>
      {!fresh && <p className="muted" role="status">Starting…</p>}
      {active && cur && (
        <p role="status" data-testid="glossary-review-running">
          {extractionProgressText(source, cur.status, cur.progress)}
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
          <button type="button" disabled={busy} onClick={cancel}>
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
