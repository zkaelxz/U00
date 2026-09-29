import { useState } from 'react'

import { dismissNotification } from '../../api/sources'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import type { OpenSeries, SourceNotification, TrackedSeries } from '../../types/sources'
import { ago, isoTime } from './sourcesFormat'

type Props = {
  notifications: SourceNotification[]
  tracked: TrackedSeries[]
  display: (source: string) => string
  onOpen: (series: OpenSeries, opener: string) => void
  onDismissed: (id: number) => void
  onUntrack: (t: TrackedSeries) => void
  untrackBusy: string | null
  untrackError: unknown
  clearUntrackError: () => void
}

// Only rendered when there are notifications or tracked series.
export function NewChapters({
  notifications, tracked, display, onOpen, onDismissed, onUntrack, untrackBusy, untrackError, clearUntrackError,
}: Props) {
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const titleOf = (n: SourceNotification) =>
    tracked.find((t) => t.source === n.source && t.series_id === n.series_id)?.title || n.series_id

  async function dismiss(id: number) {
    setError(null)
    setBusy(id)
    try {
      await dismissNotification(id)
      onDismissed(id)
    } catch (e) {
      setError(e)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Section
      title="New chapters"
      count={notifications.length}
      summary={`${notifications.length} new · ${tracked.length} tracked`}
      defaultOpen={notifications.length > 0}
      storageKey="sources.new"
    >
      <ErrorBanner error={error ?? untrackError} onDismiss={() => (error ? setError(null) : clearUntrackError())} />
      {notifications.length > 0 && (
        <ul className="sources-rows">
          {notifications.map((n) => (
            <li key={n.id}>
              <span>
                {n.title || titleOf(n)} · {display(n.source)} ·{' '}
                <time dateTime={isoTime(n.created_at)}>{ago(n.created_at)}</time>
              </span>
              <span className="actions">
                <button
                  type="button"
                  data-opener={`new:${n.id}`}
                  onClick={() => onOpen({ source: n.source, series_id: n.series_id, title: titleOf(n) }, `new:${n.id}`)}
                >
                  Open
                </button>
                <button type="button" className="link" disabled={busy === n.id} onClick={() => dismiss(n.id)}>
                  Dismiss
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {tracked.length > 0 && (
        <>
          <h4>Tracked series</h4>
          <ul className="sources-rows">
            {tracked.map((t) => (
              <li key={`${t.source}:${t.series_id}`}>
                <span>
                  {t.title || t.series_id} · {display(t.source)}
                  {t.last_check_error ? (
                    <span className="warn"> · last check failed: {t.last_check_error}</span>
                  ) : !t.last_checked ? (
                    ' · not checked yet'
                  ) : (
                    <>
                      {' '}· checked <time dateTime={isoTime(t.last_checked)}>{ago(t.last_checked)}</time>
                    </>
                  )}
                </span>
                <ConfirmButton
                  label="Stop tracking…"
                  verb="stop tracking"
                  name={t.title || t.series_id}
                  busy={untrackBusy === `${t.source}:${t.series_id}`}
                  onConfirm={() => onUntrack(t)}
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  )
}
