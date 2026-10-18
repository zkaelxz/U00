import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError } from '../api/client'
import {
  clampGpuMaxParallel,
  fetchSettingsSchema,
  getSettings,
  GPU_MAX_PARALLEL_MAX,
  gpuMaxParallelHelp,
  TOGGLES,
  updateGpuMaxParallel,
  updateSetting,
} from '../api/settings'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { PageShell } from '../components/PageShell'
import { Field } from '../components/Field'
import { Toggle } from '../components/Toggle'
import { usePersistedState } from '../hooks/usePersistedState'
import { usePcOnly } from '../hooks/usePcOnly'
import { useSession } from '../hooks/useSession'
import { useRoute } from '../router'
import { AppUpdatesCard } from './settings/AppUpdatesCard'
import { DeveloperModeCard } from './settings/DeveloperModeCard'
import { DevicesCard } from './settings/DevicesCard'
import { AutoBackupCard } from './settings/AutoBackupCard'
import { EngineRoutingCard } from './settings/EngineRoutingCard'
import { ExtensionDevicesCard } from './settings/ExtensionDevicesCard'
import { ExtensionSection } from './settings/ExtensionSection'
import { LanguagePacksCard } from './settings/LanguagePacksCard'
import { JellyfinSection } from './settings/JellyfinSection'
import { NotificationsSection } from './settings/NotificationsSection'
import { OllamaKeyCard } from './settings/OllamaKeyCard'
import { showOllamaKey } from './settings/ollamaKeyModel'
import { PastCostsCard } from './settings/PastCostsCard'
import { SpendHistoryCard } from './settings/SpendHistoryCard'
import { CustomizeMenuCard } from './settings/CustomizeMenuCard'
import { DisplayCard } from './settings/DisplayCard'
import { AdvancedCard, DefaultsCard, KeepFreeMemory, SpendingCard } from './settings/PreferencesSections'
import { SharingCard } from './settings/SharingCard'
import { TimingSettingsCard } from './settings/TimingSettingsCard'
import { TranscriptionExperimentsCard } from './settings/TranscriptionExperimentsCard'
import { SaveFolderCard } from './manga/SaveFolder'
import { WebSearchSection } from './settings/WebSearchSection'
import type { SettingsOverview, SettingsSchemaRow, SettingsToggleKey } from '../types/settings'
import { filterSettings, PC_ONLY_CARDS, SETTINGS_INDEX, SETTINGS_TABS, tabFoldKeys, tabOfCard, type SettingsTab } from './settings/settingsIndex'
import { FoldControls, FoldSignalsProvider, SettingsCard, SettingsSearch, VisibleCardsProvider, type FoldSignals } from './settings/SettingsSearch'
import { Tabs } from '../components/Tabs'
import { seedPageTabFolds } from '../components/sectionStorage'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import './settings/settings.css'

// Members who get 403 on the settings call see only the cards of this tab.
const MEMBER_TAB: SettingsTab = 'sharing'
const MEMBER_CARDS = new Set(SETTINGS_TABS.find((t) => t.id === MEMBER_TAB)!.cards.map((c) => c.id))
const ALL_CARDS = new Set(SETTINGS_INDEX.map((entry) => entry.cardId))

const TOGGLE_HELP: Partial<Record<SettingsToggleKey, string>> = {
  gpu_limit_enabled:
    'Jobs beyond "GPU jobs at once" wait in line, so the GPU does not run out of memory. To keep graphics memory or RAM free for other programs, see Keep free memory below.',
  notify_on_completion: 'Shows a notification when a job finishes.',
  use_gpu: 'Transcribe on the GPU when there is one (faster).',
  unload_ollama_before_transcribe:
    'Ollama keeps its model in GPU memory for a few minutes after translating, which can make transcription run out of memory.',
  bulk_auto_resume:
    'Resumes interrupted translation batches at startup. Off by default: resumed batches can spend on your engine account.',
}

export default function SettingsPage() {
  useState(() => seedPageTabFolds('settings', Object.fromEntries(SETTINGS_TABS.map((t) => [t.id, tabFoldKeys(t.id)]))))
  const [settings, setSettings] = useState<SettingsOverview | null>(null)
  const [schema, setSchema] = useState<SettingsSchemaRow[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  // The routing card reloads after any key, endpoint or preference save here
  // (a replaced key keeps configured=true but clears its Test).
  const [routingToken, setRoutingToken] = useState(0)
  const bumpRouting = () => setRoutingToken((t) => t + 1)
  const [storedTab, setStoredTab] = usePersistedState<SettingsTab>('settings.tab', 'translation')
  // A search hit or a link opens its card, and Expand all / Collapse all act on the tab on screen: see FoldSignals.
  const [opens, setOpens] = useState<Record<string, number>>({})
  const [bulk, setBulk] = useState<FoldSignals['bulk']>(() =>
    Object.fromEntries(SETTINGS_TABS.map((t) => [t.id, { expand: 0, collapse: 0 }])) as FoldSignals['bulk'])
  const openCards = (ids: Iterable<string>) =>
    setOpens((cur) => {
      const next = { ...cur }
      for (const id of ids) next[id] = (next[id] ?? 0) + 1
      return next
    })
  const [query, setQuery] = useState('')
  // Bumped to open the Advanced card's Sections: on a search hit, and for the uploads deep link.
  const [advancedSignal, setAdvancedSignal] = useState(0)
  const [uploadsSignal, setUploadsSignal] = useState(0)
  const [jumpTo, setJumpTo] = useState<{ id: string; n: number } | null>(null)
  const route = useRoute()
  const wantsDeveloperMode = route.name === 'settings' && route.section === 'developer-mode'
  const wantsUploads = route.name === 'settings' && route.section === 'uploads'
  const loaded = settings !== null
  // Both targets are cards of the System tab, which only exists once the settings load.
  useEffect(() => {
    if (!loaded || (!wantsDeveloperMode && !wantsUploads)) return
    const id = wantsUploads ? 'advanced' : 'developer-mode'
    setStoredTab(tabOfCard(id)!)
    if (wantsUploads) setUploadsSignal((n) => n + 1)
    setOpens((cur) => ({ ...cur, [id]: (cur[id] ?? 0) + 1 }))
    setJumpTo((cur) => ({ id, n: (cur?.n ?? 0) + 1 }))
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
    fetchSettingsSchema().then(setSchema, (e: unknown) => {
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
        schema,
        onSettings: (s: SettingsOverview) => {
          setSettings(s)
          bumpRouting()
        },
      }
    : null

  // A card that renders nothing for this person must not draw search hits to its tab.
  const ownKeyShown = showOllamaKey(useSession())
  const remote = usePcOnly() === 'remote'
  const available: ReadonlySet<string> = useMemo(() => {
    const cards = loaded ? ALL_CARDS : MEMBER_CARDS
    return new Set([...cards].filter((id) => (ownKeyShown || id !== 'ollama-key') && !(remote && PC_ONLY_CARDS.has(id))))
  }, [loaded, ownKeyShown, remote])
  const shown = (id: string) => available.has(id)
  const searching = query.trim() !== ''
  const match = useMemo(() => filterSettings(query, available), [query, available])
  const visibleTabs = SETTINGS_TABS.filter((t) => (loaded || t.id === MEMBER_TAB) && t.cards.some((c) => shown(c.id)))
  const stored = visibleTabs.some((t) => t.id === storedTab) ? storedTab : visibleTabs[0].id
  // A search shows its hits even when they are on another tab; the saved tab is left alone.
  const active = searching && match.counts[stored] === 0
    ? (visibleTabs.find((t) => match.counts[t.id] > 0)?.id ?? stored)
    : stored
  const searchHitsAdvanced = searching && match.cardIds.has('advanced')
  useEffect(() => {
    if (searchHitsAdvanced) setAdvancedSignal((n) => n + 1)
  }, [searchHitsAdvanced, query])
  // A hit opens its card, even one the person closed before.
  useEffect(() => {
    if (searching) openCards(match.cardIds)
  }, [match, searching])
  const foldAll = (what: 'expand' | 'collapse') =>
    setBulk((cur) => ({ ...cur, [active]: { ...cur[active], [what]: cur[active][what] + 1 } }))
  const signals = useMemo(() => ({ opens, bulk }), [opens, bulk])

  const panel = (id: SettingsTab, children: ReactNode) => (
    <div key={id} id={tabPanelId('settings', id)} role="tabpanel" aria-labelledby={tabButtonId('settings', id)} className="settings-panel" hidden={active !== id}>
      {children}
    </div>
  )
  const notifySummary = settings ? `Notify when a job finishes: ${settings.notify_on_completion ? 'On' : 'Off'}` : undefined
  const jobsSummary = settings
    ? `${settings.use_gpu ? 'GPU on' : 'GPU off'} · ${
        settings.gpu_limit_enabled ? `${settings.gpu_max_parallel} GPU job${settings.gpu_max_parallel === 1 ? '' : 's'} at once` : 'no GPU job limit'
      }`
    : undefined
  // The public address check and the household's accounts live on the People and access page.
  return (
    <PageShell
      width="narrow"
      title="Settings"
      label="Settings"
      className="settings-page"
      tabs={(
        <>
          {loaded && <SettingsSearch query={query} onQuery={setQuery} total={searching ? match.total : null} />}
          {visibleTabs.length > 1 && (
            <Tabs
              label="Settings"
              idPrefix="settings"
              tabs={visibleTabs.map((t) => ({ id: t.id, label: t.label, count: searching ? match.counts[t.id] : undefined }))}
              active={active}
              onSelect={setStoredTab}
            />
          )}
        </>
      )}
    >
      <FoldControls onExpand={() => foldAll('expand')} onCollapse={() => foldAll('collapse')} />
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {searching && match.total === 0 && <p className="settings-note">No settings match “{query.trim()}”.</p>}
      <FoldSignalsProvider value={signals}>
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
            {shown('defaults') && <SettingsCard id="defaults"><DefaultsCard {...prefProps} /></SettingsCard>}
            <SettingsCard id="language-packs"><LanguagePacksCard /></SettingsCard>
          </>
        ))}
        {settings && prefProps && panel('costs', (
          <>
            {shown('spending') && <SettingsCard id="spending"><SpendingCard {...prefProps} /></SettingsCard>}
            {shown('spend-history') && <SettingsCard id="spend-history"><SpendHistoryCard /></SettingsCard>}
            {shown('past-costs') && <SettingsCard id="past-costs"><PastCostsCard /></SettingsCard>}
          </>
        ))}
        {settings && prefProps && panel('notifications', (
          <>
            {shown('notifications') && (
              <SettingsCard id="notifications" summary={notifySummary}>
                <NotificationsSection notifyToggle={<div className="setting-list">{toggleField('notify_on_completion')}</div>} />
              </SettingsCard>
            )}
            {shown('auto-backup') && <SettingsCard id="auto-backup"><AutoBackupCard /></SettingsCard>}
            {shown('save-folder') && <SettingsCard id="save-folder"><SaveFolderCard /></SettingsCard>}
            {shown('app-updates') && <SettingsCard id="app-updates"><AppUpdatesCard /></SettingsCard>}
          </>
        ))}
        {/* Outside the settings gate: every signed-in person has a share-new-items choice
            and manages their own devices and extension tokens. */}
        {panel('sharing', (
          <>
            <SettingsCard id="sharing"><SharingCard /></SettingsCard>
            <SettingsCard id="display"><DisplayCard /></SettingsCard>
            <SettingsCard id="customize-menu"><CustomizeMenuCard /></SettingsCard>
            <SettingsCard id="devices"><DevicesCard /></SettingsCard>
            <SettingsCard id="extension-devices"><ExtensionDevicesCard /></SettingsCard>
            <SettingsCard id="ollama-key"><OllamaKeyCard /></SettingsCard>
          </>
        ))}
        {settings && prefProps && panel('integrations', (
          <>
            {shown('jellyfin') && <SettingsCard id="jellyfin"><JellyfinSection /></SettingsCard>}
            {shown('web-search') && <SettingsCard id="web-search"><WebSearchSection /></SettingsCard>}
            {shown('extension') && <SettingsCard id="extension"><ExtensionSection /></SettingsCard>}
          </>
        ))}
        {settings && prefProps && panel('system', (
          <>
            {shown('performance') && (
              <SettingsCard id="performance" summary={jobsSummary}>
                <Card title="Jobs and GPU" aria-label="Jobs and GPU">
                  <div className="setting-list">
                    {toggleField('use_gpu')}
                    {toggleField('gpu_limit_enabled')}
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
                    {toggleField('unload_ollama_before_transcribe')}
                    {toggleField('bulk_auto_resume')}
                  </div>
                  <KeepFreeMemory {...prefProps} />
                </Card>
              </SettingsCard>
            )}
            {shown('advanced') && (
              <SettingsCard id="advanced">
                <AdvancedCard {...prefProps} openSignal={advancedSignal} uploadsSignal={uploadsSignal} />
              </SettingsCard>
            )}
            {shown('timing-splitting') && <SettingsCard id="timing-splitting"><TimingSettingsCard {...prefProps} /></SettingsCard>}
            {shown('transcription-experiments') && <SettingsCard id="transcription-experiments"><TranscriptionExperimentsCard /></SettingsCard>}
            <SettingsCard id="developer-mode"><DeveloperModeCard /></SettingsCard>
          </>
        ))}
      </VisibleCardsProvider>
      </FoldSignalsProvider>
    </PageShell>
  )
}
