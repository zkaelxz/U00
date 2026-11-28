/*
 * Add > a video link: one button (One-click translate) that downloads the video, transcribes, translates
 * and exports it with the profile chosen in Settings, through the Queue. "Watch once" removes the title
 * again when the person is done watching. Any device whose account holds Import from a link and Start
 * jobs may start it; the run is on the PC, and removing a title stays at the PC like every delete.
 */
import { useCallback, useEffect, useState } from 'react'

import { probeLink } from '../../api/linkDetails'
import { deleteQuickVideoTitle, getQuickVideoRuns, getQuickVideoSettings, startQuickVideo } from '../../api/quickVideo'
import { ButtonLink } from '../../components/Button'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { HelpTip } from '../../components/HelpTip'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import { usePcOnly } from '../../hooks/usePcOnly'
import { routeHref } from '../../router'
import type { QuickVideoRun, QuickVideoSettings } from '../../types/quickVideo'
import { foundForLink, linkCanRun, previewRows, NO_CAP_PLAN, NO_PROFILE_PLAN, planForLink, type LinkProbeState } from './addBoxModel'
import { settingText } from './makeSubtitlesFlow'
import { canDeleteWatched, runText, visibleRuns } from './quickVideoRuns'

const POLL_MS = 3000
/**
 * A pasted link in the Add box: what the free link probe found, what One-click translate will do,
 * and its choices under Change options. Starting it creates the title; `onStarted` then shows its run.
 */
export function QuickVideoLinkPlan({ url, onStarted, onOnlyAdd, onClear }: {
  url: string
  onStarted: () => void
  onOnlyAdd?: () => void
  onClear: () => void
}) {
  const pc = usePcOnly()
  const holdsImport = useHolds('media.import_url')
  const holdsJobs = useHolds('jobs.start')
  const allowed = holdsImport && holdsJobs
  const [watchOnce, setWatchOnce] = useState(false)
  const [settings, setSettings] = useState<QuickVideoSettings | null>(null)
  const [probe, setProbe] = useState<LinkProbeState>({ state: 'reading' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (!allowed) return undefined
    getQuickVideoSettings().then(setSettings, () => undefined)
    return undefined
  }, [allowed])
  useEffect(() => {
    if (!allowed) return undefined
    // The box keys this by the link, so a new link starts again from "reading".
    let live = true
    probeLink(url).then((p) => live && setProbe({ state: 'read', probe: p }), () => live && setProbe({ state: 'failed' }))
    return () => { live = false }
  }, [allowed, url])
  const profile = settings?.profiles.find((p) => p.id === settings.profile_id) ?? null
  const summary = settings?.summary ?? []
  // The native tooltip on the button itself; the (i) under Change options is the same text for touch and keyboard.
  const summaryText = summary.map(settingText).join('\n')
  const probeShown: LinkProbeState = allowed ? probe : { state: 'off' }
  const canRun = linkCanRun(allowed, probeShown)
  const plan = planForLink({
    pc, allowed, source: !canRun && allowed, loaded: settings !== null,
    profile: profile?.name ?? null, engine: summary.find((r) => r.engine)?.engine ?? null,
    capNeeded: settings?.cap_needed === true,
  })

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
          {plan === NO_PROFILE_PLAN ? <a href={routeHref({ name: 'settings', card: 'quick-video' })}>{plan}</a>
            : plan === NO_CAP_PLAN ? <a href={routeHref({ name: 'settings', card: 'spending' })}>{plan}</a> : plan}
        </p>
      </div>
      {canRun && profile && (
        <dl className="add-box-preview" data-testid="add-preview">
          {previewRows(probeShown, summary).map((r) => (
            <div key={r.label}><dt>{r.label}</dt><dd>{r.value}</dd></div>
          ))}
        </dl>
      )}
      {canRun && (
        <div className="make-subtitles-go">
          <button
            type="button"
            className={buttonClass('primary')}
            disabled={busy || !profile || settings?.cap_needed === true}
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
              <HelpTip label="One-click translate settings" id="quick-video-settings">
                <strong>Uses profile {profile?.name}:</strong>
                <ul style={{ margin: '0.25rem 0', paddingLeft: '1.1rem', lineHeight: 1.35 }}>
                  {summary.map((row) => <li key={row.label}>{settingText(row)}</li>)}
                </ul>
                Change these in Settings &gt; One-click translate.
              </HelpTip>
            )}
          </div>
        </Section>
      )}
      <div className="actions">
        {onOnlyAdd && <button type="button" className={buttonClass(canRun ? 'ghost' : 'primary')} onClick={onOnlyAdd}>Add only</button>}
        <button type="button" className={buttonClass('ghost')} onClick={onClear}>Clear</button>
      </div>
    </div>
  )
}

/** The Add page's recent link runs, polled while one is running. Removing a watch-once title is at the PC only. */
export function QuickVideoRuns({ refreshKey }: { refreshKey: number }) {
  const pc = usePcOnly()
  const holdsJobs = useHolds('jobs.start')
  const [runs, setRuns] = useState<QuickVideoRun[]>([])
  const [error, setError] = useState<unknown>(null)
  const [allRuns, setAllRuns] = useState(false)

  const refresh = useCallback(() => getQuickVideoRuns().then(setRuns, () => undefined), [])
  useEffect(() => {
    if (!holdsJobs) return undefined
    void refresh()
    return undefined
  }, [holdsJobs, refresh, refreshKey])
  const running = runs.some((r) => r.state === 'running')
  useEffect(() => {
    if (!running) return undefined
    const t = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(t)
  }, [running, refresh])

  if (!holdsJobs || (!runs.length && !error)) return null
  const shownRuns = visibleRuns(runs, allRuns)
  const remove = (run: QuickVideoRun) => {
    setError(null)
    deleteQuickVideoTitle(run.drama_id).then(() => void refresh(), setError)
  }

  return (
    <section className="add-runs" aria-label="From a link">
      <h2 className="add-runs-title">From a link</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <ul className="add-runs-list">
        {shownRuns.map((run) => (
          <li key={run.drama_id} className="add-run">
            <span className="add-run-name"><strong>{run.title}</strong> <span className="muted">· {runText(run)}</span></span>
            <div className="add-run-actions">
              <ButtonLink size="sm" variant="secondary" href={routeHref({ name: 'drama', id: run.drama_id, stage: run.state === 'done' ? 'review' : null })}>
                Open
              </ButtonLink>
              {pc !== 'remote' && canDeleteWatched(run) && (
                <ConfirmButton size="sm" name={run.title} label="Done watching, delete…" confirmLabel="Confirm delete" onConfirm={() => remove(run)} />
              )}
            </div>
          </li>
        ))}
      </ul>
      {shownRuns.length < runs.length && (
        <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setAllRuns(true)}>Show more</button>
      )}
    </section>
  )
}
