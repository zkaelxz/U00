import { useEffect, useState } from 'react'

import { getPorts } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, type PcMode } from '../../hooks/usePcOnly'
import type { PortEntry } from '../../types/diagnostics'
import { portsSummary, portStatus, portText } from './portsFormat'

/** "Ports": the ports Baihe uses, whether each is in use, and how to change it. Read-only, PC only. */
export function PortsSection({ pc }: { pc: PcMode }) {
  if (pc === 'remote') {
    return (
      <Section title="Ports" storageKey="diagnostics.ports" summary={PC_ONLY_SUMMARY}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  return <PortsBody />
}

function PortsBody() {
  const [ports, setPorts] = useState<PortEntry[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    getPorts().then((r) => setPorts(r.ports), setError)
  }, [])

  return (
    <Section title="Ports" storageKey="diagnostics.ports" summary={ports ? portsSummary(ports) : undefined}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!ports ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <ul className="diag-rows diag-stack" data-testid="ports-list">
          {ports.map((p) => (
            <li key={p.key} data-testid={`port-${p.key}`}>
              <strong>{p.label}</strong>: {portText(p)}{' '}
              <Badge tone={p.active ? 'ok' : 'neutral'}>{portStatus(p)}</Badge>
              <br />
              <span className="muted">{p.how_to_change}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
