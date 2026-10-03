import { useEffect, useRef, useState } from 'react'

import { CHECK_JOB_ID, dismissNotification, setTrackedDrama, setTrackedSave, startCheckNow } from '../../api/sources'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { buttonClass } from '../../components/uiClasses'
import type { CheckResult, OpenSeries, SourceNotification, SourceSummary, TrackedSeries } from '../../types/sources'
import { ago, checkSummary, isoTime, percent, trackedDramaChoices } from './sourcesFormat'
import { dramaLabel } from './urlImportFormat'
import { useDramaList } from './useDramaList'
import { useSourcesJob } from './useSourcesJob'

type Props = {
  notifications: SourceNotification[]
  tracked: TrackedSeries[]
  sources: SourceSummary[] | null
  display: (source: string) => string
  // False from another device while Sources writes are PC only: no Check now, no drama link.
  canAct: boolean
  onOpen: (series: OpenSeries, opener: string) => void
  onDismissed: (id: number) => void
  onUntrack: (t: TrackedSeries) => void
  onTracked: (t: TrackedSeries[]) => void
  // A check finished: reload tracked series and notifications.
  onChecked: () => void
  untrackBusy: string | null
  untrackError: unknown
  clearUntrackError: () => void
}

const AUTO_HELP =
  'Auto-import into: where a series’ new chapters go when “Auto-import new chapters” is on in Source settings. Otherwise they are only listed here.'

// A Card (always open), only rendered when there are notifications or tracked series.
export function NewChapters({
  notifications, tracked, sources, display, canAct, onOpen, onDismissed, onUntrack, onTracked, onChecked,
  untrackBusy, untrackError, clearUntrackError,
}: Props) {
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [linking, setLinking] = useState<string | null>(null)
  const check = useSourcesJob<CheckResult>(canAct ? CHECK_JOB_ID : null)
  const dramas = useDramaList(canAct && tracked.length > 0)
  const titleOf = (n: SourceNotification) =>
    tracked.find((t) => t.source === n.source && t.series_id === n.series_id)?.title || n.series_id

  // Reload once per finished run that this page started.
  const reloaded = useRef<CheckResult | null>(null)
  useEffect(() => {
    if (check.status === 'done' && check.startedHere && check.result && reloaded.current !== check.result) {
      reloaded.current = check.result
      onChecked()
    }
  }, [check.status, check.startedHere, check.result, onChecked])

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

  async function saveCbz(t: TrackedSeries, on: boolean) {
    const key = `${t.source}:${t.series_id}`
    setError(null)
    setLinking(key)
    try {
      onTracked(await setTrackedSave(t.source, t.series_id, on))
    } catch (e) {
      setError(e)
    } finally {
      setLinking(null)
    }
  }

  async function link(t: TrackedSeries, value: string) {
    const key = `${t.source}:${t.series_id}`
    setError(null)
    setLinking(key)
    try {
      onTracked(await setTrackedDrama(t.source, t.series_id, value ? Number(value) : null))
    } catch (e) {
      setError(e)
    } finally {
      setLinking(null)
    }
  }

  const running = check.status === 'running'
  const result = check.status === 'done' ? check.result : null
  const failures = result ? Object.entries(result.errors) : []

  return (
    <Card
      className="sources-new"
      aria-label="New chapters"
      title={
        <>
          New chapters{' '}
          {notifications.length > 0 && <Badge tone="accent">{notifications.length} new</Badge>}
        </>
      }
      meta={`${tracked.length} tracked series`}
      actions={
        canAct && tracked.length > 0 ? (
          <button
            type="button"
            className={buttonClass('secondary')}
            disabled={running}
            onClick={() => check.start(() => startCheckNow())}
          >
            {running ? 'Checking…' : 'Check now'}
          </button>
        ) : undefined
      }
    >
      <ErrorBanner error={error ?? untrackError} onDismiss={() => (error ? setError(null) : clearUntrackError())} />
      {canAct && tracked.length > 0 && (
        <div className="sources-check" data-testid="sources-check">
          <p className="muted" aria-live="polite">
            {running
              ? `${check.message ?? 'Checking tracked series…'} ${percent(check.progress)}`.trim()
              : result
                ? checkSummary(result)
                : 'New chapters are announced here, never downloaded unless auto-import or saving as CBZ is on.'}
          </p>
          {failures.length > 0 && (
            <details>
              <summary>Why {failures.length === 1 ? 'one' : `${failures.length}`} failed</summary>
              <ul>
                {failures.map(([title, why]) => (
                  <li key={title}>
                    {title}: {why}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <ErrorBanner
            error={check.startError ?? check.error}
            onDismiss={check.startError ? check.clearStartError : check.reset}
            describe={{ serverText: true }}
          />
        </div>
      )}
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
                  className={buttonClass('secondary', 'sm')}
                  data-opener={`new:${n.id}`}
                  onClick={() => onOpen({ source: n.source, series_id: n.series_id, title: titleOf(n) }, `new:${n.id}`)}
                >
                  Open
                </button>
                <button
                  type="button"
                  className={buttonClass('ghost', 'sm')}
                  disabled={busy === n.id}
                  onClick={() => dismiss(n.id)}
                >
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
          {canAct && <p className="muted sources-auto-help">{AUTO_HELP}</p>}
          <ul className="sources-rows sources-tracked">
            {tracked.map((t) => {
              const key = `${t.source}:${t.series_id}`
              const source = sources?.find((s) => s.name === t.source)
              const choices = trackedDramaChoices(dramas.items ?? [], source)
              const current = t.drama_id !== null && !choices.some((d) => d.id === t.drama_id)
              return (
                <li key={key}>
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
                  {canAct && (
                    <label className="sources-autoimport">
                      <span>Auto-import into</span>
                      <select
                        value={t.drama_id ?? ''}
                        disabled={!dramas.items || linking === key}
                        onChange={(e) => link(t, e.target.value)}
                      >
                        <option value="">None</option>
                        {current && <option value={t.drama_id!}>Drama #{t.drama_id}</option>}
                        {choices.map((d) => (
                          <option key={d.id} value={d.id}>
                            {dramaLabel(d)}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  {canAct && source?.supports.get_pages && (
                    <label className="sources-autoimport">
                      <input
                        type="checkbox"
                        checked={t.save_cbz}
                        disabled={linking === key}
                        onChange={(e) => saveCbz(t, e.target.checked)}
                      />
                      <span>Save new chapters as CBZ</span>
                    </label>
                  )}
                  <ConfirmButton
                    label="Stop tracking…"
                    verb="stop tracking"
                    name={t.title || t.series_id}
                    busy={untrackBusy === key}
                    onConfirm={() => onUntrack(t)}
                  />
                </li>
              )
            })}
          </ul>
        </>
      )}
    </Card>
  )
}
