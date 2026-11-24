import { useEffect, useState } from 'react'

import { getQuality } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import type { QualityCheck } from '../../types/diagnostics'
import { clipStatus, clipText, gateStatus, marginText, metricLabel, qualitySummary, ranText } from './qualityFormat'

/** "Quality": the newest release check on this PC, each score against its baseline. Read-only; the check itself runs from the command line. */
export function QualitySection({ openSignal }: { openSignal?: number }) {
  const [data, setData] = useState<QualityCheck | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    getQuality().then((q) => { if (live) setData(q) }, (e) => { if (live) setError(e) })
    return () => { live = false }
  }, [])

  const gate = data ? gateStatus(data) : null
  return (
    <Section title="Quality" storageKey="diagnostics.quality" summary={data ? qualitySummary(data) : undefined}
      openSignal={openSignal}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!data || !gate ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <p data-testid="quality-gate"><Badge tone={gate.tone}>{gate.label}</Badge> {gate.reason}</p>
          {data.found && !data.unreadable && (
            <>
              <p className="muted">{ranText(data)} {marginText(data)}</p>
              <ul className="diag-rows diag-stack" data-testid="quality-list">
                {data.clips.map((c) => {
                  const status = clipStatus(c)
                  return (
                    <li key={c.id} data-testid={`quality-${c.id}`}>
                      <strong>{c.id}</strong>: {clipText(c)} <Badge tone={status.tone}>{status.label}</Badge>
                      <br />
                      <span className="muted">{metricLabel(c)}</span>
                    </li>
                  )
                })}
              </ul>
            </>
          )}
          <p className="muted">
            The check runs on the GPU PC with <code>python -m tools.release_check MANIFEST.json --yes</code> and
            shows here when its results are in this app's data folder.
          </p>
        </>
      )}
    </Section>
  )
}
