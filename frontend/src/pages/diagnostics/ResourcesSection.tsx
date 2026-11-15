import { useEffect, useState } from 'react'

import { getResources } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import type { ResourceReadings } from '../../types/diagnostics'
import { readingText, resourcesSummary, resourceStatus, ruleText, sampledText } from './resourcesFormat'

const REFRESH_MS = 30_000

/** "Resources": the resource monitor's last readings and each alert rule's standing. Read-only. */
export function ResourcesSection({ openSignal }: { openSignal?: number }) {
  const [data, setData] = useState<ResourceReadings | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    const load = () => getResources().then((r) => { if (live) { setData(r); setError(null) } },
      (e) => { if (live) setError(e) })
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => { live = false; window.clearInterval(timer) }
  }, [])

  return (
    <Section title="Resources" storageKey="diagnostics.resources" summary={data ? resourcesSummary(data) : undefined}
      defaultOpen openSignal={openSignal}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!data ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <p className="muted" data-testid="resources-sampled">{sampledText(data)}</p>
          <ul className="diag-rows diag-stack" data-testid="resources-list">
            {data.metrics.map((m) => {
              const status = resourceStatus(m)
              return (
                <li key={m.name} data-testid={`resource-${m.name}`}>
                  <strong>{m.label}</strong>: {readingText(m)} <Badge tone={status.tone}>{status.label}</Badge>
                  <br />
                  <span className="muted">{ruleText(m)}</span>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </Section>
  )
}
