/*
 * DramaPicker: "Import into" select for the Sources imports, with an
 * optional "New drama…" entry that creates one (POST /api/dramas) with a
 * media type the import accepts, then selects it.
 *
 *   const dramas = useDramaList()   // useDramaList.ts
 *   <DramaPicker dramas={chapterImportDramas(dramas.items, comic)} value={id} onChange={setId}
 *                newDrama={{ title, language, comic }} onCreated={dramas.add} />
 */
import { useState } from 'react'

import { createDrama } from '../../api/library'
import type { DramaSummary } from '../../api/types'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { humanize } from '../../components/labels'
import { buttonClass } from '../../components/uiClasses'
import { SOURCE_LANGUAGES } from '../libraryForm'
import { dramaLabel, newDramaRequest } from './urlImportFormat'

const NEW = 'new'

type Props = {
  dramas: DramaSummary[] | null
  value: number | null
  onChange: (id: number | null) => void
  // Offer "New drama…": the prefilled title and language, and whether it is a comic.
  newDrama?: { title: string; language: string | null; comic: boolean }
  onCreated?: (d: DramaSummary) => void
  disabled?: boolean
  help?: string
}

export function DramaPicker({ dramas, value, onChange, newDrama, onCreated, disabled, help }: Props) {
  const [creating, setCreating] = useState(false)
  const [title, setTitle] = useState(newDrama?.title ?? '')
  const [language, setLanguage] = useState(() => newDramaRequest('', newDrama?.language, false).source_language)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const choose = (v: string) => {
    if (v === NEW) {
      setCreating(true)
      return
    }
    setCreating(false)
    onChange(v ? Number(v) : null)
  }

  const create = () => {
    if (!newDrama || !title.trim()) return
    setBusy(true)
    setError(null)
    createDrama(newDramaRequest(title, language, newDrama.comic)).then(
      (d) => {
        setBusy(false)
        setCreating(false)
        onCreated?.(d)
        onChange(d.id)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  const kind = newDrama?.comic ? 'comic' : 'novel'
  return (
    <div className="drama-picker">
      <Field label="Import into" help={help}>
        <select
          value={creating ? NEW : value ? String(value) : ''}
          disabled={disabled || !dramas}
          onChange={(e) => choose(e.target.value)}
        >
          <option value="">{dramas ? 'Choose a drama…' : 'Loading…'}</option>
          {(dramas ?? []).map((d) => (
            <option key={d.id} value={d.id}>
              {dramaLabel(d)}
            </option>
          ))}
          {newDrama && <option value={NEW}>New drama…</option>}
        </select>
      </Field>
      {dramas && dramas.length === 0 && !creating && (
        <p className="muted">{newDrama ? `No ${kind} dramas yet. Choose “New drama…” to make one.` : 'No dramas can take this yet.'}</p>
      )}
      {creating && newDrama && (
        <div className="drama-new" role="group" aria-label="New drama">
          <Field label="Title">
            <input type="text" value={title} maxLength={300} onChange={(e) => setTitle(e.target.value)} />
          </Field>
          <Field label="Language">
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {SOURCE_LANGUAGES.map((l) => (
                <option key={l} value={l}>
                  {humanize('language', l)}
                </option>
              ))}
            </select>
          </Field>
          <p className="muted">
            Made as a {humanize('mediaType', newDramaRequest('', language, newDrama.comic).media_type).toLowerCase()} drama.
          </p>
          <div className="actions">
            <button type="button" className={buttonClass('secondary')} disabled={busy || !title.trim()} onClick={create}>
              {busy ? 'Creating…' : 'Create drama'}
            </button>
            <button type="button" className={buttonClass('ghost')} onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
          <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
        </div>
      )}
    </div>
  )
}
