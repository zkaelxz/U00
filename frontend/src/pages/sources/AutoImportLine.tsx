import { useEffect, useState } from 'react'

import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { getSourcesSettings, updateSourcesSettings } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import type { SourcesSettings, SourcesSettingsUpdate } from '../../types/sources'
import { CHECK_FIELD, fieldProblem } from './sourcesSettings'

/**
 * The two settings that decide what happens to new chapters, on one line of the New chapters card.
 * Each applies on its own (the switch at once, the number when its field is left or Enter is
 * pressed), so there is no Save button to find.
 */
export function AutoImportLine() {
  const [settings, setSettings] = useState<SourcesSettings | null>(null)
  const [hours, setHours] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  // Wait for /api/meta so another device never sends a settings request (these writes are PC only).
  useEffect(() => {
    let off = false
    void loadPcMode().then(() => {
      if (off || getPcMode() === 'remote') return
      getSourcesSettings().then(
        (s) => {
          if (off) return
          setSettings(s)
          setHours(String(s.check_interval_hours))
        },
        () => undefined,
      )
    })
    return () => {
      off = true
    }
  }, [])

  if (!settings) return null
  const problem = fieldProblem(CHECK_FIELD, hours)

  async function apply(changes: SourcesSettingsUpdate) {
    setError(null)
    setBusy(true)
    try {
      const next = await updateSourcesSettings(changes)
      setSettings(next)
      setHours(String(next.check_interval_hours))
    } catch (e) {
      setError(e)
      setHours(String(settings?.check_interval_hours ?? ''))
    } finally {
      setBusy(false)
    }
  }

  function commitHours() {
    if (problem || Number(hours) === settings?.check_interval_hours) return
    void apply({ check_interval_hours: Number(hours) })
  }

  return (
    <div className="sources-auto-line" data-testid="sources-auto-line">
      <div className="toggle-list">
        <Field label="Auto-import new chapters" help="Off: new chapters are announced, not downloaded.">
          <Toggle
            checked={settings.auto_queue_new_chapters}
            disabled={busy}
            onChange={(on) => void apply({ auto_queue_new_chapters: on })}
          />
        </Field>
      </div>
      <Field label={CHECK_FIELD.label} help={CHECK_FIELD.help} error={problem}>
        <input
          type="number"
          inputMode="numeric"
          min={CHECK_FIELD.min}
          max={CHECK_FIELD.max}
          value={hours}
          disabled={busy}
          onChange={(e) => setHours(e.target.value)}
          onBlur={commitHours}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitHours()
          }}
        />
      </Field>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true, serverText: true }} />
    </div>
  )
}
