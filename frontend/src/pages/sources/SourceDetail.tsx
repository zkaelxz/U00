import { useEffect, useState } from 'react'

import { getSource, listAttempts, resetSourceHealth } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import type { SourceAttempt, SourceDetail as Detail, SourceHealth } from '../../types/sources'
import { humanizeValue as humanize } from '../../components/labels'
import { ago, healthLine, isoTime, pausedFor, tierLines } from './sourcesFormat'

type Props = {
  name: string
  onHealth: (name: string, h: SourceHealth) => void
}

/** A source's record, loaded when Details is opened. Information only. */
export function SourceDetail({ name, onHealth }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [resetting, setResetting] = useState(false)

  useEffect(() => {
    let off = false
    getSource(name).then(
      (d) => !off && setDetail(d),
      (e: unknown) => !off && setError(e),
    )
    return () => {
      off = true
    }
  }, [name])

  async function tryNow() {
    setError(null)
    setResetting(true)
    try {
      const h = await resetSourceHealth(name)
      setDetail((d) => (d ? { ...d, health_detail: h } : d))
      onHealth(name, h)
    } catch (e) {
      setError(e)
    } finally {
      setResetting(false)
    }
  }

  if (!detail) {
    return error ? (
      <ErrorBanner error={error} describe={{ pcOnly: true }} />
    ) : (
      <p className="muted">Loading…</p>
    )
  }

  const rows: [string, string][] = [
    ['Status', humanize(detail.status)],
    ['Technical status', humanize(detail.technical_status)],
    ['Access method', humanize(detail.access_method)],
    ['Content reached', humanize(detail.content_access_status)],
    ['Sign-in required', humanize(detail.authentication_required)],
    ['Purchase required', humanize(detail.purchase_required)],
    ['Protection', humanize(detail.technical_protection)],
    ['Automation', humanize(detail.automation_permission)],
    ['AI use', humanize(detail.ai_ml_use)],
  ]
  const paused = pausedFor(detail.health_detail.retry_after)
  const terms = Object.entries(detail.terms ?? {}).filter(([, v]) => v !== null && v !== '' && v !== false)

  return (
    <div className="source-detail">
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
      <dl className="source-dl">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
        <div>
          <dt>Access tiers</dt>
          <dd>{tierLines(detail.tiers).join(' · ')}</dd>
        </div>
      </dl>
      <p className="muted">{healthLine(detail.health_detail)}</p>
      {paused && (
        <p className="warn">
          {paused}{' '}
          <button type="button" disabled={resetting} onClick={tryNow}>
            Try again now
          </button>
        </p>
      )}
      {detail.auth_supported && <p>Sign-in: {detail.has_saved_signin ? 'saved' : 'none'}</p>}
      {terms.length > 0 && (
        <Section title="Terms notes" summary="Information only">
          <dl className="source-dl">
            {terms.map(([k, v]) => (
              <div key={k}>
                <dt>{humanize(k)}</dt>
                <dd>{typeof v === 'string' ? v : String(v)}</dd>
              </div>
            ))}
          </dl>
        </Section>
      )}
      <Attempts name={name} />
    </div>
  )
}

/** "Recent attempts": fetched the first time it is opened. */
function Attempts({ name }: { name: string }) {
  const [rows, setRows] = useState<SourceAttempt[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  function onToggle(open: boolean) {
    if (!open || rows) return
    setError(null)
    listAttempts(name, 20).then(setRows, setError)
  }

  return (
    <Section title="Recent attempts" onToggle={onToggle}>
      <ErrorBanner error={error} describe={{ pcOnly: true }} />
      {!rows && !error && <p className="muted">Loading…</p>}
      {rows && !rows.length && <p className="muted">No attempts yet.</p>}
      {rows && rows.length > 0 && (
        <ul className="sources-attempts">
          {rows.map((a, i) => (
            <li key={`${a.created_at ?? 0}-${i}`}>
              {a.url} · {humanize(a.technical_status)} ·{' '}
              <time dateTime={isoTime(a.created_at)}>{ago(a.created_at)}</time>
              {(a.lines.length > 0 || a.reasons.length > 0) && (
                <details>
                  <summary>Details</summary>
                  <ul>
                    {[...a.reasons, ...a.lines].map((l, j) => (
                      <li key={j}>{l}</li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
