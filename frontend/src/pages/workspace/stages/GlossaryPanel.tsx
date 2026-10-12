import { useEffect, useRef, useState, type ReactNode } from 'react'

import {
  deleteGlossaryTerms,
  getGlossaryCatalogues,
  getGlossaryTerms,
  getInstructions,
  getSpellingSuggestions,
  saveGlossaryTerm,
} from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { Tabs } from '../../../components/Tabs'
import { tabButtonId, tabPanelId } from '../../../components/tabIds'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { GlossaryCatalogues, GlossaryTerm } from '../../../types/translateStage'
import { splitLines, type RunForm } from '../translateForm'
import { pruneSelection, selectedInOrder, toggleAll, toggleId } from './glossarySelection'
import { useStage, useStageFocus } from '../StageContext'
import { GlossaryImport } from './GlossaryImport'
import { InstructionsEditor } from './GlossaryInstructions'
import { GlossaryRetranslate } from './GlossaryRetranslate'
import { SuggestSpellings } from './GlossarySpellings'
import { PossibleNames, SimilarSpellings } from './GlossaryVariants'
import { NameReportPanel } from './NameReportPanel'
import { VtuberNames } from './VtuberNames'
import { LanguagePacks } from './LanguagePacks'
import { SeriesAssign } from './SeriesAssign'
import { SuggestTerms, useSuggestSources } from './SuggestTerms'
import { startCardSuggestLabel, type GlossarySource } from './glossaryExtract'
import { scanBanner, scanSummary, sourceToShow } from './glossaryScan'
import { GLOSSARY_TABS, emptyTabText, instructionsSummary, type GlossaryTab } from './glossaryTabs'
import { loadGlossaryUi, saveGlossaryUi } from './glossaryUi'
import { useGlossaryScans, useGlossaryTermsVersion } from './useGlossaryRun'

interface TermForm {
  id?: number
  term_original: string
  term_translation: string
  notes: string
  category: string
  policy: string
  enforce_exact: boolean
  aliases: string
  banned: string
}

const EMPTY: TermForm = {
  term_original: '', term_translation: '', notes: '', category: '', policy: '',
  enforce_exact: false, aliases: '', banned: '',
}

const toForm = (t: GlossaryTerm): TermForm => ({
  id: t.id,
  term_original: t.term_original,
  term_translation: t.term_translation,
  notes: t.notes,
  category: t.category ?? '',
  policy: t.policy ?? '',
  enforce_exact: t.enforce_exact,
  aliases: t.aliases.join('\n'),
  banned: t.banned_translations.join('\n'),
})

function TermEditor({ initial, catalogues, onSave, onCancel }: {
  initial: TermForm
  catalogues: GlossaryCatalogues | null
  onSave: (f: TermForm) => void
  onCancel: () => void
}) {
  const [f, setF] = useState(initial)
  const [problem, setProblem] = useState<string | null>(null)
  const set = <K extends keyof TermForm>(k: K, v: TermForm[K]) => setF((s) => ({ ...s, [k]: v }))
  const submit = () => {
    if (!f.term_original.trim() || !f.term_translation.trim()) {
      return setProblem('Both the original and the translation are required.')
    }
    setProblem(null)
    onSave(f)
  }
  return (
    <fieldset className="term-form">
      <legend>{f.id ? 'Edit term' : 'Add term'}</legend>
      <Field label="Original"><input value={f.term_original} onChange={(e) => set('term_original', e.target.value)} /></Field>
      <Field label="Translation"><input value={f.term_translation} onChange={(e) => set('term_translation', e.target.value)} /></Field>
      <Field label="Notes"><input value={f.notes} onChange={(e) => set('notes', e.target.value)} /></Field>
      {catalogues && (
        <>
          <Field label="Category">
            <select value={f.category} onChange={(e) => set('category', e.target.value)}>
              <option value="">None</option>
              {catalogues.term_categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select>
          </Field>
          <Field label="Policy">
            <select value={f.policy} onChange={(e) => set('policy', e.target.value)}>
              <option value="">None</option>
              {catalogues.term_policies.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </select>
          </Field>
        </>
      )}
      <Field label="Aliases" help="One per line.">
        <textarea rows={2} value={f.aliases} onChange={(e) => set('aliases', e.target.value)} />
      </Field>
      <Field label="Banned" help="Banned translations, one per line.">
        <textarea rows={2} value={f.banned} onChange={(e) => set('banned', e.target.value)} />
      </Field>
      <div className="setting-list">
        <Field label="Enforce exact">
          <Toggle checked={f.enforce_exact} onChange={(v) => set('enforce_exact', v)} />
        </Field>
      </div>
      {problem && <p className="error" role="alert">{problem}</p>}
      <div className="actions">
        <button type="button" className={buttonClass('primary')} onClick={submit}>Save term</button>
        <button type="button" className={buttonClass('secondary')} onClick={onCancel}>Cancel</button>
      </div>
    </fieldset>
  )
}

// What the Translate stage hands over so the Re-translate tab can use the run form's settings.
export interface RetranslateProps {
  f: RunForm
  busy: boolean
  onStarted: (jobId: string) => void
}

// The list loads when it opens, not when the page does: it reads every line.
function RetranslateTab({ retranslate }: { retranslate?: RetranslateProps | null }) {
  const [open, setOpen] = useState(false)
  if (!retranslate) return <p className="muted" role="status">Loading the translate options…</p>
  return (
    <>
      <div className="actions">
        <button type="button" className={`${buttonClass('secondary')} glossary-retranslate-open`} disabled={open} onClick={() => setOpen(true)}>
          Re-translate lines affected by the glossary…
        </button>
        <span className="muted">Uses the settings on the Translate form above.</span>
      </div>
      {open && (
        <GlossaryRetranslate
          f={retranslate.f}
          busy={retranslate.busy}
          onStarted={retranslate.onStarted}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  )
}

export function GlossaryPanel({ focusReady, retranslate }: { focusReady?: boolean; retranslate?: RetranslateProps | null }) {
  const { dramaId } = useStage()
  // Everything below belongs to one title; a different title starts clean.
  return <GlossaryBody key={dramaId} focusReady={focusReady} retranslate={retranslate} />
}

function GlossaryBody({ focusReady, retranslate }: { focusReady?: boolean; retranslate?: RetranslateProps | null }) {
  const { dramaId, drama } = useStage()
  const focusSignal = useStageFocus('glossary', 'translate-glossary', focusReady)
  const seriesId = drama.series_id ?? null
  const [remembered] = useState(() => loadGlossaryUi(dramaId))
  const [tab, setTabState] = useState<GlossaryTab>(remembered.tab ?? 'terms')
  const setTab = (t: GlossaryTab) => {
    setTabState(t)
    saveGlossaryUi(dramaId, { tab: t })
  }
  const [terms, setTerms] = useState<GlossaryTerm[] | null>(null)
  const [catalogues, setCatalogues] = useState<GlossaryCatalogues | null>(null)
  // seriesId is the series the text was read for: the series editor waits
  // for the new series' text so it can't save the old one over it.
  const [instructions, setInstructions] = useState<{ project: string; series: string; seriesId: number | null } | null>(null)
  const [editing, setEditing] = useState<TermForm | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [saveError, setSaveError] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  // Bumped when extracted proposals are added (From novel/lines, review).
  const termsVersion = useGlossaryTermsVersion()
  const [deleteFailure, setDeleteFailure] = useState<unknown>(null)
  const sources = useSuggestSources()
  const scans = useGlossaryScans(dramaId)
  // A source chosen on this visit; null follows a held scan, then the remembered source, then the default.
  const [userPick, setUserPick] = useState<GlossarySource | null>(null)
  const source = sourceToShow({ userPick, storedPick: remembered.source, scans: scans.scans, defaultSource: sources.defaultSource })
  const held = scans.loaded ? scanBanner(scans.scans, remembered.source) : null
  const heldSource = held?.source ?? null
  // A pending "suggest" request from the empty-state card.
  const [suggestRequested, setSuggestRequested] = useState(false)
  const [importSignal, setImportSignal] = useState(0)
  const [showNameReport, setShowNameReportState] = useState(remembered.nameReportOpen)
  const setShowNameReport = (open: boolean) => {
    setShowNameReportState(open)
    saveGlossaryUi(dramaId, { nameReportOpen: open })
  }
  const [similarFor, setSimilarFor] = useState<number | null>(null)
  const [suggestFor, setSuggestForState] = useState<number | null>(remembered.spellingTermId)
  const setSuggestFor = (id: number | null) => {
    setSuggestForState(id)
    saveGlossaryUi(dramaId, { spellingTermId: id })
  }

  // The chosen source is remembered once it is the person's choice or a scan lives there, so a
  // remount (leaving the page, a reload) opens on the same source rather than on the default.
  useEffect(() => {
    if (userPick) saveGlossaryUi(dramaId, { source: userPick })
    else if (heldSource) saveGlossaryUi(dramaId, { source: heldSource })
  }, [dramaId, userPick, heldSource])

  useEffect(() => {
    let cancelled = false
    getGlossaryTerms(dramaId).then(
      (t) => {
        if (cancelled) return
        setTerms(t)
        setSelected((cur) => pruneSelection(cur, t.map((x) => x.id)))
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads, termsVersion, seriesId])

  // The spelling suggestions live on the server; when this page was left and reopened, the panel
  // for the term they answered comes back with them.
  const spellingChecked = useRef(false)
  useEffect(() => {
    if (!terms || spellingChecked.current) return
    spellingChecked.current = true
    if (suggestFor !== null) return
    getSpellingSuggestions(dramaId).then(
      (r) => {
        const answered = r.status === 'done' ? r.result?.term : null
        const t = answered ? terms.find((x) => x.term_original === answered) : null
        if (t) {
          setSuggestForState(t.id)
          saveGlossaryUi(dramaId, { spellingTermId: t.id })
        }
      },
      () => undefined,
    )
  }, [dramaId, terms, suggestFor])

  useEffect(() => {
    let cancelled = false
    Promise.all([getGlossaryCatalogues(), getInstructions(dramaId)]).then(
      ([c, i]) => {
        if (cancelled) return
        setCatalogues(c)
        setInstructions({ project: i.project_instructions, series: i.series_instructions, seriesId })
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, seriesId])

  const save = (f: TermForm) =>
    saveGlossaryTerm(dramaId, {
      ...(f.id ? { id: f.id } : {}),
      term_original: f.term_original.trim(),
      term_translation: f.term_translation.trim(),
      notes: f.notes,
      category: f.category,
      policy: f.policy,
      enforce_exact: f.enforce_exact,
      aliases: splitLines(f.aliases),
      banned_translations: splitLines(f.banned),
    }).then(() => {
      setSaveError(null)
      setEditing(null)
      setReloads((n) => n + 1)
    }, setSaveError)

  const ids = terms ? terms.map((t) => t.id) : []
  const chosen = selectedInOrder(selected, ids)
  const allOn = ids.length > 0 && chosen.length === ids.length

  const remove = (termIds: number[]) => {
    setDeleting(true)
    setDeleteFailure(null)
    deleteGlossaryTerms(dramaId, termIds)
      .then(
        ({ deleted, not_found }) => {
          setConfirming(false)
          setDeleteError(
            not_found.length ? `${not_found.length} of ${termIds.length} term(s) were no longer in the glossary.` : null,
          )
          setSelected(new Set())
          setEditing((cur) => (cur?.id && deleted.includes(cur.id) ? null : cur))
          setReloads((n) => n + 1)
        },
        (e: unknown) => {
          setConfirming(false)
          setDeleteError(null)
          setDeleteFailure(e)
        },
      )
      .finally(() => setDeleting(false))
  }

  // Empty glossary of a drama in a series: one card to start from. Without a
  // series the Series section above is the first thing to do.
  const showStart = !!terms && terms.length === 0 && seriesId != null
  const hasTerms = !!terms && terms.length > 0
  const emptyCtx = { hasSeries: seriesId != null, termCount: terms ? terms.length : null }
  const tabs = GLOSSARY_TABS.map((t) =>
    t.id === 'find' && held ? { ...t, attention: held.kind === 'ready' ? held.count : 1 } : t,
  )
  const termsSummary = seriesId == null ? 'not in a series' : terms ? (terms.length ? `${terms.length} term${terms.length === 1 ? '' : 's'}` : 'no terms yet') : undefined
  const sectionSummary = [termsSummary, held ? scanSummary(held) : null].filter(Boolean).join(', ') || undefined
  const panel = (id: GlossaryTab, children: ReactNode) => (
    <div
      id={tabPanelId('glossary', id)}
      role="tabpanel"
      aria-labelledby={tabButtonId('glossary', id)}
      className="stack glossary-panel"
      hidden={tab !== id}
    >
      {emptyTabText(id, emptyCtx) !== null && id !== 'terms' ? <p className="muted" role="status">{emptyTabText(id, emptyCtx)}</p> : children}
    </div>
  )
  const openNames = (open: () => void) => {
    open()
    setTab('names')
  }

  return (
    <Section
      storageKey="translate.glossary"
      openSignal={focusSignal}
      title="Glossary"
      count={terms?.length}
      summary={sectionSummary}
    >
      <div id="translate-glossary" role="region" aria-label="Glossary" className="stack">
      <Tabs label="Glossary" idPrefix="glossary" tabs={tabs} active={tab} onSelect={setTab} />
      {panel('terms', (
        <>
      <SeriesAssign />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {emptyTabText('terms', emptyCtx) && <p className="muted" role="status">{emptyTabText('terms', emptyCtx)}</p>}
      {showStart && !editing && (
        <div className="glossary-start" role="group" aria-label="Start your glossary" data-testid="glossary-start">
          <h4>Start your glossary</h4>
          <p className="muted">Terms belong to the title's series.</p>
          <div className="glossary-start-actions">
            <button
              type="button"
              className={buttonClass('primary')}
              disabled={!!sources.blockers[source] || !sources.ready}
              onClick={() => {
                setUserPick(source)
                setSuggestRequested(true)
                setTab('find')
              }}
            >
              {startCardSuggestLabel(source)}
            </button>
            <button type="button" className={buttonClass('secondary')} onClick={() => setEditing(EMPTY)}>Add a term</button>
            <button
              type="button"
              className={buttonClass('secondary')}
              onClick={() => {
                setImportSignal((n) => n + 1)
                setTab('find')
              }}
            >
              Import a file
            </button>
          </div>
        </div>
      )}
      {terms && terms.length > 0 && (
        <div className="table-scroll"><table role="table" className="card-table glossary-table">
          <thead>
            <tr role="row">
              <th role="columnheader">
                <input
                  type="checkbox"
                  aria-label="Select all terms"
                  checked={allOn}
                  onChange={() => {
                    setConfirming(false)
                    setSelected((cur) => toggleAll(cur, ids))
                  }}
                />
              </th>
              <th role="columnheader">Original</th><th role="columnheader">Translation</th><th role="columnheader">Aliases</th><th role="columnheader">Banned</th><th role="columnheader">Exact</th><th role="columnheader"><span className="visually-hidden">Edit</span></th></tr>
          </thead>
          <tbody>
            {terms.map((t) => (
              <tr key={t.id} role="row" className="card-main">
                <td role="cell" className="card-check">
                  <input
                    type="checkbox"
                    aria-label={`Select ${t.term_original}`}
                    checked={selected.has(t.id)}
                    onChange={() => {
                      setConfirming(false)
                      setSelected((cur) => toggleId(cur, t.id))
                    }}
                  />
                </td>
                <td role="cell" data-label="Original" className="card-title">{t.term_original}</td>
                <td role="cell" data-label="Translation" className="card-wide">{t.term_translation}</td>
                <td role="cell" data-label="Aliases" className="card-meta">{t.aliases.join(', ')}</td>
                <td role="cell" data-label="Banned" className="card-meta">{t.banned_translations.join(', ')}</td>
                <td role="cell" data-label="Exact" className="card-meta">{t.enforce_exact ? 'Yes' : 'No'}</td>
                <td role="cell" className="card-action"><button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Edit ${t.term_original}`} onClick={() => setEditing(toForm(t))}>Edit</button>
                  <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Find similar spellings of ${t.term_original}`} title="Look in the source lines for other ways this name was written" onClick={() => openNames(() => setSimilarFor(similarFor === t.id ? null : t.id))}>Find similar</button>
                  <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Suggest spellings of ${t.term_original}`} title="Ask the engine how this name may have been written, then check it against the lines" onClick={() => openNames(() => setSuggestFor(suggestFor === t.id ? null : t.id))}>Suggest spellings</button></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {terms && terms.length > 0 && (
        <div className="glossary-bulk">
          {!confirming ? (
            <button
              type="button"
              className={buttonClass('danger', 'sm')}
              disabled={chosen.length === 0 || deleting}
              onClick={() => setConfirming(true)}
            >
              Delete selected{chosen.length ? ` (${chosen.length})` : ''}
            </button>
          ) : (
            <>
              <span role="alert">Delete {chosen.length} term{chosen.length === 1 ? '' : 's'} from the series glossary?</span>
              <button type="button" className={buttonClass('danger', 'sm')} disabled={deleting} onClick={() => remove(chosen)}>
                {deleting ? 'Deleting…' : 'Yes, delete'}
              </button>
              <button type="button" className={buttonClass('secondary', 'sm')} disabled={deleting} onClick={() => setConfirming(false)}>Cancel</button>
            </>
          )}
          {chosen.length === 0 && !confirming && <span className="muted">Select terms to delete.</span>}
          {deleteError && <p className="error" role="alert">{deleteError}</p>}
          <ErrorBanner error={deleteFailure} onDismiss={() => setDeleteFailure(null)} />
        </div>
      )}
      {editing ? (
        <TermEditor
          key={editing.id ?? 'new'}
          initial={editing}
          catalogues={catalogues}
          onSave={save}
          onCancel={() => setEditing(null)}
        />
      ) : !showStart && (
        <div className="actions">
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={seriesId == null}
            aria-describedby={seriesId == null ? 'glossary-add-reason' : undefined}
            onClick={() => setEditing(EMPTY)}
          >
            Add term
          </button>
          {seriesId == null && <span className="muted" id="glossary-add-reason">Still needed: a series (above).</span>}
        </div>
      )}
      <ErrorBanner error={saveError} onDismiss={() => setSaveError(null)} />
      <LanguagePacks
        glossarySources={(terms ?? []).map((t) => t.term_original)}
        hasSeries={seriesId != null}
        onAdded={() => setReloads((n) => n + 1)}
      />
      <VtuberNames />
        </>
      ))}
      {panel('find', (
        <>
      {scans.loaded ? (
        <SuggestTerms
          sources={sources}
          source={source}
          hasTerms={hasTerms}
          startRequested={suggestRequested}
          onStartHandled={() => setSuggestRequested(false)}
          onPick={setUserPick}
        />
      ) : (
        <p className="muted" role="status">Checking for a glossary scan…</p>
      )}
      <GlossaryImport openSignal={importSignal} hasTerms={hasTerms} onImported={() => setReloads((n) => n + 1)} />
        </>
      ))}
      {panel('names', (
        <>
      <PossibleNames hasSeries={seriesId != null} onCreated={() => setReloads((n) => n + 1)} />
      {similarFor === null && suggestFor === null && hasTerms && (
        <p className="muted">To look for other spellings of a term, use Find similar or Suggest spellings on its row in Terms.</p>
      )}
      {terms?.map((t) => t.id === similarFor && (
        <SimilarSpellings
          key={`similar-${t.id}-${termsVersion}`}
          term={t}
          onAdded={() => setReloads((n) => n + 1)}
          onClose={() => setSimilarFor(null)}
        />
      ))}
      {terms?.map((t) => t.id === suggestFor && (
        <SuggestSpellings
          key={`suggest-${t.id}-${termsVersion}`}
          term={t}
          initialEnglish={loadGlossaryUi(dramaId).english[String(t.id)]}
          onEnglish={(v) => saveGlossaryUi(dramaId, { english: { [String(t.id)]: v } })}
          onAdded={() => setReloads((n) => n + 1)}
          onClose={() => setSuggestFor(null)}
        />
      ))}
      {showNameReport ? (
        <NameReportPanel onClose={() => setShowNameReport(false)} />
      ) : (
        <div className="actions">
          <button
            type="button"
            className={buttonClass('secondary')}
            title="Find a name that is heard or written differently across all your titles"
            onClick={() => setShowNameReport(true)}
          >
            Check names across all titles
          </button>
        </div>
      )}
        </>
      ))}
      {panel('instructions', instructions ? (
        <>
          <p className="muted" data-testid="instructions-state">
            Project: {instructionsSummary(instructions.project)}
            {seriesId != null && instructions.seriesId === seriesId ? ` · Series: ${instructionsSummary(instructions.series)}` : ''}
          </p>
          <InstructionsEditor
            key="project"
            scope="project"
            initial={instructions.project}
            saved={instructions.project}
            defaultOpen
            onSaved={(text) => setInstructions((cur) => cur && { ...cur, project: text })}
          />
          {seriesId == null ? (
            <p className="muted">Series instructions need a series: add this title to one in the Terms tab.</p>
          ) : instructions.seriesId === seriesId && (
            <InstructionsEditor
              key={`series-${seriesId}`}
              scope="series"
              initial={instructions.series}
              saved={instructions.series}
              onSaved={(text) => setInstructions((cur) => cur && { ...cur, series: text })}
            />
          )}
        </>
      ) : <p className="muted" role="status">Loading instructions…</p>)}
      {panel('retranslate', <RetranslateTab retranslate={retranslate} />)}
      </div>
    </Section>
  )
}
