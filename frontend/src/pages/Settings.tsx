import { useEffect, useState } from 'react'
import { getSettings, TOGGLES, updateSetting } from '../api/settings'
import { ErrorBanner } from '../components/ErrorBanner'
import { Field } from '../components/Field'
import { Section } from '../components/Section'
import { Toggle } from '../components/Toggle'
import { SettingsKeyForm } from './SettingsKeyForm'
import { ExtensionSection } from './settings/ExtensionSection'
import { NotificationsSection } from './settings/NotificationsSection'
import { PreferencesSections } from './settings/PreferencesSections'
import { SECRET_ENGINES } from './settingsKeys'
import type { SettingsOverview, SettingsToggleKey } from '../types/settings'

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

  const keyNames = settings ? Object.keys(settings.engine_keys) : []
  const missing = settings ? keyNames.filter((n) => !settings.engine_keys[n]) : []
  const configured = keyNames.length - missing.length
  const total = keyNames.length

  return (
    <section className="panel" aria-label="Settings">
      <h2>Settings</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {settings && (
        <>
          <div className="setting-list">
            {TOGGLES.map(({ key, label }) => (
              <Field key={key} label={label} help={TOGGLE_HELP[key]}>
                <Toggle checked={settings[key]} onChange={(next) => toggle(key, next)} />
              </Field>
            ))}
          </div>
          <Section
            storageKey="settings.api-keys"
            title="API keys configured"
            summary={`${configured} of ${total} configured${missing.length ? `, missing ${missing.join(', ')}` : ''}`}
          >
            <p className="muted">
              Keys are saved to .env on the Baihe PC and never shown again, only whether one is
              configured. Setting keys works only on that PC (on when started with start.bat; otherwise set
              BAIHE_API_ALLOW_KEY_WRITES=1). You can also edit .env.
            </p>
            {SECRET_ENGINES.filter(({ engine }) => engine in settings.engine_keys).map(
              ({ engine, label }) => (
                <SettingsKeyForm
                  key={engine}
                  engine={engine}
                  label={label}
                  configured={settings.engine_keys[engine]}
                  onResult={(r) =>
                    setSettings((cur) =>
                      cur
                        ? { ...cur, engine_keys: { ...cur.engine_keys, [r.engine]: r.configured } }
                        : cur,
                    )
                  }
                />
              ),
            )}
            <dl>
              {Object.entries(settings.engine_keys)
                .filter(([name]) => !SECRET_ENGINES.some((e) => e.engine === name))
                .map(([name, set]) => (
                  <div key={name}>
                    <dt>{name}</dt>
                    <dd data-testid={`key-${name}`}>{set ? 'Yes' : 'No'}</dd>
                  </div>
                ))}
            </dl>
          </Section>
          <PreferencesSections settings={settings} onSettings={setSettings} />
          <NotificationsSection />
          <ExtensionSection />
        </>
      )}
    </section>
  )
}
