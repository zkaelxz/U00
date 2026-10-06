import { useEffect, useState, type ReactNode } from 'react'

import { ApiError } from '../../../api/client'
import { dismissGlossaryTerms, getGlossaryDismissals, restoreGlossaryTerms } from '../../../api/autotuneGlossary'
import { getGlossaryTerms } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { Field } from '../../../components/Field'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import { useStage } from '../StageContext'
import {
  addTermsLabel,
  applySummary,
  chosenTerms,
  countInGlossary,
  defaultTermSelection,
  highConfidenceTerms,
  isActiveStatus,
  overwriteConfirmText,
  toggleTerm,
  type Blocker,
} from './autotuneGlossary'
import {
  SOURCE_TEXT,
  SUGGEST_HELP,
  suggestButtonLabel,
  applyRequest,
  buildOverrides,
  editProposal,
  extractionProgressText,
  glossaryApplyErrorText,
  missingTranslationText,
  missingTranslations,
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
  useRunScoped,
} from './useGlossaryRun'
import './autotuneGlossary.css'

interface Props {
  source: GlossarySource
  // Why this source can't run; null = ready. hasSource is null while read.
  blocker: Blocker | null
  ready: boolean
  // The source picker, rendered in the bar so its button starts this run.
  picker: ReactNode
  // Reasons for the other sources that can't run, shown under the bar.
  reasons: ReactNode
  // Terms already in the glossary: the button then reads "more terms".
  hasTerms: boolean
  // The empty-state card asked for a run; onStartHandled clears the request.
  startRequested: boolean
  onStartHandled: () => void
}

// Glossary > Suggest terms: asks the drama's translation engine to propose
// terms from the transcript or the attached novel, then adds the checked
// ones (with any edits) to the series glossary, matched by term text, never
// by position. After adding, bumpGlossaryTerms() makes the Glossary table
// re-read.
export function GlossaryExtract({ source, blocker, ready, picker, reasons, hasTerms, startRequested, onStartHandled }: Props) {
  const { dramaId } = useStage()
  const text = SOURCE_TEXT[source]
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, clearError } = useGlossaryRun(dramaId, source)
  // Selection, edits and a pending overwrite confirm belong to the run whose
  // proposals are shown; a new run (from any panel or tab) starts them over.
  const run = status?.run_id ?? null
  // null = the default selection for the current proposals.
  const [picked, setPicked] = useRunScoped<Set<string> | null>(run, null)
  const [edits, setEdits] = useRunScoped<Edits>(run, {})
  const [overwrite, setOverwrite] = useState(false)
  // From novel only; not remembered, so a paid re-run is always a choice.
  const [fresh, setFresh] = useState(false)
  const [confirming, setConfirming] = useRunScoped(run, false)
  // Chosen terms already in the series glossary, re-read when confirming
  // an overwrite; null while reading or if the read failed.
  const [existing, setExisting] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  // The series' ignore list (null while unread or if the read failed).
  const [ignored, setIgnored] = useState<string[] | null>(null)
  const [showIgnored, setShowIgnored] = useState(false)
  const readIgnored = () =>
    getGlossaryDismissals(dramaId).then(
      (r) => setIgnored(r.dismissals.map((d) => d.term)),
      () => setIgnored(null),
    )
  useEffect(() => {
    readIgnored()
  }, [dramaId])

  const active = isActiveStatus(status?.status)
  const proposals: NovelGlossaryProposal[] = status?.status === 'done' ? status.proposals ?? [] : []
  const sel = picked ?? defaultTermSelection(proposals)
  const chosen = chosenTerms(proposals, sel)
  const missing = missingTranslations(proposals, chosen, edits)
  const catalogues = useGlossaryCatalogues(proposals.length > 0)

  const start = () => {
    setBusy(true)
    setError(null)
    setProblem(null)
    setNote(null)
    setPicked(null)
    setEdits({})
    setConfirming(false)
    startExtraction(dramaId, source, { fresh: source === 'novel' && fresh })
      .then((r) => {
        setProblem(r.problem)
        setError(r.error)
      })
      .finally(() => setBusy(false))
  }

  // Runs once per request, also when picking the source remounted this panel.
  useEffect(() => {
    if (!startRequested) return
    onStartHandled()
    if (!blocker && ready && !active) start()
    // start() reads the current render's state; only a new request should fire it.
  }, [startRequested])

  // Cancel stays disabled after a press until the next poll brings new status.
  const [cancelSentFor, setCancelSentFor] = useState<object | null>(null)
  const cancel = () => {
    // Run-scoped: the server refuses (409) when a newer run is held.
    if (!status?.run_id) return
    setCancelSentFor(status)
    GLOSSARY_API[source].cancel(dramaId, status.run_id).then(
      () => bumpGlossaryRun(source),
      (e: unknown) => {
        setCancelSentFor(null)
        setError(e)
      },
    )
  }

  const apply = () => {
    setBusy(true)
    setConfirming(false)
    setProblem(null)
    const overrides = buildOverrides(proposals, chosen, edits)
    GLOSSARY_API[source]
      .apply(dramaId, applyRequest(chosen, run, overrides, overwrite))
      .then(
        (r) => {
          setError(null)
          setNote(applySummary(r))
          setPicked(new Set(chosen.filter((t) => !r.added.includes(t) && !r.overwritten.includes(t))))
          bumpGlossaryTerms()
        },
        (e: unknown) => {
          const t = glossaryApplyErrorText(e)
          if (t) setProblem(t)
          else setError(e)
          // Replaced by another run: show that run's proposals to review.
          if (e instanceof ApiError && e.status === 409) bumpGlossaryRun(source)
        },
      )
      .finally(() => setBusy(false))
  }

  // Overwrite always asks first; the count comes from the glossary as it is now.
  const onAdd = () => {
    if (!overwrite) return apply()
    setExisting(null)
    setConfirming(true)
    getGlossaryTerms(dramaId).then(
      (terms) => setExisting(countInGlossary(chosen, terms.map((t) => t.term_original))),
      () => setExisting(null),
    )
  }
  // The server stops listing ignored terms, so re-read the run and the list.
  const changeIgnored = (change: (terms: string[]) => Promise<unknown>, terms: string[]) =>
    change(terms).then(
      () => {
        setError(null)
        bumpGlossaryRun(source)
        return readIgnored()
      },
      (e: unknown) => setError(e),
    )
  const highTerms = highConfidenceTerms(proposals)
  const toggle = (term: string) => {
    setConfirming(false)
    setPicked(toggleTerm(sel, term))
  }
  const edit = <K extends keyof ProposalValues>(p: NovelGlossaryProposal, field: K, value: ProposalValues[K]) => {
    setConfirming(false)
    setEdits((cur) => editProposal(cur, p, field, value))
  }

  return (
    <div className="novel-glossary suggest-terms" data-testid={text.testId}>
      <div className="suggest-bar">
        <span className="suggest-bar-label">Suggest terms from</span>
        {picker}
        {!active && (
          <button type="button" className={buttonClass('secondary')} disabled={!!blocker || !ready || busy} onClick={start}>
            {suggestButtonLabel(proposals.length > 0 || hasTerms)}
          </button>
        )}
      </div>
      <p className="muted">{SUGGEST_HELP}</p>
      {reasons}
      {active && status && (
        <p className="actions" role="status" data-testid={`${text.testId}-running`}>
          <span>{extractionProgressText(source, status.status, status.progress)}</span>
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={cancelSentFor === status || !status.run_id} onClick={cancel}>
            Cancel
          </button>
        </p>
      )}
      {!active && source === 'novel' && (
        <div className="setting-list">
          <Field
            label="Fresh suggestions"
            help="Ask the model again instead of reusing the suggestions saved from the last run over this novel (costs a new run)."
          >
            <Toggle checked={fresh} onChange={setFresh} />
          </Field>
        </div>
      )}
      {status?.status === 'error' && (
        <p className="error" role="alert">
          {safeDetail(status.message) ?? 'Extraction failed. Details are in the app log.'}
        </p>
      )}
      {status?.status === 'cancelled' && <p className="muted">Extraction was cancelled.</p>}
      {status?.status === 'done' && proposals.length === 0 && <p className="muted">No new terms were found.</p>}
      {(proposals.length > 0 || (ignored?.length ?? 0) > 0) && (
        <div className="actions">
          {proposals.length > 0 && (
            <button
              type="button"
              className={buttonClass('secondary')}
              disabled={highTerms.size === 0}
              onClick={() => {
                setConfirming(false)
                setPicked(new Set(highTerms))
              }}
            >
              Select all High ({highTerms.size})
            </button>
          )}
          {(ignored?.length ?? 0) > 0 && (
            <button type="button" className={buttonClass('secondary')} aria-expanded={showIgnored} onClick={() => setShowIgnored(!showIgnored)}>
              Ignored ({ignored?.length})
            </button>
          )}
        </div>
      )}
      {showIgnored && ignored && ignored.length > 0 && (
        <ul className="novel-glossary-cards" data-testid={`${text.testId}-ignored`}>
          {ignored.map((t) => (
            <li key={t}>
              <strong>{t}</strong>{' '}
              <button
                type="button"
                className={buttonClass('secondary')}
                aria-label={`Restore ${t}`}
                onClick={() => changeIgnored((x) => restoreGlossaryTerms(dramaId, x), [t])}
              >
                Restore
              </button>
            </li>
          ))}
        </ul>
      )}
      {proposals.length > 0 && (
        <GlossaryProposals
          proposals={proposals}
          selected={sel}
          onToggle={toggle}
          edits={edits}
          onEdit={edit}
          catalogues={catalogues}
          isPhone={isPhone}
          testId={text.testId}
          onIgnore={(t) => changeIgnored((x) => dismissGlossaryTerms(dramaId, x), [t])}
        />
      )}
      {proposals.length > 0 && (
        <div className="novel-glossary-apply">
          <div className="setting-list">
            <Field label="Overwrite existing terms">
              <Toggle
                checked={overwrite}
                onChange={(v) => {
                  setConfirming(false)
                  setOverwrite(v)
                }}
              />
            </Field>
          </div>
          {overwrite && (
            <p className="muted">Tick terms marked "Already in glossary" to replace them.</p>
          )}
          {!confirming ? (
            <div className="actions">
              <button
                type="button"
                className="primary"
                disabled={chosen.length === 0 || missing.length > 0 || busy}
                onClick={onAdd}
              >
                {addTermsLabel(chosen.length)}
              </button>
              {chosen.length === 0 && <span className="muted">Select terms to add.</span>}
              {missing.length > 0 && <span className="muted">{missingTranslationText(missing)}</span>}
            </div>
          ) : (
            <div className="actions" role="alert">
              <span>{overwriteConfirmText(existing)}</span>
              <button type="button" className={buttonClass('danger')} disabled={busy} onClick={apply}>
                Yes, overwrite
              </button>
              <button type="button" className={buttonClass('secondary')} onClick={() => setConfirming(false)}>Cancel</button>
            </div>
          )}
        </div>
      )}
      {note && <p role="status">{note}</p>}
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
