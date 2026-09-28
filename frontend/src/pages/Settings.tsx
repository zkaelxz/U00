import { useEffect, useState } from 'react'
import { getSettings, TOGGLES, updateSetting } from '../api/settings'
import { ErrorBanner } from '../components/ErrorBanner'
import type { SettingsOverview, SettingsToggleKey } from '../types/settings'

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

  return (
    <section className="panel" aria-label="Settings">
      <h2>Settings</h2>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {settings && (
        <>
          <h3>Job options</h3>
          {TOGGLES.map(({ key, label }) => (
            <label key={key} style={{ display: 'block' }}>
              <input
                type="checkbox"
                checked={settings[key]}
                onChange={(e) => toggle(key, e.target.checked)}
              />{' '}
              {label}
            </label>
          ))}
          <h3>API keys configured</h3>
          <dl>
            {Object.entries(settings.engine_keys).map(([name, set]) => (
              <div key={name}>
                <dt>{name}</dt>
                <dd data-testid={`key-${name}`}>{set ? 'Yes' : 'No'}</dd>
              </div>
            ))}
          </dl>
          <p className="muted">
            API-key entry is not available in this UI yet (Slice 24, pending the loopback policy
            decision). Keys are never shown here, only whether one is configured.
          </p>
        </>
      )}
    </section>
  )
}
