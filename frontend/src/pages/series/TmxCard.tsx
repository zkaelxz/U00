// The series page's translation-memory file buttons: download the memory as TMX, or add pairs from a TMX file.
import { useRef, useState } from 'react'

import { exportTmx, importTmx } from '../../api/series'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { downloadText } from '../../components/downloadBlob'
import { buttonClass } from '../../components/uiClasses'

export function TmxCard({ seriesId, canEdit, onImported }: { seriesId: number; canEdit: boolean; onImported: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [result, setResult] = useState<string | null>(null)

  const run = (work: () => Promise<void>) => {
    setBusy(true)
    setError(null)
    setResult(null)
    work().then(
      () => setBusy(false),
      (err: unknown) => { setBusy(false); setError(err) },
    )
  }
  const download = () => run(async () => {
    downloadText(await exportTmx(seriesId), `series-${seriesId}-memory.tmx`, 'application/x-tmx+xml')
  })
  const upload = (file: File) => run(async () => {
    const done = await importTmx(seriesId, await file.text())
    setResult(`Added ${done.added}, kept ${done.skipped}`)
    onImported()
  })

  return (
    <Card title="Translation memory">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="actions">
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={download} disabled={busy} data-testid="series-tmx-export">Export TMX</button>
        {canEdit && (
          <>
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => fileRef.current?.click()} disabled={busy} data-testid="series-tmx-import">Import TMX</button>
            <input
              ref={fileRef}
              type="file"
              accept=".tmx,application/xml,text/xml"
              hidden
              data-testid="series-tmx-file"
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (f) upload(f)
              }}
            />
          </>
        )}
      </div>
      {result && <p className="muted" role="status" data-testid="series-tmx-result">{result}</p>}
    </Card>
  )
}
