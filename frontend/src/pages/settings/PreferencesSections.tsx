/*
 * Settings > the persisted PC-side preferences (inventory G05, G06 URLs,
 * G08, G09, G13, G14, G15). The theme (G03) is the header button, not a setting here.
 * The preferences live on the Baihe PC (db app_settings, endpoint URLs in
 * .env) and are used by the server's translate, transcribe, OCR and
 * download jobs and by the CLI. Changing them is PC only; away from the PC
 * each block shows "PC only".
 *
 * Layout (UI refresh §3.12): Defaults and Spending are Cards;
 * OCR, offline, downloads and server addresses are rare, so they are
 * Sections inside one "Advanced" Card. Their fields are generated from the
 * declared settings (SchemaCard); the server addresses are .env values and
 * stay hand-written.
 */
import { useState } from 'react'

import { clearEndpointUrl, resetMonthCounter, setEndpointUrl, undoMonthCounterReset } from '../../api/settings'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { humanize, humanizeValue } from '../../components/labels'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { EndpointName, SettingsOverview, SettingsSchemaRow } from '../../types/settings'
import {
  capSummary,
  checkEndpointUrl,
  cookiesSummary,
  ENDPOINTS,
  OCR_LABELS,
  SAVED_ON_PC_NOTE,
} from './preferences'
import { SchemaCard } from './SchemaCard'

const grid = { display: 'grid', gap: 'var(--space-3)' } as const

type Props = {
  settings: SettingsOverview
  onSettings: (s: SettingsOverview) => void
  schema: SettingsSchemaRow[] | null
}
// Bumping a signal opens the Advanced card's Sections: `openSignal` all of them (a search hit),
// `uploadsSignal` only Uploads (the Source stage's link). Section reacts to any change of its number.
type AdvancedProps = Props & { openSignal?: number; uploadsSignal?: number }

export function DefaultsCard({ schema, settings, onSettings }: Props) {
  const p = settings.preferences
  return (
    <SchemaCard
      schema={schema}
      settings={settings}
      onSettings={onSettings}
      section="Translation style"
      as="card"
      title="Translation style"
      summary={`${humanize('locale', p.default_locale)}${p.default_style_note ? ' · style note' : ''}`}
    />
  )
}

export function SpendingCard(props: Props) {
  const { schema, settings, onSettings } = props
  const remote = usePcOnly() === 'remote'
  return (
    <SchemaCard
      schema={schema}
      settings={settings}
      onSettings={onSettings}
      section="Spending"
      as="card"
      title="Spending"
      summary={capSummary(settings.preferences.monthly_cap_usd, settings.monthly_cap_env_usd)}
      placeholderOverride={{ monthly_cap_usd: settings.monthly_cap_env_usd ? String(settings.monthly_cap_env_usd) : 'None' }}
      after={
        <>
          <p className="muted" data-testid="cap-effective">
            {settings.effective_monthly_cap_usd > 0
              ? `Cap in effect: $${settings.effective_monthly_cap_usd.toFixed(2)} a month.`
              : 'No monthly cap in effect.'}
          </p>
          <MonthCounter {...props} remote={remote} />
        </>
      }
    />
  )
}

// Outside the preferences form: the reset is its own PC-only action, not a saved field.
function MonthCounter({ settings, onSettings, remote }: Props & { remote: boolean }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const cap = settings.effective_monthly_cap_usd
  const run = (work: typeof resetMonthCounter) => {
    setBusy(true)
    setError(null)
    work().then(
      (r) => {
        onSettings({ ...settings, ...r.after })
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }
  const resetAt = settings.month_spend_reset_at
  return (
    <div data-testid="month-counter">
      <p data-testid="month-spend">
        This month: ${settings.month_spend_usd.toFixed(2)}
        {resetAt ? `, counted toward the cap since reset: $${settings.month_spend_counted_usd.toFixed(2)}` : ''}
      </p>
      {resetAt ? (
        <p className="muted" data-testid="month-reset-at">
          Counter reset on {new Date(resetAt + 'Z').toLocaleString()}.
        </p>
      ) : null}
      {error ? <ErrorBanner error={error} /> : null}
      {remote ? null : (
        <div className="actions">
          <ConfirmButton
            name="this month's counter"
            label="Reset this month's counter…"
            verb="reset"
            tone="primary"
            busy={busy}
            onConfirm={() => run(resetMonthCounter)}
          />
          {resetAt ? (
            <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => run(undoMonthCounterReset)}>
              Undo reset
            </button>
          ) : null}
        </div>
      )}
      {remote ? null : (
        <p className="muted">
          Keeps your history, starts counting from now.{' '}
          {cap > 0 ? `The cap stays at $${cap.toFixed(2)}.` : 'No cap is set.'}
        </p>
      )}
    </div>
  )
}

// How the Advanced section's rows fold. A row of the section that no fold names lands in
// "Other", so a newly declared setting shows up without touching this list.
const ADVANCED_FOLDS: { title: string; keys: string[] }[] = [
  { title: 'OCR', keys: ['ocr_backend', 'ocr_prefer_paddle_vl_manga', 'tesseract_cmd'] },
  { title: 'Offline and performance', keys: ['whisper_model_path', 'ollama_num_ctx_override', 'keep_free_vram_gb', 'keep_free_ram_gb'] },
  { title: 'Downloads', keys: ['cookies_browser', 'cookies_file', 'lncrawl_cmd'] },
  { title: 'Uploads', keys: ['max_upload_mb'] },
]

export function AdvancedCard(props: AdvancedProps) {
  const remote = usePcOnly() === 'remote'
  const { schema, settings, onSettings, openSignal = 0, uploadsSignal = 0 } = props
  const p = settings.preferences
  const named = new Set(ADVANCED_FOLDS.flatMap((f) => f.keys))
  const other = (schema ?? []).filter((r) => r.section === 'Advanced' && !r.custom && !r.dev_only && !named.has(r.key)).map((r) => r.key)
  const common = { schema, settings, onSettings, section: 'Advanced' }
  const summaries: Record<string, string> = {
    OCR: OCR_LABELS[p.ocr_backend] ?? humanizeValue(p.ocr_backend),
    'Offline and performance': [
      p.whisper_model_path ? 'Whisper folder set' : 'Whisper downloads',
      p.ollama_num_ctx_override ? `num_ctx ${p.ollama_num_ctx_override}` : 'num_ctx auto',
      p.keep_free_vram_gb || p.keep_free_ram_gb ? 'Memory kept free' : 'No memory reserve',
    ].join(' · '),
    Downloads: `Cookies: ${cookiesSummary(p.cookies_browser && humanizeValue(p.cookies_browser), p.cookies_file)}`,
    Uploads: `Limit ${settings.effective_upload_max_mb.toLocaleString('en-US')} MB${settings.upload_max_mb_from_env ? ' · set by the environment' : ''}`,
  }
  return (
    <Card title="Advanced" meta="OCR, offline models, memory to keep free, downloads, uploads, server addresses" aria-label="Advanced">
      {ADVANCED_FOLDS.map((fold) => (
        <SchemaCard
          key={fold.title}
          {...common}
          keys={fold.keys}
          title={fold.title}
          summary={summaries[fold.title]}
          openSignal={fold.title === 'Uploads' ? openSignal + uploadsSignal : openSignal}
          {...(fold.title === 'Downloads' ? { before: DOWNLOADS_NOTE } : {})}
          {...(fold.title === 'Uploads' ? uploadsEnv(settings) : {})}
        />
      ))}
      {other.length > 0 && (
        <SchemaCard {...common} keys={other} title="Other" summary={`${other.length} setting${other.length === 1 ? '' : 's'}`} openSignal={openSignal} />
      )}
      <EndpointsSection settings={settings} remote={remote} onSettings={onSettings} openSignal={openSignal} />
    </Card>
  )
}

const DOWNLOADS_NOTE = (
  <p className="settings-note">
    Some sites block downloads unless you are signed in. yt-dlp can use your browser login
    for video URL downloads and Live capture started on this PC. Other devices never get
    these cookies.
  </p>
)

// BAIHE_MAX_UPLOAD_MB wins over the saved limit, so the field shows it and cannot be edited.
function uploadsEnv(settings: SettingsOverview) {
  if (!settings.upload_max_mb_from_env) return {}
  const mb = settings.effective_upload_max_mb
  return {
    before: <p className="settings-note">Set by the environment, so it can't be changed here.</p>,
    locked: { max_upload_mb: String(mb) },
    helpOverride: {
      max_upload_mb: `Set by BAIHE_MAX_UPLOAD_MB (${mb.toLocaleString('en-US')} MB), so it can't be changed here. Remove the variable to use a saved limit.`,
    },
  }
}

function EndpointsSection({ settings, remote, onSettings, openSignal }: { settings: SettingsOverview; remote: boolean; onSettings: (s: SettingsOverview) => void; openSignal: number }) {
  const set = ENDPOINTS.filter((e) => settings.endpoints[e.name]).length
  const title = 'Server addresses'
  if (remote) {
    // Away from the PC the addresses aren't sent, but whether each is set is (engine_keys).
    const configured = ENDPOINTS.filter((e) => settings.engine_keys[e.name]).length
    return (
      <Section title={title} openSignal={openSignal} summary={`${configured} of ${ENDPOINTS.length} set · ${PC_ONLY_SUMMARY}`}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  return (
    <Section title={title} openSignal={openSignal} summary={`${set} of ${ENDPOINTS.length} set`}>
      <div style={grid}>
        <p className="settings-note">
          Addresses of local servers Baihe talks to. {SAVED_ON_PC_NOTE}
        </p>
        {ENDPOINTS.map((e) => (
          <EndpointForm
            key={e.name}
            name={e.name}
            label={e.label}
            help={e.help}
            placeholder={e.placeholder}
            current={settings.endpoints[e.name]}
            configured={Boolean(settings.engine_keys[e.name])}
            onResult={(name, url, configured) =>
              onSettings({
                ...settings,
                endpoints: { ...settings.endpoints, [name]: url },
                engine_keys: { ...settings.engine_keys, [name]: configured },
              })
            }
          />
        ))}
      </div>
    </Section>
  )
}

type EndpointFormProps = {
  name: EndpointName
  label: string
  help: string
  placeholder: string
  current: string | null
  configured: boolean
  onResult: (name: EndpointName, url: string | null, configured: boolean) => void
}

function EndpointForm({ name, label, help, placeholder, current, configured, onResult }: EndpointFormProps) {
  const [draft, setDraft] = useState(current ?? '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  const run = (call: () => Promise<{ url: string | null; configured: boolean }>, done: string) => {
    setBusy(true)
    setError(null)
    setNote(null)
    call().then(
      (r) => {
        setBusy(false)
        setDraft(r.url ?? '')
        setNote(done)
        onResult(name, r.url, r.configured)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }
  const save = () => {
    const problem = checkEndpointUrl(draft)
    setFieldError(problem)
    if (!problem) run(() => setEndpointUrl(name, draft.trim()), 'Saved.')
  }
  const clearNote = configured && !current ? 'Set in .env in a form not shown here.' : null

  return (
    <div style={{ display: 'grid', gap: 'var(--space-2)' }} data-testid={`endpoint-${name}`}>
      <Field label={label} help={help} error={fieldError}>
        <input
          type="url"
          inputMode="url"
          spellCheck={false}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => {
            setDraft(e.target.value)
            setFieldError(null)
            setNote(null)
          }}
        />
      </Field>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
      <div className="settings-actions">
        <button type="button" className={buttonClass('secondary')} disabled={busy || !draft.trim() || draft.trim() === current} onClick={save}>
          Save
        </button>
        <button type="button" className={buttonClass('ghost')} disabled={busy || !configured} onClick={() => run(() => clearEndpointUrl(name), 'Cleared.')}>
          Clear
        </button>
        <span className="muted" role="status">{note ?? clearNote ?? ''}</span>
      </div>
    </div>
  )
}
