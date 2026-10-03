import { useEffect, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import {
  clampGpuMaxParallel,
  getSettings,
  GPU_MAX_PARALLEL_MAX,
  gpuMaxParallelHelp,
  TOGGLES,
  updateGpuMaxParallel,
  updateSetting,
} from '../api/settings'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { Toggle } from '../components/Toggle'
import { AppUpdatesCard } from './settings/AppUpdatesCard'
import { DeveloperModeCard } from './settings/DeveloperModeCard'
import { DevicesCard } from './settings/DevicesCard'
import { AutoBackupCard } from './settings/AutoBackupCard'
import { EngineRoutingCard } from './settings/EngineRoutingCard'
import { ExtensionSection } from './settings/ExtensionSection'
import { JellyfinSection } from './settings/JellyfinSection'
import { NotificationsSection } from './settings/NotificationsSection'
import { NotionSection } from './settings/NotionSection'
import { AdvancedCard, DefaultsCard, SpendingCard } from './settings/PreferencesSections'
import { SharingCard } from './settings/SharingCard'
import { TranscriptionExperimentsCard } from './settings/TranscriptionExperimentsCard'
import { WebSearchSection } from './settings/WebSearchSection'
import type { SettingsOverview, SettingsToggleKey } from '../types/settings'
import './settings/settings.css'

const TOGGLE_HELP: Partial<Record<SettingsToggleKey, string>> = {
  gpu_limit_enabled:
    'Queues GPU-heavy jobs beyond "GPU jobs at once" so they do not run out of memory.',
  notify_on_completion: 'Shows a notification when a background job finishes.',
  use_gpu: 'Transcribe on the graphics card when one is available (faster).',
  bulk_auto_resume:
    'Resume interrupted translation batches when the app starts. Off by default: resumed batches can spend on your engine account.',
}

type FoldId = 'jobs' | 'engines' | 'defaults' | 'alerts' | 'sharing' | 'integrations' | 'advanced' | 'experimental'

const FOLD_LABEL: Record<FoldId, string> = {
  jobs: 'Jobs',
  engines: 'Engines and keys',
  defaults: 'Translation and spending',
  alerts: 'Notifications, backups, updates',
  sharing: 'Sharing and devices',
  integrations: 'Integrations',
  advanced: 'Advanced',
  experimental: 'Experimental & developer',
}

// One collapsible group. The jump links must not touch location.hash: the app routes on it.
function Fold({
  id,
  signals,
  summary,
  defaultOpen,
  single,
  children,
}: {
  id: FoldId
  signals: Record<string, number>
  summary: string
  defaultOpen?: boolean
  single?: boolean
  children: ReactNode
}) {
  return (
    <div id={`settings-${id}`} className={single ? 'settings-fold settings-fold-single' : 'settings-fold'}>
      <Section
        title={FOLD_LABEL[id]}
        summary={summary}
        storageKey={`settings.${id}`}
        defaultOpen={defaultOpen}
        openSignal={signals[id] ?? 0}
      >
        {children}
      </Section>
    </div>
  )
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOverview | null>(null)
  const [error, setError] = useState<unknown>(null)
  // The routing card reloads after any key, endpoint or preference save here
  // (a replaced key keeps configured=true but clears its Test).
  const [routingToken, setRoutingToken] = useState(0)
  const bumpRouting = () => setRoutingToken((t) => t + 1)
  // Jump links: bump a section's signal (opens it), then scroll once it is open.
  const [signals, setSignals] = useState<Record<string, number>>({})
  const [jumpTo, setJumpTo] = useState<{ id: FoldId; n: number } | null>(null)
  function jump(id: FoldId) {
    setSignals((cur) => ({ ...cur, [id]: (cur[id] ?? 0) + 1 }))
    setJumpTo((cur) => ({ id, n: (cur?.n ?? 0) + 1 }))
  }
  useEffect(() => {
    if (!jumpTo) return
    const el = document.getElementById(`settings-${jumpTo.id}`)
    el?.scrollIntoView({ block: 'start' })
    el?.querySelector('summary')?.focus({ preventScroll: true })
  }, [jumpTo])

  useEffect(() => {
    // 403: not an admin. The admin cards stay hidden; Sharing below still shows.
    getSettings().then(setSettings, (e: unknown) => {
      if (!(e instanceof ApiError && e.status === 403)) setError(e)
    })
  }, [])

  async function toggle(key: SettingsToggleKey, value: boolean) {
    if (!settings) return
    const previous = settings
    setError(null)
    setSettings({ ...settings, [key]: value }) // optimistic
    try {
      setSettings(await updateSetting(key, value))
    } catch (e) {
      setSettings(previous) // roll back
      setError(e)
    }
  }

  async function setGpuMaxParallel(raw: number) {
    if (!settings || !Number.isFinite(raw)) return
    const value = clampGpuMaxParallel(raw)
    const previous = settings
    setError(null)
    setSettings({ ...settings, gpu_max_parallel: value }) // optimistic
    try {
      setSettings(await updateGpuMaxParallel(value))
    } catch (e) {
      setSettings(previous) // roll back
      setError(e)
    }
  }

  const prefProps = settings
    ? {
        settings,
        onSettings: (s: SettingsOverview) => {
          setSettings(s)
          bumpRouting()
        },
      }
    : null

  // Cards are grouped into folds: Jobs and Engines and keys start open, the
  // rest are folded and remember their state. Remote access and the
  // household's accounts live on the Admin page.
  const navIds: FoldId[] = prefProps
    ? ['jobs', 'engines', 'defaults', 'alerts', 'sharing', 'integrations', 'advanced', 'experimental']
    : ['sharing']
  return (
    <section className="panel page-narrow settings-page" aria-label="Settings">
      <h2>Settings</h2>
      <nav className="settings-jump" aria-label="Jump to a settings section">
        {navIds.map((id) => (
          <button key={id} type="button" className="settings-jump-link" onClick={() => jump(id)}>
            {FOLD_LABEL[id]}
          </button>
        ))}
      </nav>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {settings && prefProps && (
        <>
          <Fold id="jobs" signals={signals} summary="Background jobs and GPU" defaultOpen single>
            <Card title="Jobs" aria-label="Jobs">
              <div className="setting-list">
                {TOGGLES.map(({ key, label }) => (
                  <Field key={key} label={label} help={TOGGLE_HELP[key]}>
                    <Toggle checked={settings[key]} onChange={(next) => toggle(key, next)} />
                  </Field>
                ))}
                <Field label="GPU jobs at once" help={gpuMaxParallelHelp(settings.gpu_max_parallel)}>
                  <input
                    type="number"
                    min={1}
                    max={GPU_MAX_PARALLEL_MAX}
                    step={1}
                    value={settings.gpu_max_parallel}
                    disabled={!settings.gpu_limit_enabled}
                    onChange={(e) => setGpuMaxParallel(e.target.valueAsNumber)}
                  />
                </Field>
              </div>
            </Card>
          </Fold>
          <Fold id="engines" signals={signals} summary="Keys, tests and which engine does what">
            <EngineRoutingCard
              refreshToken={routingToken}
              settings={settings}
              onKey={(r) => {
                setSettings((cur) =>
                  cur ? { ...cur, engine_keys: { ...cur.engine_keys, [r.engine]: r.configured } } : cur,
                )
                bumpRouting()
              }}
              geminiFreeTier={settings.gemini_free_tier}
              onGeminiFreeTier={(next) => void toggle('gemini_free_tier', next)}
            />
          </Fold>
          <Fold id="defaults" signals={signals} summary="English variant, style note, monthly cap">
            <DefaultsCard {...prefProps} />
            <SpendingCard {...prefProps} />
          </Fold>
          <Fold id="alerts" signals={signals} summary="Notifications, automatic backups, app updates">
            <NotificationsSection />
            <AutoBackupCard />
            <AppUpdatesCard />
          </Fold>
        </>
      )}
      {/* Outside the settings gate: every signed-in person has a share-new-items choice
          and manages their own devices. */}
      <Fold id="sharing" signals={signals} summary="Share new items, signed-in devices">
        <SharingCard />
        <DevicesCard />
      </Fold>
      {settings && prefProps && (
        <>
          <Fold id="integrations" signals={signals} summary="Jellyfin, Notion, web search, browser extension">
            <JellyfinSection />
            <NotionSection />
            <WebSearchSection />
            <ExtensionSection />
          </Fold>
          <Fold id="advanced" signals={signals} summary="OCR, offline models, downloads and server addresses" single>
            <AdvancedCard {...prefProps} />
          </Fold>
          <Fold id="experimental" signals={signals} summary="Transcription experiments, Developer Mode">
            <TranscriptionExperimentsCard />
            <DeveloperModeCard />
          </Fold>
        </>
      )}
    </section>
  )
}
