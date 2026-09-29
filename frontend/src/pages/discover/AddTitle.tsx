/*
 * Discover > Add a title (DI09, DI10). Optionally fill the form from a
 * public page (the PC reads it and an AI suggests fields; nothing is saved
 * until "Add to catalogue"), then add it to the known-titles catalogue.
 * This adds a catalogue entry only; to pull chapters or episodes into a
 * drama, use Sources > Paste a link.
 */
import { useState, type FormEvent } from 'react'

import { createTitle, importSuggestion } from '../../api/discover'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import type { KnownTitleCreate } from '../../types/discover'
import { EMPTY_TITLE, LANGUAGES, TITLE_MEDIA_TYPES, applySuggestion, isHttpUrl, mediaLabel, titleFormProblems } from './discoverFormat'

export function AddTitle({ engine, aiReady, onAdded }: { engine: string; aiReady: boolean; onAdded: () => void }) {
  const [form, setForm] = useState<KnownTitleCreate>(EMPTY_TITLE)
  const [pageUrl, setPageUrl] = useState('')
  const [reading, setReading] = useState(false)
  const [readNote, setReadNote] = useState<{ text: string; warn: boolean } | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [touched, setTouched] = useState(false)

  const set = (k: keyof KnownTitleCreate) => (e: { target: { value: string } }) => {
    setForm((f) => ({ ...f, [k]: e.target.value }))
    setSaved(null)
  }

  async function readPage() {
    if (!isHttpUrl(pageUrl) || reading) return
    setReading(true)
    setError(null)
    setReadNote(null)
    try {
      const r = await importSuggestion(pageUrl.trim(), engine || undefined)
      if (r.found) {
        setForm((f) => applySuggestion(f, r.suggestion, pageUrl))
        setReadNote({ text: 'Filled in from the page. Check the fields, then add it.', warn: false })
      } else {
        setReadNote({ text: r.message || 'Nothing could be read from that page. Fill the fields in yourself.', warn: true })
      }
    } catch (e) {
      setError(e)
    } finally {
      setReading(false)
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    setTouched(true)
    const p = titleFormProblems(form)
    if (p.title || p.url || saving) return
    setSaving(true)
    setError(null)
    try {
      const t = await createTitle({ ...form, source_url: form.source_url.trim() })
      setSaved(`Added “${t.title_original}” to your catalogue.`)
      setForm(EMPTY_TITLE)
      setPageUrl('')
      setReadNote(null)
      setTouched(false)
      onAdded()
    } catch (err) {
      setError(err)
    } finally {
      setSaving(false)
    }
  }

  const problems = touched ? titleFormProblems(form) : {}

  return (
    <div className="discover-block">
      <p className="muted discover-lead">
        Adds a catalogue entry (title, author, tags, synopsis), not chapters or episodes. To pull content into a drama,
        use Sources › Paste a link.
      </p>
      <div className="discover-row">
        <Field label="Fill from a page (optional)">
          <input type="url" value={pageUrl} maxLength={2000} onChange={(e) => setPageUrl(e.target.value)} placeholder="https://" />
        </Field>
        <button type="button" onClick={readPage} disabled={!aiReady || !isHttpUrl(pageUrl) || reading} aria-busy={reading}>
          {reading ? 'Reading…' : 'Read page'}
        </button>
      </div>
      {!aiReady && <p className="muted">Reading a page needs an AI engine (set a key in Settings).</p>}
      {readNote && (
        <p className={readNote.warn ? 'warn' : 'muted'} role="status">
          {readNote.text}
        </p>
      )}
      <form className="discover-block" onSubmit={save} noValidate>
        <div className="discover-form-grid">
          <Field label="Title (original language)" error={problems.title}>
            <input type="text" value={form.title_original} maxLength={300} onChange={set('title_original')} />
          </Field>
          <Field label="Title (English)">
            <input type="text" value={form.title_en} maxLength={300} onChange={set('title_en')} />
          </Field>
          <Field label="Author">
            <input type="text" value={form.author} maxLength={300} onChange={set('author')} />
          </Field>
          <Field label="Language">
            <select value={form.language} onChange={set('language')}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Media type">
            <select value={form.media_type} onChange={set('media_type')}>
              {TITLE_MEDIA_TYPES.map((m) => (
                <option key={m} value={m}>
                  {mediaLabel(m)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Tags" help="Comma-separated.">
            <input type="text" value={form.tags} maxLength={500} onChange={set('tags')} />
          </Field>
        </div>
        <Field label="Summary">
          <textarea rows={3} value={form.summary_en} maxLength={5000} onChange={set('summary_en')} />
        </Field>
        <Field label="Source URL (optional)" error={problems.url}>
          <input type="url" value={form.source_url} maxLength={2000} onChange={set('source_url')} placeholder="https://" />
        </Field>
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
        {saved && (
          <p className="discover-ok" role="status">
            {saved}
          </p>
        )}
        <div>
          <button type="submit" className="primary" disabled={saving} aria-busy={saving}>
            {saving ? 'Adding…' : 'Add to catalogue'}
          </button>
        </div>
      </form>
    </div>
  )
}
