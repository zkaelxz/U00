import { useEffect, useMemo, useState, type ReactNode } from 'react'
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
import { ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { Toggle } from '../components/Toggle'
import { usePersistedState } from '../hooks/usePersistedState'
import { useRoute } from '../router'
import { AppUpdatesCard } from './settings/AppUpdatesCard'
import { DeveloperModeCard } from './settings/DeveloperModeCard'
import { DevicesCard } from './settings/DevicesCard'
import { AutoBackupCard } from './settings/AutoBackupCard'
import { EngineRoutingCard } from './settings/EngineRoutingCard'
import { ExtensionDevicesCard } from './settings/ExtensionDevicesCard'
import { ExtensionSection } from './settings/ExtensionSection'
import { LoadedModelsCard } from './settings/LoadedModelsCard'
import { JellyfinSection } from './settings/JellyfinSection'
import { NotificationsSection } from './settings/NotificationsSection'
import { PastCostsCard } from './settings/PastCostsCard'
import { SpendHistoryCard } from './settings/SpendHistoryCard'
import { CustomizeMenuCard } from './settings/CustomizeMenuCard'
import { AdvancedCard, DefaultsCard, SpendingCard } from './settings/PreferencesSections'
import { SharingCard } from './settings/SharingCard'
import { TranscriptionExperimentsCard } from './settings/TranscriptionExperimentsCard'
import { SaveFolderCard } from './manga/SaveFolder'
import { WebSearchSection } from './settings/WebSearchSection'
import type { SettingsOverview, SettingsToggleKey } from '../types/settings'
import { filterSettings, SETTINGS_INDEX, SETTINGS_TABS, type SettingsTab } from './settings/settingsIndex'
import { SettingsCard, SettingsSearch, VisibleCardsProvider } from './settings/SettingsSearch'
import { panelId, SettingsTabs, tabId } from './settings/SettingsTabs'
import './settings/settings.css'

// Members who get 403 on the settings call see only these.
const MEMBER_CARDS = ['sharing', 'customize-menu', 'devices', 'extension-devices']
const ALL_CARDS = new Set(SETTINGS_INDEX.map((entry) => entry.cardId))

const TOGGLE_HELP: Partial<Record<SettingsToggleKey, string>> = {
  gpu_limit_enabled:
    'Jobs beyond "GPU jobs at once" wait in line, so the GPU does not run out of memory. To keep graphics memory or RAM free for other programs, see Advanced > Offline and performance.',
  notify_on_completion: 'Shows a notification when a job finishes.',
  use_gpu: 'Transcribe on the GPU when there is one (faster).',
  unload_ollama_before_transcribe:
    'Ollama keeps its model in GPU memory for a few minutes after translating, which can make transcription run out of memory.',
  bulk_auto_resume:
    'Resumes interrupted translation batches at startup. Off by default: resumed batches can spend on your engine account.',
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOverview | null>(null)
  const [error, setError] = useState<unknown>(null)
  // The routing card reloads after any key, endpoint or preference save here
  // (a replaced key keeps configured=true but clears its Test).
  const [routingToken, setRoutingToken] = useState(0)
  const bumpRouting = () => setRoutingToken((t) => t + 1)
  const [storedTab, setStoredTab] = usePersistedState<SettingsTab>('settings.tab', 'translation')
  const [query, setQuery] = useState('')
  // Bumped to open the Advanced card's Sections: on a search hit, and for the uploads deep link.
  const [advancedSignal, setAdvancedSignal] = useState(0)
  const [uploadsSignal, setUploadsSignal] = useState(0)
  const [jumpTo, setJumpTo] = useState<{ id: string; n: number } | null>(null)
  const route = useRoute()
  const wantsDeveloperMode = route.name === 'settings' && route.section === 'developer-mode'
  const wantsUploads = route.name === 'settings' && route.section === 'uploads'
  const loaded = settings !== null
  // Both targets sit on the System tab, which only exists once the settings load.
  useEffect(() => {
    if (!loaded || (!wantsDeveloperMode && !wantsUploads)) return
    setStoredTab('system')
    if (wantsUploads) setUploadsSignal((n) => n + 1)
    setJumpTo((cur) => ({ id: wantsUploads ? 'advanced' : 'developer-mode', n: (cur?.n ?? 0) + 1 }))
  }, [wantsDeveloperMode, wantsUploads, loaded, setStoredTab])
  useEffect(() => {
    if (!jumpTo) return
    const el = document.getElementById(`settings-card-${jumpTo.id}`)
    if (!el) return
    el.scrollIntoView({ block: 'start' })
    // Cards above the target finish loading after the jump and push it out of
    // view, so keep it aligned until the page stops growing or the person scrolls.
    const page = el.closest('.settings-page')
    if (!page || typeof ResizeObserver === 'undefined') return
    const realign = () => el.scrollIntoView({ block: 'start' })
    const observer = new ResizeObserver(realign)
    observer.observe(page)
    const stop = () => observer.disconnect()
    const timer = window.setTimeout(stop, 1500)
    const inputEvents = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const
    inputEvents.forEach((name) => window.addEventListener(name, stop, { once: true, passive: true }))
    return () => {
      stop()
      window.clearTimeout(timer)
      inputEvents.forEach((name) => window.removeEventListener(name, stop))
    }
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

  // No aria-label on the three Jobs cards: the card names below stay distinct from the Preferences and Translation cards.
  const toggleField = (key: SettingsToggleKey) => {
    const label = TOGGLES.find((t) => t.key === key)!.label
    return (
      <Field label={label} help={TOGGLE_HELP[key]}>
        <Toggle checked={settings![key]} onChange={(next) => toggle(key, next)} />
      </Field>
    )
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

  const available: ReadonlySet<string> = useMemo(() => (loaded ? ALL_CARDS : new Set(MEMBER_CARDS)), [loaded])
  const searching = query.trim() !== ''
  const match = useMemo(() => filterSettings(query, available), [query, available])
  const visibleTabs = SETTINGS_TABS.filter((t) => loaded || t.id === 'preferences')
  const stored = visibleTabs.some((t) => t.id === storedTab) ? storedTab : visibleTabs[0].id
  // A search shows its hits even when they are on another tab; the saved tab is left alone.
  const active = searching && match.counts[stored] === 0
    ? (visibleTabs.find((t) => match.counts[t.id] > 0)?.id ?? stored)
    : stored
  const searchHitsAdvanced = searching && match.cardIds.has('advanced')
  useEffect(() => {
    if (searchHitsAdvanced) setAdvancedSignal((n) => n + 1)
  }, [searchHitsAdvanced, query])

  const panel = (id: SettingsTab, children: ReactNode) => (
    <div key={id} id={panelId(id)} role="tabpanel" aria-labelledby={tabId(id)} className="settings-panel" hidden={active !== id}>
      {children}
    </div>
  )
  // Remote access and the household's accounts live on the Admin page.
  return (
    <section className="panel page-narrow settings-page" aria-label="Settings">
      <h2>Settings</h2>
      <SettingsSearch query={query} onQuery={setQuery} total={searching ? match.total : null} />
      {visibleTabs.length > 1 && (
        <SettingsTabs
          tabs={visibleTabs.map((t) => ({ ...t, count: searching ? match.counts[t.id] : undefined }))}
          active={active}
          onSelect={setStoredTab}
        />
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {searching && match.total === 0 && <p className="settings-note">No settings match “{query.trim()}”.</p>}
      <VisibleCardsProvider value={searching ? match.cardIds : null}>
        {settings && prefProps && panel('translation', (
          <>
            <SettingsCard id="engine-routing">
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
            </SettingsCard>
            <SettingsCard id="defaults"><DefaultsCard {...prefProps} /></SettingsCard>
            <SettingsCard id="spending"><SpendingCard {...prefProps} /></SettingsCard>
            <SettingsCard id="past-costs"><PastCostsCard /></SettingsCard>
            <SettingsCard id="spend-history"><SpendHistoryCard /></SettingsCard>
          </>
        ))}
        {panel('preferences', (
          <>
            {settings && prefProps && (
              <>
                <SettingsCard id="notify-toggle">
                  <Card title="Notify me">
                    <div className="setting-list">{toggleField('notify_on_completion')}</div>
                  </Card>
                </SettingsCard>
                <SettingsCard id="notifications"><NotificationsSection /></SettingsCard>
                <SettingsCard id="auto-backup"><AutoBackupCard /></SettingsCard>
                <SettingsCard id="save-folder"><SaveFolderCard /></SettingsCard>
                <SettingsCard id="app-updates"><AppUpdatesCard /></SettingsCard>
              </>
            )}
            {/* Outside the settings gate: every signed-in person has a share-new-items choice
                and manages their own devices and extension devices. */}
            <SettingsCard id="sharing"><SharingCard /></SettingsCard>
            <SettingsCard id="customize-menu"><CustomizeMenuCard /></SettingsCard>
            <SettingsCard id="devices"><DevicesCard /></SettingsCard>
            <SettingsCard id="extension-devices"><ExtensionDevicesCard /></SettingsCard>
            {settings && prefProps && (
              <>
                <SettingsCard id="jellyfin"><JellyfinSection /></SettingsCard>
                <SettingsCard id="web-search"><WebSearchSection /></SettingsCard>
                <SettingsCard id="extension"><ExtensionSection /></SettingsCard>
              </>
            )}
          </>
        ))}
        {settings && prefProps && panel('system', (
          <>
            <SettingsCard id="performance">
              <Card title="Performance">
                <div className="setting-list">
                  {toggleField('gpu_limit_enabled')}
                  {toggleField('use_gpu')}
                  {toggleField('unload_ollama_before_transcribe')}
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
            </SettingsCard>
            <SettingsCard id="loaded-models"><LoadedModelsCard /></SettingsCard>
            <SettingsCard id="auto-resume">
              <Card title="Batch resume">
                <div className="setting-list">{toggleField('bulk_auto_resume')}</div>
              </Card>
            </SettingsCard>
            <SettingsCard id="advanced">
              <AdvancedCard {...prefProps} openSignal={advancedSignal} uploadsSignal={uploadsSignal} />
            </SettingsCard>
            <SettingsCard id="transcription-experiments"><TranscriptionExperimentsCard /></SettingsCard>
            <SettingsCard id="developer-mode"><DeveloperModeCard /></SettingsCard>
            <SettingsCard id="ports">
              <Card title="Ports" meta="Which ports Baihe listens on">
                <div className="settings-actions">
                  <ButtonLink href="#/diagnostics" variant="ghost">Open Ports on Diagnostics</ButtonLink>
                </div>
              </Card>
            </SettingsCard>
          </>
        ))}
      </VisibleCardsProvider>
    </section>
  )
}
