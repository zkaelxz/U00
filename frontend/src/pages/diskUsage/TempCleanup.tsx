/*
 * "Clean temp files now" under Library tools > Disk usage: deletes everything
 * in Baihe's own temp folder, where jobs keep work files that a crash or a
 * killed job can leave behind. Refused by the server while a job runs (409).
 * The result is counts and megabytes only.
 */
import { useState } from 'react'

import { cleanTempFiles } from '../../api/diskUsage'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { TEMP_CLEAN_INTRO, describeTempCleaned } from './diskUsageModel'

const SERVER = { pcOnly: true, serverText: true } as const

export function TempCleanup({ onCleaned }: { onCleaned: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<string | null>(null)
  const run = () => {
    setBusy(true)
    setError(null)
    setResult(null)
    cleanTempFiles().then(
      (r) => {
        setBusy(false)
        setResult(describeTempCleaned(r))
        onCleaned()
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  return (
    <section aria-label="Temp files" className="du-trash du-temp">
      <h3 className="du-trash-title">Temp files</h3>
      <p className="muted du-why">{TEMP_CLEAN_INTRO}</p>
      <div className="du-actions">
        <ConfirmButton
          name="temp files"
          label="Clean temp files now…"
          ariaLabel="Clean temp files now"
          confirmLabel="Confirm: delete all temp files"
          verb="delete"
          busy={busy}
          onConfirm={run}
        />
      </div>
      {result && <p className="muted" role="status">{result}</p>}
      <ErrorBanner error={error} describe={SERVER} onDismiss={() => setError(null)} />
    </section>
  )
}
