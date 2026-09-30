import { useEffect, useState } from 'react'
import { getSettings, TOGGLES, updateSetting } from '../api/settings'
import { Card } from '../components/Card'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { Toggle } from '../components/Toggle'
import { ApiKeysCard } from './settings/ApiKeysCard'
import { ExtensionSection } from './settings/ExtensionSection'
import { JellyfinSection } from './settings/JellyfinSection'
import { NotificationsSection } from './settings/NotificationsSection'
import { AdvancedCard, AppearanceCard, DefaultsCard, SpendingCard } from './settings/PreferencesSections'
import { WebSearchSection } from './settings/WebSearchSection'
import type { SettingsOverview, SettingsToggleKey } from '../types/settings'
import './settings/settings.css'

const TOGGLE_HELP: Record<SettingsToggleKey, string> = {
  gpu_limit_enabled: 'Runs GPU-heavy jobs one at a time so they do not run out of memory.',
  notify_on_completion: 'Shows a notification when a background job finishes.',
  use_gpu: 'Transcribe on the graphics card when one is available (faster).',
  gemini_free_tier: 'Slows Gemini requests to stay inside the free tier rate limits.',
}

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsOverview | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    getSettings().then(setSettings, setError)
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

  const prefProps = settings ? { settings, onSettings: setSettings } : null

  // Layout (UI refresh §3.12): always-open Cards for what people change on
  // most visits; rare options sit in Sections inside the "Advanced" Card.
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
            </div>
          </Card>
          <ApiKeysCard
            settings={settings}
            onKey={(r) =>
              setSettings((cur) =>
                cur ? { ...cur, engine_keys: { ...cur.engine_keys, [r.engine]: r.configured } } : cur,
              )
            }
          />
          <DefaultsCard {...prefProps} />
          <SpendingCard {...prefProps} />
          <NotificationsSection />
          <JellyfinSection />
          <WebSearchSection />
          <ExtensionSection />
          <AppearanceCard />
          <AdvancedCard {...prefProps} />
        </>
      )}
    </section>
  )
}
