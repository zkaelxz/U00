import { useEffect, useState } from 'react'

import { getQuality } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { ErrorBanner } from '../../components/ErrorBanner'
import { HelpTip } from '../../components/HelpTip'
import { Section } from '../../components/Section'
import type { QualityCheck } from '../../types/diagnostics'
import { QualityTrend } from './QualityTrend'
import { clipStatus, differenceText, kindLabel, marginText, metricLabel, nextStep, num, sortedClips, verdict, verdictDetail } from './qualityFormat'

// Shape and colour carry the result as well as the word, so it reads without telling green from red.
const ICON = {
  ok: <path d="M5 12l5 5 9-10" />,
  bad: <path d="M6 6l12 12M18 6L6 18" />,
  neutral: <path d="M6 12h12" />,
} as const

function Icon({ tone }: { tone: 'ok' | 'bad' | 'neutral' }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
      {ICON[tone]}
    </svg>
  )
}

/** "Quality": the newest release check on this PC, each score against its baseline. Read-only; the check itself runs from the command line. */
export function QualitySection() {
  const [data, setData] = useState<QualityCheck | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let live = true
    getQuality().then((q) => { if (live) setData(q) }, (e) => { if (live) setError(e) })
    return () => { live = false }
  }, [])

  const v = data ? verdict(data) : null
  const step = data ? nextStep(data) : null
  return (
    <Section title="Quality" storageKey="benchmark.quality" defaultOpen>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!data || !v ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <div className={`quality-verdict quality-${v.tone}`} data-testid="quality-gate">
            <div>
              <strong>{v.title}</strong>
              <p>{verdictDetail(data)}</p>
            </div>
            <HelpTip label="About these numbers" id="quality-help">{marginText(data)}</HelpTip>
          </div>
          {data.found && !data.unreadable && (
            <>
              <table className="table bench-table quality-table" data-testid="quality-list">
                <thead>
                  <tr><th>Check</th><th className="num">Now</th><th className="num">Baseline</th><th>Difference</th><th>Result</th></tr>
                </thead>
                <tbody>
                  {sortedClips(data.clips).map((c) => {
                    const status = clipStatus(c)
                    return (
                      <tr key={c.id} data-testid={`quality-${c.id}`} className={status.tone === 'bad' ? 'quality-row-bad' : undefined}>
                        <td><strong>{kindLabel(c)}</strong>, {c.id}<br /><span className="muted">{metricLabel(c)}</span></td>
                        <td className="num"><strong>{num(c.score)}</strong></td>
                        <td className="num">{num(c.baseline)}</td>
                        <td>{differenceText(c)}</td>
                        <td>
                          <Badge tone={status.tone}>
                            <span className="quality-result"><Icon tone={status.tone === 'ok' || status.tone === 'bad' ? status.tone : 'neutral'} />{status.label}</span>
                          </Badge>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <p className="muted">Not measured yet, because there is no checked test set: translation, comic reading, Live.</p>
            </>
          )}
          <QualityTrend />
          {step && <p className="quality-next" data-testid="quality-next">{step}</p>}
          <p className="muted">This page only shows results. The check is started by hand on the PC, and the numbers stay on the PC.</p>
        </>
      )}
    </Section>
  )
}
