import { useCallback, useEffect, useState } from 'react'

import { getSource, listAttempts, resetSourceHealth } from '../../api/sources'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { ExtensionOnlyMark as Mark, SourceAttempt, SourceDetail as Detail, SourceHealth } from '../../types/sources'
import { humanizeValue as humanize } from '../../components/labels'
import { ExtensionOnlyMark } from './ExtensionOnlyMark'
import { SourceAccess } from './SourceAccess'
import { accessMethodLabel, ago, healthLine, healthTooltip, isoTime, pausedFor, statusLabel, tierLines } from './sourcesFormat'

type Props = {
  name: string
  onHealth: (name: string, h: SourceHealth) => void
  onSignin: (name: string, has: boolean) => void
  // The extension-only marker was set or cleared.
  onExtensionOnly?: (name: string, on: boolean) => void
}

/** A source's record, loaded when Details is opened. Information only. */
export function SourceDetail({ name, onHealth, onSignin, onExtensionOnly }: Props) {
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [resetting, setResetting] = useState(false)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let off = false
    getSource(name).then(
      (d) => !off && setDetail(d),
      (e: unknown) => !off && setError(e),
    )
    return () => {
      off = true
    }
  }, [name, reload])

  const refresh = useCallback(() => setReload((n) => n + 1), [])
  const signinChanged = useCallback((has: boolean) => onSignin(name, has), [name, onSignin])

  function markChanged(mark: Mark) {
    setDetail((d) => (d ? { ...d, ...mark } : d))
    onExtensionOnly?.(name, mark.extension_only)
  }

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
    ['Status', statusLabel(detail)],
    ['Technical status', humanize(detail.technical_status)],
    ['Access method', accessMethodLabel(detail)],
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
          <dd>{tierLines(detail.tiers, detail).join(' · ')}</dd>
        </div>
      </dl>
      <p className="muted" title={healthTooltip(detail.health_detail)}>{healthLine(detail.health_detail)}</p>
      {paused && (
        <p className="warn">
          {paused}{' '}
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={resetting} onClick={tryNow}>
            Try again now
          </button>
        </p>
      )}
      {detail.auth_supported && <p>Sign-in: {detail.has_saved_signin ? 'saved' : 'none'}</p>}
      <SourceAccess detail={detail} onChanged={refresh} onSignin={signinChanged} />
      <ExtensionOnlyMark detail={detail} onChanged={markChanged} />
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

const ATTEMPTS_SHOWN = 3

/** "Recent attempts": fetched the first time it is opened. */
function Attempts({ name }: { name: string }) {
  const [rows, setRows] = useState<SourceAttempt[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [showAll, setShowAll] = useState(false)

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
          {(showAll ? rows : rows.slice(0, ATTEMPTS_SHOWN)).map((a, i) => (
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
      {rows && rows.length > ATTEMPTS_SHOWN && (
        <button type="button" className="link" onClick={() => setShowAll((v) => !v)}>
          {showAll ? 'Show fewer' : `Show all (${rows.length})`}
        </button>
      )}
    </Section>
  )
}
