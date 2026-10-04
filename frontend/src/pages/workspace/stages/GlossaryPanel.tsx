import { useEffect, useState } from 'react'

import {
  deleteGlossaryTerms,
  getGlossaryCatalogues,
  getGlossaryTerms,
  getInstructions,
  saveGlossaryTerm,
  saveInstructions,
} from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { GlossaryCatalogues, GlossaryTerm } from '../../../types/translateStage'
import { splitLines } from '../translateForm'
import { pruneSelection, selectedInOrder, toggleAll, toggleId } from './glossarySelection'
import { useStage } from '../StageContext'
import { GlossaryImport } from './GlossaryImport'
import { SeriesAssign } from './SeriesAssign'
import { LinesGlossary, NovelGlossary } from './NovelGlossary'
import { useGlossaryTermsVersion } from './useGlossaryRun'

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

function InstructionsEditor({ scope, initial }: { scope: 'project' | 'series'; initial: string }) {
  const { dramaId } = useStage()
  const [text, setText] = useState(initial)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const label = scope === 'project' ? 'Project instructions' : 'Series instructions'
  return (
    <div>
      <Field label={label}>
        <textarea
          rows={3}
          value={text}
          onChange={(e) => {
            setSaved(false)
            setText(e.target.value)
          }}
        />
      </Field>
      <button
        type="button"
        className={buttonClass('secondary', 'sm')}
        onClick={() =>
          saveInstructions(dramaId, scope, text).then(() => {
            setError(null)
            setSaved(true)
          }, setError)
        }
      >
        Save {scope} instructions
      </button>
      {saved && <span role="status"> Saved.</span>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}

export function GlossaryPanel() {
  const { dramaId, drama } = useStage()
  const seriesId = drama.series_id ?? null
  const [terms, setTerms] = useState<GlossaryTerm[] | null>(null)
  const [catalogues, setCatalogues] = useState<GlossaryCatalogues | null>(null)
  const [instructions, setInstructions] = useState<{ project: string; series: string } | null>(null)
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

  useEffect(() => {
    let cancelled = false
    Promise.all([getGlossaryCatalogues(), getInstructions(dramaId)]).then(
      ([c, i]) => {
        if (cancelled) return
        setCatalogues(c)
        setInstructions({ project: i.project_instructions, series: i.series_instructions })
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

  return (
    <Section
      storageKey="translate.glossary"
      title="Glossary"
      defaultOpen
      count={terms?.length}
      summary={seriesId == null ? 'not in a series' : terms ? (terms.length ? `${terms.length} term${terms.length === 1 ? '' : 's'}` : 'no terms yet') : undefined}
    >
      <div role="region" aria-label="Glossary">
      <SeriesAssign />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {terms && terms.length === 0 && seriesId != null && <p className="muted">No terms yet. They belong to the drama's series.</p>}
      {terms && terms.length > 0 && (
        <div className="table-scroll"><table>
          <thead>
            <tr>
              <th>
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
              <th>Original</th><th>Translation</th><th>Aliases</th><th>Banned</th><th>Exact</th><th /></tr>
          </thead>
          <tbody>
            {terms.map((t) => (
              <tr key={t.id}>
                <td>
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
                <td>{t.term_original}</td>
                <td>{t.term_translation}</td>
                <td>{t.aliases.join(', ')}</td>
                <td>{t.banned_translations.join(', ')}</td>
                <td>{t.enforce_exact ? 'Yes' : 'No'}</td>
                <td><button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Edit ${t.term_original}`} onClick={() => setEditing(toForm(t))}>Edit</button></td>
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
      ) : (
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
      <GlossaryImport hasTerms={!!terms && terms.length > 0} onImported={() => setReloads((n) => n + 1)} />
      <NovelGlossary />
      <LinesGlossary />
      {instructions && (
        <>
          <InstructionsEditor scope="project" initial={instructions.project} />
          <InstructionsEditor scope="series" initial={instructions.series} />
        </>
      )}
      </div>
    </Section>
  )
}
