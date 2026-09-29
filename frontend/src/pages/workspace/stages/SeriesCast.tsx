import { useEffect, useState } from 'react'

import { addSeriesPerson, updateSeriesPerson } from '../../../api/seriesPeople'
import { deleteSeriesCharacter, listSeriesCharacters } from '../../../api/stageDeletes'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import type { SeriesCharacter } from '../../../types/autotuneGlossary'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { PC_ONLY_DELETE_NOTE, usePcOnly } from '../../../hooks/usePcOnly'
import {
  CUSTOM,
  PRONOUN_PRESETS,
  buildPersonCreate,
  buildPersonUpdate,
  isPersonDirty,
  personProblem,
  toPersonForm,
  type PersonForm,
} from './seriesPeopleForm'
import './seriesCast.css'

const PRONOUNS_HELP =
  'Fixes this person\'s pronouns in translation for every drama in the series. ' +
  'A drama\'s own Characters table can override them for that drama.'

// Name, pronouns (preset or custom), aliases and notes: shared by the
// inline edit and the add form.
function PersonFields({ form, set }: {
  form: PersonForm
  set: <K extends keyof PersonForm>(k: K, v: PersonForm[K]) => void
}) {
  return (
    <div className="series-person-grid">
      <Field label="Name">
        <input value={form.character_name} maxLength={200} onChange={(e) => set('character_name', e.target.value)} />
      </Field>
      <Field label="Pronouns" help={PRONOUNS_HELP}>
        <select value={form.pronoun_choice} onChange={(e) => set('pronoun_choice', e.target.value)}>
          <option value="">Unspecified (use the default)</option>
          {PRONOUN_PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
          <option value={CUSTOM}>Custom…</option>
        </select>
      </Field>
      {form.pronoun_choice === CUSTOM && (
        <Field label="Custom pronouns">
          <input value={form.custom_pronouns} maxLength={40} placeholder="e.g. xe/xem" onChange={(e) => set('custom_pronouns', e.target.value)} />
        </Field>
      )}
      <Field label="Aliases" help="Nicknames and other spellings, separated by |. A Chinese alias lets Auto QC check this name.">
        <input value={form.aliases} maxLength={1000} onChange={(e) => set('aliases', e.target.value)} />
      </Field>
      <div className="series-person-wide">
        <Field label="Notes" help="Speaking style, relationships, anything worth remembering.">
          <input value={form.notes} maxLength={2000} onChange={(e) => set('notes', e.target.value)} />
        </Field>
      </div>
    </div>
  )
}

function useForm(initial: () => PersonForm) {
  const [form, setForm] = useState<PersonForm>(initial)
  const set = <K extends keyof PersonForm>(k: K, v: PersonForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  return { form, setForm, set }
}

function EditPerson({ seriesId, person, onSaved, onCancel }: {
  seriesId: number
  person: SeriesCharacter
  onSaved: (p: SeriesCharacter) => void
  onCancel: () => void
}) {
  const { form, set } = useForm(() => toPersonForm(person))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const changes = buildPersonUpdate(person, form)
  const dirty = isPersonDirty(changes)

  const save = () => {
    const p = personProblem(form)
    setProblem(p)
    if (p) return
    setBusy(true)
    setError(null)
    updateSeriesPerson(seriesId, person.id, changes).then(
      (saved) => {
        setBusy(false)
        onSaved(saved)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  return (
    <fieldset className="series-person-edit" aria-label={`Edit ${person.character_name}`}>
      <legend>Editing {person.character_name}</legend>
      <PersonFields form={form} set={set} />
      {problem && <p className="error" role="alert">{problem}</p>}
      <div className="actions">
        <button type="button" className="primary" disabled={!dirty || busy} title={dirty ? undefined : 'No changes to save.'} onClick={save}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </fieldset>
  )
}

function AddPerson({ seriesId, onAdded }: { seriesId: number; onAdded: (p: SeriesCharacter) => void }) {
  const { form, setForm, set } = useForm(() => toPersonForm())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)

  const add = () => {
    const p = personProblem(form)
    setProblem(p)
    if (p) return
    setBusy(true)
    setError(null)
    addSeriesPerson(seriesId, buildPersonCreate(form)).then(
      (saved) => {
        setBusy(false)
        setForm(toPersonForm())
        onAdded(saved)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  return (
    <fieldset className="series-person-add" aria-label="Add a person to the series">
      <legend>Add a person</legend>
      <PersonFields form={form} set={set} />
      {problem && <p className="error" role="alert">{problem}</p>}
      <div className="actions">
        <button type="button" disabled={busy || !form.character_name.trim()} title={form.character_name.trim() ? undefined : 'Type a name first.'} onClick={add}>
          {busy ? 'Adding…' : 'Add person'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </fieldset>
  )
}

const byName = (a: SeriesCharacter, b: SeriesCharacter) =>
  a.character_name < b.character_name ? -1 : a.character_name > b.character_name ? 1 : 0

// Characters → "Series cast (n)": the series-level people shared by every
// drama in the series. Adding and editing (name, pronouns, aliases, notes)
// work remotely (lines.edit); removing one is PC-only.
export function SeriesCast({ seriesId }: { seriesId: number }) {
  const [cast, setCast] = useState<SeriesCharacter[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [removeError, setRemoveError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const pc = usePcOnly()

  useEffect(() => {
    let cancelled = false
    listSeriesCharacters(seriesId).then(
      (c) => !cancelled && setCast(c),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [seriesId])

  const remove = (c: SeriesCharacter) => {
    setRemoveError(null)
    setNotice(null)
    deleteSeriesCharacter(seriesId, c.id).then(
      () => {
        setError(null)
        setNotice(`Removed ${c.character_name} from the series.`)
        setCast((cur) => cur && cur.filter((x) => x.id !== c.id))
      },
      setRemoveError,
    )
  }

  // Replace by id, never by position.
  const saved = (p: SeriesCharacter) => {
    setEditing(null)
    setNotice(`Saved ${p.character_name}.`)
    setCast((cur) => cur && cur.map((x) => (x.id === p.id ? p : x)).sort(byName))
  }
  const added = (p: SeriesCharacter) => {
    setNotice(`Added ${p.character_name} to the series.`)
    setCast((cur) => [...(cur ?? []).filter((x) => x.id !== p.id), p].sort(byName))
  }

  // Not loaded (or failed to load): only a load error.
  if (!cast) return <ErrorBanner error={error} onDismiss={() => setError(null)} />

  return (
    <Section
      storageKey="translate.characters.series"
      title="Series cast"
      count={cast.length}
      summary={cast.length ? 'shared by every drama in the series' : 'no one yet'}
    >
      {cast.length === 0 && <p className="muted">No one yet. Add the series' recurring people below.</p>}
      {cast.length > 0 && (
        <ul className="series-cast" data-testid="series-cast">
          {cast.map((c) => (
            <li key={c.id}>
              {editing === c.id ? (
                <EditPerson seriesId={seriesId} person={c} onSaved={saved} onCancel={() => setEditing(null)} />
              ) : (
                <>
                  <span>
                    <strong>{c.character_name}</strong>
                    {c.pronouns && <span className="muted"> · {c.pronouns}</span>}
                    {c.aliases && <span className="muted"> · also {c.aliases}</span>}
                    {c.notes && <span className="muted series-cast-notes">{c.notes}</span>}
                  </span>
                  <span className="actions">
                    <button type="button" aria-label={`Edit ${c.character_name}`} onClick={() => setEditing(c.id)}>
                      Edit
                    </button>
                    {pc === 'local' && (
                      <ConfirmButton
                        name={c.character_name}
                        label="Remove…"
                        confirmLabel={`Confirm remove ${c.character_name} from series`}
                        onConfirm={() => remove(c)}
                      />
                    )}
                  </span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
      {pc === 'remote' && cast.length > 0 && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
      <AddPerson seriesId={seriesId} onAdded={added} />
      {notice && <p role="status">{notice}</p>}
      <ErrorBanner error={removeError} describe={{ pcOnly: true }} onDismiss={() => setRemoveError(null)} />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
