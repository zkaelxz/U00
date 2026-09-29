/*
 * Settings > API keys (UI refresh §3.12): one row per engine with a Set /
 * Missing badge, always visible. "Set key" / "Replace" opens that engine's
 * SettingsKeyForm in place. Keys are write-only: the page only ever learns
 * whether one is configured. Writing is PC only, so away from the PC the
 * rows show status and one muted line instead of the buttons.
 */
import { useState } from 'react'

import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { EngineKeyResult, SettingsOverview } from '../../types/settings'
import { SettingsKeyForm } from '../SettingsKeyForm'
import { keyRows } from '../settingsKeys'

type Props = { settings: SettingsOverview; onKey: (r: EngineKeyResult) => void }

export function ApiKeysCard({ settings, onKey }: Props) {
  const remote = usePcOnly() === 'remote'
  const [open, setOpen] = useState<string | null>(null)
  const rows = keyRows(settings.engine_keys)
  const set = rows.filter((r) => settings.engine_keys[r.engine]).length

  return (
    <Card title="API keys" meta={`${set} of ${rows.length} set`} aria-label="API keys">
      <ul className="status-list" aria-label="API keys">
        {rows.map(({ engine, label, writable }) => {
          const configured = settings.engine_keys[engine]
          const expanded = open === engine
          return (
            <li key={engine}>
              <div className="status-row">
                <span className="status-row-name">{label}</span>
                <span data-testid={`key-${engine}`}>
                  <Badge tone={configured ? 'ok' : 'neutral'}>{configured ? 'Set' : 'Missing'}</Badge>
                </span>
                {writable && !remote && (
                  <button
                    type="button"
                    className={buttonClass(expanded ? 'ghost' : 'secondary', 'sm')}
                    aria-expanded={expanded}
                    aria-label={expanded ? `Close ${label} key` : `${configured ? 'Replace' : 'Set'} ${label} key`}
                    onClick={() => setOpen(expanded ? null : engine)}
                  >
                    {expanded ? 'Close' : configured ? 'Replace' : 'Set key'}
                  </button>
                )}
              </div>
              {expanded && (
                <SettingsKeyForm engine={engine} label={label} configured={configured} onResult={onKey} />
              )}
            </li>
          )
        })}
      </ul>
      <p className="settings-note">
        {remote
          ? 'Setting keys is PC only.'
          : 'Keys are saved to .env on the Baihe PC and never shown again. Setting them works only on that PC (on when started with start.bat; otherwise set BAIHE_API_ALLOW_KEY_WRITES=1). You can also edit .env.'}
      </p>
    </Card>
  )
}
