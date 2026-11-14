/*
 * Add > a video link: one button (One-Click Translate) that downloads the video, transcribes, translates
 * and exports it with the profile chosen in Settings, through the Queue. "Watch once" removes the title
 * again when the person is done watching. PC only, like the download it runs.
 */
import { useCallback, useEffect, useState } from 'react'

import { probeLink } from '../../api/linkDetails'
import { deleteQuickVideoTitle, getQuickVideoRuns, getQuickVideoSettings, startQuickVideo } from '../../api/quickVideo'
import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { HelpTip } from '../../components/HelpTip'
import { withoutExceptionNames } from '../../components/errorMessages'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import type { QuickVideoRun, QuickVideoSettings, QuickVideoStep } from '../../types/quickVideo'
import { foundForLink, linkCanRun, planForLink, type LinkProbeState } from './addBoxModel'
import { settingText } from './makeSubtitlesFlow'

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

/**
 * A pasted link in the Add box: what the free link probe found, what One-Click Translate will do,
 * and its choices under Change options. Starting it creates the title; `onStarted` then shows its run.
 */
export function QuickVideoLinkPlan({ url, onStarted, onOnlyAdd, onClear }: {
  url: string
  onStarted: () => void
  onOnlyAdd: () => void
  onClear: () => void
}) {
  const pc = usePcOnly()
  const [watchOnce, setWatchOnce] = useState(false)
  const [settings, setSettings] = useState<QuickVideoSettings | null>(null)
  const [probe, setProbe] = useState<LinkProbeState>({ state: 'reading' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (pc === 'remote') return undefined
    getQuickVideoSettings().then(setSettings, () => undefined)
    return undefined
  }, [pc])
  useEffect(() => {
    if (pc === 'remote') return undefined
    // The box keys this by the link, so a new link starts again from "reading".
    let live = true
    probeLink(url).then((p) => live && setProbe({ state: 'read', probe: p }), () => live && setProbe({ state: 'failed' }))
    return () => { live = false }
  }, [pc, url])
  const profile = settings?.profiles.find((p) => p.id === settings.profile_id) ?? null
  const summary = settings?.summary ?? []
  // The native tooltip on the button itself; the (i) under Change options is the same text for touch and keyboard.
  const summaryText = summary.map(settingText).join('\n')
  const probeShown: LinkProbeState = pc === 'remote' ? { state: 'off' } : probe
  const canRun = linkCanRun(pc, probeShown)

  const go = () => {
    setBusy(true)
    setError(null)
    startQuickVideo(url, watchOnce).then(
      () => { setBusy(false); onStarted() },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }

  return (
    <div className="stack">
      <div className="add-box-found">
        <p data-testid="add-found"><strong>Found:</strong> {foundForLink(url, probeShown)}</p>
        <p data-testid="add-plan">
          {planForLink({
            pc, source: !canRun && pc !== 'remote', loaded: settings !== null,
            profile: profile?.name ?? null, engine: summary.find((r) => r.engine)?.engine ?? null,
          })}
        </p>
      </div>
      {canRun && (
        <div className="make-subtitles-go">
          <button
            type="button"
            className={buttonClass('primary')}
            disabled={busy || !profile}
            title={summaryText || undefined}
            aria-describedby={summary.length ? 'quick-video-settings' : undefined}
            onClick={go}
          >
            Make English subtitles
          </button>
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {canRun && (
        <Section title="Change options" summary={watchOnce ? 'watch once' : undefined}>
          <label className="quick-video-check">
            <input type="checkbox" checked={watchOnce} onChange={(e) => setWatchOnce(e.target.checked)} />
            Watch once
          </label>
          <div className="actions">
            {profile && <p className="muted">Profile: {profile.name}</p>}
            {summary.length > 0 && (
              <HelpTip label="One-Click Translate settings" id="quick-video-settings">
                <strong>Uses profile {profile?.name}:</strong>
                <ul style={{ margin: '0.25rem 0', paddingLeft: '1.1rem', lineHeight: 1.35 }}>
                  {summary.map((row) => <li key={row.label}>{settingText(row)}</li>)}
                </ul>
                Change these in Settings &gt; Quick video translate.
              </HelpTip>
            )}
          </div>
        </Section>
      )}
      <div className="actions">
        <button type="button" className={buttonClass(canRun ? 'ghost' : 'primary')} onClick={onOnlyAdd}>Only add it, don&apos;t start</button>
        <button type="button" className={buttonClass('ghost')} onClick={onClear}>Clear</button>
      </div>
    </div>
  )
}

/** The Add page's recent link runs, polled while one is running. PC only, like the download they run. */
export function QuickVideoRuns({ refreshKey }: { refreshKey: number }) {
  const pc = usePcOnly()
  const [runs, setRuns] = useState<QuickVideoRun[]>([])
  const [error, setError] = useState<unknown>(null)
  const [allRuns, setAllRuns] = useState(false)

  const refresh = useCallback(() => getQuickVideoRuns().then(setRuns, () => undefined), [])
  useEffect(() => {
    if (pc === 'remote') return undefined
    void refresh()
    return undefined
  }, [pc, refresh, refreshKey])
  const running = runs.some((r) => r.state === 'running')
  useEffect(() => {
    if (!running) return undefined
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [running, refresh])

  if (pc === 'remote' || (!runs.length && !error)) return null
  const shownRuns = visibleRuns(runs, allRuns)
  const remove = (run: QuickVideoRun) => {
    setError(null)
    deleteQuickVideoTitle(run.drama_id).then(() => void refresh(), setError)
  }

  return (
    <Card title="From a link" aria-label="From a link">
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
