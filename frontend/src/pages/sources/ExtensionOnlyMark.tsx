/*
 * ExtensionOnlyMark: inside a source's Details (PC only). The person records that this
 * source works only through the browser extension. It is their own note: it changes no test
 * result, and imports and scheduled checks for a marked source stop before reading the site.
 */
import { useId, useState } from 'react'

import { setExtensionOnly } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { ExtensionOnlyMark as Mark, SourceDetail } from '../../types/sources'
import { EXTENSION_ONLY_HINT, isoDay } from './sourcesFormat'

const NOTE_MAX = 200

type Props = {
  detail: SourceDetail
  onChanged: (mark: Mark) => void
}

export function ExtensionOnlyMark({ detail, onChanged }: Props) {
  const on = !!detail.extension_only
  const [note, setNote] = useState(detail.extension_note ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const helpId = useId()

  async function save(next: boolean, text?: string) {
    setError(null)
    setBusy(true)
    try {
      const mark = await setExtensionOnly(detail.name, next, text)
      if (!next) setNote('')
      onChanged(mark)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="source-extension" role="group" aria-label={`Browser extension: ${detail.display_name}`}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
      <div className="toggle-list">
        <Field
          label="Works only with the browser extension"
          help="Your own note. Imports and checks skip a marked site; the tests above still run."
        >
          <Toggle checked={on} disabled={busy} aria-describedby={helpId} onChange={(next) => save(next, note)} />
        </Field>
      </div>
      {on && detail.extension_marked_at && <p className="muted">Marked by you, {isoDay(detail.extension_marked_at)}.</p>}
      {on && detail.extension_works_without && (
        <p className="warn" id={helpId}>
          {EXTENSION_ONLY_HINT}{' '}
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => save(false)}>
            Clear marker
          </button>
        </p>
      )}
      {on && (
        <div className="actions">
          <input
            type="text"
            aria-label="Note"
            placeholder="Note (optional)"
            maxLength={NOTE_MAX}
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button
            type="button"
            className={buttonClass('secondary', 'sm')}
            disabled={busy || note === (detail.extension_note ?? '')}
            onClick={() => save(true, note)}
          >
            Save note
          </button>
        </div>
      )}
    </div>
  )
}
