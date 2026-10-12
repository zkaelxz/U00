import { useEffect, useState } from 'react'

import { generateEpisodeSummary, getEpisodeSummaryOptions } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { humanize } from '../../../components/labels'
import type { EpisodeSummaryOptions } from '../../../types/translateStage'

// Writes the running episode summary from the title's current lines, for a run
// that left it empty. The text only fills the box: Save keeps it. The call is
// paid for on a cloud engine, so the engine is named before it runs.
export function EpisodeSummaryGenerate({ dramaId, onGenerated }: { dramaId: number; onGenerated: (text: string) => void }) {
  const [options, setOptions] = useState<EpisodeSummaryOptions | null>(null)
  const [engine, setEngine] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getEpisodeSummaryOptions(dramaId).then(
      (o) => {
        if (cancelled) return
        setOptions(o)
        setEngine(o.engines.includes(o.engine) ? o.engine : o.engines[0] ?? '')
      },
      () => undefined, // the box still works by hand
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (!options || options.engines.length === 0) return null
  const run = () => {
    setPending(true)
    setError(null)
    generateEpisodeSummary(dramaId, engine).then((r) => onGenerated(r.summary), setError).finally(() => setPending(false))
  }
  return (
    <div className="actions" data-testid="episode-summary-generate">
      <select aria-label="Summary engine" value={engine} onChange={(e) => setEngine(e.target.value)} disabled={pending}>
        {options.engines.map((e) => <option key={e} value={e}>{humanize('engine', e)}</option>)}
      </select>
      <button type="button" onClick={run} disabled={pending || !engine}>
        {pending ? 'Generating…' : 'Generate summary'}
      </button>
      <span className="muted">Fills the box from the translated lines; press Save to keep it.</span>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
