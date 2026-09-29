import { useState } from 'react'

import { ApiError } from '../../../api/client'
import { getGlossaryTerms } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { Section } from '../../../components/Section'
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
  isActiveStatus,
  overwriteConfirmText,
  novelGlossaryBlocker,
  toggleTerm,
} from './autotuneGlossary'
import {
  SOURCE_TEXT,
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
  useHasNovel,
  useRunScoped,
} from './useGlossaryRun'
import './autotuneGlossary.css'

interface Props {
  source: GlossarySource
  // Section title and remembered open state (defaults per source).
  title?: string
  storageKey?: string
}

// Glossary → "From novel" / "From lines": asks the drama's translation
// engine to propose terms from the attached novel or the source lines, then
// adds the checked ones (with any edits) to the series glossary, matched by
// term text, never by position. After adding, bumpGlossaryTerms() makes the
// Glossary table re-read.
export function GlossaryExtract({ source, title, storageKey }: Props) {
  const { dramaId, drama } = useStage()
  const text = SOURCE_TEXT[source]
  const isPhone = useMediaQuery('(max-width: 640px)')
  const { status, error: loadError, clearError } = useGlossaryRun(dramaId, source)
  const hasNovel = useHasNovel(dramaId, drama, source === 'novel')
  // Selection, edits and a pending overwrite confirm belong to the run whose
  // proposals are shown; a new run (from any panel or tab) starts them over.
  const run = status?.run_id ?? null
  // null = the default selection for the current proposals.
  const [picked, setPicked] = useRunScoped<Set<string> | null>(run, null)
  const [edits, setEdits] = useRunScoped<Edits>(run, {})
  const [overwrite, setOverwrite] = useState(false)
  const [confirming, setConfirming] = useRunScoped(run, false)
  // Chosen terms already in the series glossary, re-read when confirming
  // an overwrite; null while reading or if the read failed.
  const [existing, setExisting] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const active = isActiveStatus(status?.status)
  const blocker = novelGlossaryBlocker(dramaId, drama.series_id, hasNovel !== false)
  const proposals: NovelGlossaryProposal[] = status?.status === 'done' ? status.proposals ?? [] : []
  const sel = picked ?? defaultTermSelection(proposals)
  const chosen = chosenTerms(proposals, sel)
  const missing = missingTranslations(proposals, chosen, edits)
  const catalogues = useGlossaryCatalogues(proposals.length > 0)
  const engine = humanize('engine', drama.translation_engine || 'claude')

  const start = () => {
    setBusy(true)
    setError(null)
    setProblem(null)
    setNote(null)
    setPicked(null)
    setEdits({})
    setConfirming(false)
    startExtraction(dramaId, source)
      .then((r) => {
        setProblem(r.problem)
        setError(r.error)
      })
      .finally(() => setBusy(false))
  }

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
  const toggle = (term: string) => {
    setConfirming(false)
    setPicked(toggleTerm(sel, term))
  }
  const edit = <K extends keyof ProposalValues>(p: NovelGlossaryProposal, field: K, value: ProposalValues[K]) => {
    setConfirming(false)
    setEdits((cur) => editProposal(cur, p, field, value))
  }

  const summary = active
    ? 'running'
    : proposals.length
      ? `${proposals.length} proposed`
      : `uses ${engine}`

  return (
    <Section storageKey={storageKey ?? text.storageKey} title={title ?? text.title} summary={summary}>
      <div className="novel-glossary" data-testid={text.testId}>
        <p className="muted">{text.intro(engine)}</p>
        {active && status ? (
          <p className="actions" role="status" data-testid={`${text.testId}-running`}>
            <span>{extractionProgressText(source, status.status, status.progress)}</span>
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={cancelSentFor === status || !status.run_id} onClick={cancel}>
              Cancel
            </button>
          </p>
        ) : (
          <div className="actions">
            <button type="button" className={buttonClass('secondary')} disabled={!!blocker || hasNovel === null || busy} onClick={start}>
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
              <p className="muted">Tick terms marked "already in glossary" to replace them.</p>
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
    </Section>
  )
}

type PanelProps = Omit<Props, 'source'>

// Glossary → "From novel" (also mounted on the Source stage, parity T02).
export function NovelGlossary(props: PanelProps) {
  return <GlossaryExtract source="novel" {...props} />
}

// Glossary → "From lines" (parity X10).
export function LinesGlossary(props: PanelProps) {
  return <GlossaryExtract source="lines" {...props} />
}
