/*
 * Make subtitles > From a link: one button that downloads the video, transcribes, translates and exports
 * it with the profile chosen in Settings, through the Queue. "Watch once" removes the title again when
 * the person is done watching. PC only, like the download it runs.
 */
import { useCallback, useEffect, useState } from 'react'

import { deleteQuickVideoTitle, getQuickVideoRuns, getQuickVideoSettings, startQuickVideo } from '../../api/quickVideo'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { withoutExceptionNames } from '../../components/errorMessages'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import type { QuickVideoRun, QuickVideoSettings, QuickVideoStep } from '../../types/quickVideo'

const POLL_MS = 3000
// Eight finished runs pushed the form nearly two screens down on a phone.
const SHOWN_RUNS = 3
const STEP_NOW: Record<QuickVideoStep, string> = {
  download: 'Downloading', transcribe: 'Transcribing', translate: 'Translating', export: 'Exporting',
}
const STEP_NAME: Record<QuickVideoStep, string> = {
  download: 'Download', transcribe: 'Transcribe', translate: 'Translate', export: 'Export',
}

export const runText = (run: QuickVideoRun): string =>
  run.state === 'done' ? 'Done'
    : run.state === 'stopped' ? `${STEP_NAME[run.step]} stopped${run.reason ? `: ${withoutExceptionNames(run.reason)}` : ''}`
      : STEP_NOW[run.step]

export const visibleRuns = (runs: QuickVideoRun[], all: boolean): QuickVideoRun[] =>
  all ? runs : runs.filter((r, i) => i < SHOWN_RUNS || r.state === 'running')

// A failed or stopped run has nothing to finish watching.
export const canDeleteWatched = (run: QuickVideoRun): boolean => run.watch_once && run.state === 'done'

export function QuickVideoLink() {
  const pc = usePcOnly()
  const [url, setUrl] = useState('')
  const [watchOnce, setWatchOnce] = useState(false)
  const [runs, setRuns] = useState<QuickVideoRun[]>([])
  const [settings, setSettings] = useState<QuickVideoSettings | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [allRuns, setAllRuns] = useState(false)

  const refresh = useCallback(() => getQuickVideoRuns().then(setRuns, () => undefined), [])
  useEffect(() => {
    if (pc === 'remote') return undefined
    void refresh()
    getQuickVideoSettings().then(setSettings, () => undefined)
    return undefined
  }, [pc, refresh])
  const profile = settings?.profiles.find((p) => p.id === settings.profile_id) ?? null
  const running = runs.some((r) => r.state === 'running')
  useEffect(() => {
    if (!running) return undefined
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [running, refresh])

  if (pc === 'remote') return null
  const go = () => {
    setBusy(true)
    setError(null)
    startQuickVideo(url.trim(), watchOnce).then(
      () => { setBusy(false); setUrl(''); void refresh() },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }
  const shownRuns = visibleRuns(runs, allRuns)
  const remove = (run: QuickVideoRun) => {
    setError(null)
    deleteQuickVideoTitle(run.drama_id).then(() => void refresh(), setError)
  }

  return (
    <Card title="From a link" aria-label="From a link">
      <Field label="Video link">
        <input value={url} maxLength={2000} inputMode="url" onChange={(e) => setUrl(e.target.value)} />
      </Field>
      <label className="quick-video-check">
        <input type="checkbox" checked={watchOnce} onChange={(e) => setWatchOnce(e.target.checked)} />
        Watch once
      </label>
      {profile && <p className="muted">Profile: {profile.name}</p>}
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={busy || !url.trim() || !profile} onClick={go}>
          One-Click Translate
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <ul className="stack">
        {shownRuns.map((run) => (
          <li key={run.drama_id}>
            <strong>{run.title}</strong> · {runText(run)}
            <div className="actions">
              <ButtonLink size="sm" variant="ghost" href={routeHref({ name: 'drama', id: run.drama_id, stage: run.state === 'done' ? 'review' : null })}>
                Open
              </ButtonLink>
              {canDeleteWatched(run) && (
                <ConfirmButton name={run.title} label="Done watching, delete…" confirmLabel="Confirm delete" onConfirm={() => remove(run)} />
              )}
            </div>
          </li>
        ))}
      </ul>
      {shownRuns.length < runs.length && (
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setAllRuns(true)}>Show more</button>
      )}
    </Card>
  )
}
