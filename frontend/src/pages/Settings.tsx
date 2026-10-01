import { useEffect, useState } from 'react'
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
import { ApiKeysCard } from './settings/ApiKeysCard'
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

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOverview | null>(null)
  const [error, setError] = useState<unknown>(null)
  // The routing card reloads after any key, endpoint or preference save here
  // (a replaced key keeps configured=true but clears its Test).
  const [routingToken, setRoutingToken] = useState(0)
  const bumpRouting = () => setRoutingToken((t) => t + 1)

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

  // Always-open Cards for what people change on most visits; integrations
  // and experimental options sit in collapsed Sections at the end. Remote
  // access and the household's accounts live on the Admin page.
  return (
    <section className="panel page-narrow settings-page" aria-label="Settings">
      <h2>Settings</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {settings && prefProps && (
        <>
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
          <ApiKeysCard
            settings={settings}
            onKey={(r) => {
              setSettings((cur) =>
                cur ? { ...cur, engine_keys: { ...cur.engine_keys, [r.engine]: r.configured } } : cur,
              )
              bumpRouting()
            }}
          />
          <EngineRoutingCard
            refreshToken={routingToken}
            geminiFreeTier={settings.gemini_free_tier}
            onGeminiFreeTier={(next) => void toggle('gemini_free_tier', next)}
          />
          <DefaultsCard {...prefProps} />
          <SpendingCard {...prefProps} />
          <NotificationsSection />
          <AutoBackupCard />
          <AppUpdatesCard />
        </>
      )}
      {/* Outside the settings gate: every signed-in person has a share-new-items choice. */}
      <SharingCard />
      {/* Also outside it: every signed-in person manages their own devices. */}
      <DevicesCard />
      {settings && prefProps && (
        <>
          <Section title="Integrations" summary="Jellyfin, Notion, web search, browser extension" storageKey="settings.integrations">
            <JellyfinSection />
            <NotionSection />
            <WebSearchSection />
            <ExtensionSection />
          </Section>
          <AdvancedCard {...prefProps} />
          <Section title="Experimental & developer" summary="Transcription experiments, Developer Mode" storageKey="settings.experimental">
            <TranscriptionExperimentsCard />
            <DeveloperModeCard />
          </Section>
        </>
      )}
    </section>
  )
}
