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
 * Sections inside one "Advanced" Card.
 */
import { useState, type ReactNode } from 'react'

import { clearEndpointUrl, resetMonthCounter, setEndpointUrl, undoMonthCounterReset, updatePreferences } from '../../api/settings'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { humanize, humanizeValue } from '../../components/labels'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { EndpointName, SettingsOverview, SettingsPreferences } from '../../types/settings'
import {
  capSummary,
  changedPreferences,
  checkEndpointUrl,
  checkPath,
  cookiesSummary,
  ENDPOINTS,
  OCR_LABELS,
  parseCap,
  parseNumCtx,
  parseUploadMb,
  DEFAULT_UPLOAD_MB,
  SAVED_ON_PC_NOTE,
  type Parsed,
} from './preferences'

const grid = { display: 'grid', gap: 'var(--space-3)' } as const

type Props = { settings: SettingsOverview; onSettings: (s: SettingsOverview) => void }

function useCommon({ settings, onSettings }: Props) {
  const remote = usePcOnly() === 'remote'
  return { prefs: settings.preferences, remote, onSaved: onSettings }
}

export function DefaultsCard(props: Props) {
  const common = useCommon(props)
  const p = props.settings.preferences
  const c = props.settings.choices
  return (
    <PrefsSection
      {...common}
      as="card"
      title="Translation style"
      summary={`${humanize('locale', p.default_locale)}${p.default_style_note ? ' · style note' : ''}`}
      fromPrefs={(x) => ({
        default_locale: x.default_locale,
        default_style_note: x.default_style_note,
      })}
      toPatch={(d) => ({ ok: true, value: d as Partial<SettingsPreferences> })}
    >
      {(d, set) => (
        <>
          <Field label="English variant" help="Spelling for new translations. The Translate form starts with this.">
            <select value={String(d.default_locale)} onChange={(e) => set('default_locale', e.target.value)}>
              {c.locales.map((l) => (
                <option key={l} value={l}>{humanize('locale', l)}</option>
              ))}
            </select>
          </Field>
          <Field label="Style note" help="The Translate form's style note starts with this text.">
            <textarea rows={2} maxLength={2000} value={String(d.default_style_note)} onChange={(e) => set('default_style_note', e.target.value)} />
          </Field>
        </>
      )}
    </PrefsSection>
  )
}

export function SpendingCard(props: Props) {
  const common = useCommon(props)
  const { settings } = props
  const p = settings.preferences
  return (
    <PrefsSection
      {...common}
      as="card"
      title="Spending"
      summary={capSummary(p.monthly_cap_usd, settings.monthly_cap_env_usd)}
      fromPrefs={(x) => ({ monthly_cap_usd: x.monthly_cap_usd === null ? '' : String(x.monthly_cap_usd) })}
      toPatch={(d) => {
        const cap = parseCap(String(d.monthly_cap_usd))
        return cap.ok ? { ok: true, value: { monthly_cap_usd: cap.value } } : cap
      }}
    >
      {(d, set) => (
        <>
          <Field
            label="Monthly cap"
            unit="USD"
            help="Checked against the estimated spend logged this calendar month (UTC). A translation won't start once it is used up, and a running one stops cleanly, keeping finished lines. 0 means no cap; blank uses BAIHE_MONTHLY_CAP_USD from .env."
          >
            <input type="text" inputMode="decimal" value={String(d.monthly_cap_usd)} onChange={(e) => set('monthly_cap_usd', e.target.value)} placeholder={settings.monthly_cap_env_usd ? String(settings.monthly_cap_env_usd) : 'None'} />
          </Field>
          <p className="muted" data-testid="cap-effective">
            {settings.effective_monthly_cap_usd > 0
              ? `Cap in effect: $${settings.effective_monthly_cap_usd.toFixed(2)} a month.`
              : 'No monthly cap in effect.'}
          </p>
          <MonthCounter {...props} remote={common.remote} />
        </>
      )}
    </PrefsSection>
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

export function AdvancedCard(props: Props) {
  const common = useCommon(props)
  const { settings, onSettings } = props
  const p = settings.preferences
  const c = settings.choices
  return (
    <Card title="Advanced" meta="OCR, offline models, downloads, upload size and server addresses" aria-label="Advanced">
      <PrefsSection
        {...common}
        title="OCR"
        summary={OCR_LABELS[p.ocr_backend] ?? humanizeValue(p.ocr_backend)}
        fromPrefs={(x) => ({
          ocr_backend: x.ocr_backend,
          ocr_prefer_paddle_vl_manga: x.ocr_prefer_paddle_vl_manga,
          tesseract_cmd: x.tesseract_cmd,
        })}
        toPatch={(d) => pathPatch(d, ['tesseract_cmd'])}
      >
        {(d, set) => (
          <>
            <Field label="Default backend" help="Used where OCR runs without a per-page choice. Auto picks manga_ocr for Japanese, PaddleOCR for Chinese and Korean, Tesseract otherwise.">
              <select value={String(d.ocr_backend)} onChange={(e) => set('ocr_backend', e.target.value)}>
                {c.ocr_backends.map((b) => (
                  <option key={b} value={b}>{OCR_LABELS[b] ?? humanizeValue(b)}</option>
                ))}
              </select>
            </Field>
            <div className="setting-list">
              <Field label="Japanese: prefer PaddleOCR-VL" help="Only changes what Auto picks for Japanese. Leave off unless a side-by-side on your own pages shows it reads better than manga_ocr.">
                <Toggle checked={Boolean(d.ocr_prefer_paddle_vl_manga)} onChange={(next) => set('ocr_prefer_paddle_vl_manga', next)} />
              </Field>
            </div>
            <Field label="Tesseract program" help="Only needed if OCR says Tesseract is not installed or not on PATH after installing it. The full path to tesseract.exe on the Baihe PC. Blank if OCR already works.">
              <input type="text" spellCheck={false} value={String(d.tesseract_cmd)} onChange={(e) => set('tesseract_cmd', e.target.value)} placeholder="C:\Program Files\Tesseract-OCR\tesseract.exe" />
            </Field>
          </>
        )}
      </PrefsSection>
      <PrefsSection
        {...common}
        title="Offline and performance"
        summary={[
          p.whisper_model_path ? 'Whisper folder set' : 'Whisper downloads',
          p.ollama_num_ctx_override ? `num_ctx ${p.ollama_num_ctx_override}` : 'num_ctx auto',
        ].join(' · ')}
        fromPrefs={(x) => ({
          whisper_model_path: x.whisper_model_path,
          ollama_num_ctx_override: x.ollama_num_ctx_override ? String(x.ollama_num_ctx_override) : '',
        })}
        toPatch={(d) => {
          const n = parseNumCtx(String(d.ollama_num_ctx_override))
          if (!n.ok) return n
          const paths = pathPatch(d, ['whisper_model_path'])
          return paths.ok ? { ok: true, value: { ...paths.value, ollama_num_ctx_override: n.value } } : paths
        }}
      >
        {(d, set) => (
          <>
            <Field label="Offline Whisper model folder" help="For a PC that can't reach Hugging Face: a folder on the Baihe PC holding an already-downloaded faster-whisper model. Blank downloads the model on first use.">
              <input type="text" spellCheck={false} value={String(d.whisper_model_path)} onChange={(e) => set('whisper_model_path', e.target.value)} />
            </Field>
            <Field label="Ollama context window" unit="tokens" help="Blank or 0 sizes it from each prompt (recommended). A value here can only raise the window above that estimate, never lower it.">
              <input type="text" inputMode="numeric" value={String(d.ollama_num_ctx_override)} onChange={(e) => set('ollama_num_ctx_override', e.target.value)} placeholder="Auto" />
            </Field>
          </>
        )}
      </PrefsSection>
      <PrefsSection
        {...common}
        title="Downloads"
        summary={`Cookies: ${cookiesSummary(p.cookies_browser && humanizeValue(p.cookies_browser), p.cookies_file)}`}
        fromPrefs={(x) => ({ cookies_browser: x.cookies_browser ?? '', cookies_file: x.cookies_file, lncrawl_cmd: x.lncrawl_cmd })}
        toPatch={(d) => {
          const paths = pathPatch(d, ['cookies_file', 'lncrawl_cmd'])
          return paths.ok
            ? { ok: true, value: { ...paths.value, cookies_browser: String(d.cookies_browser) || null } }
            : paths
        }}
      >
        {(d, set) => (
          <>
            <p className="settings-note">
              Some sites block downloads unless you are signed in. yt-dlp can use your own browser
              login for video downloads from a URL and for Live capture started on this PC. Other
              devices never get these cookies.
            </p>
            <Field label="Cookies from browser" help="The downloader (yt-dlp) reads this browser's cookies on the Baihe PC.">
              <select value={String(d.cookies_browser)} onChange={(e) => set('cookies_browser', e.target.value)}>
                <option value="">None</option>
                {c.cookie_browsers.map((b) => (
                  <option key={b} value={b}>{humanizeValue(b)}</option>
                ))}
              </select>
            </Field>
            <Field label="Cookie file (cookies.txt)" help="The path to a cookies.txt file on the Baihe PC (export one with a browser add-on such as Get cookies.txt). Used instead of the browser above when set. Only the path is saved here, never the file's contents.">
              <input type="text" spellCheck={false} value={String(d.cookies_file)} onChange={(e) => set('cookies_file', e.target.value)} />
            </Field>
            <Field label="Novel downloader (lightnovel-crawler)" help="Only needed if you installed lightnovel-crawler (a separate program you install yourself) and it isn't on PATH. The full path to lncrawl on the Baihe PC; the file must be named lncrawl or lightnovel-crawler. Blank to find it on PATH.">
              <input type="text" spellCheck={false} value={String(d.lncrawl_cmd)} onChange={(e) => set('lncrawl_cmd', e.target.value)} placeholder="C:\Users\you\.local\bin\lncrawl.exe" />
            </Field>
          </>
        )}
      </PrefsSection>
      <PrefsSection
        {...common}
        title="Uploads"
        summary={`Limit ${settings.effective_upload_max_mb.toLocaleString('en-US')} MB${settings.upload_max_mb_from_env ? ' · set by the environment' : ''}`}
        fromPrefs={(x) => ({ max_upload_mb: String(x.max_upload_mb) })}
        toPatch={(d) => {
          const n = parseUploadMb(String(d.max_upload_mb), DEFAULT_UPLOAD_MB)
          return n.ok ? { ok: true, value: { max_upload_mb: n.value } } : n
        }}
      >
        {(d, set) => (
          <>
            {settings.upload_max_mb_from_env && (
              <p className="settings-note">Set by the environment, so it can't be changed here.</p>
            )}
            <Field
              label="Upload size limit (MB)"
              help={
                settings.upload_max_mb_from_env
                  ? `Set by the environment (BAIHE_MAX_UPLOAD_MB), so it is ${settings.effective_upload_max_mb.toLocaleString('en-US')} MB and can't be changed here. Remove the variable to use a saved limit.`
                  : `The largest audio or video file or backup you can upload to this PC. From 100 to 1,048,576 MB; blank uses ${DEFAULT_UPLOAD_MB.toLocaleString('en-US')} MB. The drive also needs room for the file. Other devices can't upload.`
              }
            >
              <input
                type="text"
                inputMode="numeric"
                value={settings.upload_max_mb_from_env ? String(settings.effective_upload_max_mb) : String(d.max_upload_mb)}
                disabled={settings.upload_max_mb_from_env}
                onChange={(e) => set('max_upload_mb', e.target.value)}
                placeholder={String(DEFAULT_UPLOAD_MB)}
              />
            </Field>
          </>
        )}
      </PrefsSection>
      <EndpointsSection settings={settings} remote={common.remote} onSettings={onSettings} />
    </Card>
  )
}

function pathPatch(d: Draft, keys: (keyof SettingsPreferences)[]): Parsed<Partial<SettingsPreferences>> {
  const value: Partial<SettingsPreferences> = {}
  for (const k of keys) {
    const raw = String(d[k] ?? '').trim()
    const problem = checkPath(raw)
    if (problem) return { ok: false, error: problem }
    ;(value as Record<string, unknown>)[k] = raw
  }
  return { ok: true, value: { ...(d as Partial<SettingsPreferences>), ...value } }
}

type Draft = Record<string, string | boolean | number | null>

type PrefsSectionProps = {
  as?: 'card' | 'section'
  title: string
  summary: string
  prefs: SettingsPreferences
  remote: boolean
  fromPrefs: (p: SettingsPreferences) => Draft
  toPatch: (d: Draft) => Parsed<Partial<SettingsPreferences>>
  onSaved: (s: SettingsOverview) => void
  children: (d: Draft, set: (key: string, value: string | boolean) => void) => ReactNode
}

function PrefsSection({ as = 'section', title, summary, prefs, remote, fromPrefs, toPatch, onSaved, children }: PrefsSectionProps) {
  const [draft, setDraft] = useState<Draft>(() => fromPrefs(prefs))
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  if (remote) {
    return (
      <Block as={as} title={title} summary={PC_ONLY_SUMMARY}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Block>
    )
  }

  const base = fromPrefs(prefs)
  const dirty = Object.keys(base).some((k) => base[k] !== draft[k])
  const set = (key: string, value: string | boolean) => {
    setDraft((cur) => ({ ...cur, [key]: value }))
    setNote(null)
    setProblem(null)
  }
  const save = () => {
    const parsed = toPatch(draft)
    if (!parsed.ok) {
      setProblem(parsed.error)
      return
    }
    const patch = changedPreferences(prefs, parsed.value)
    if (Object.keys(patch).length === 0) {
      setDraft(base)
      setNote('Nothing to change.')
      return
    }
    setBusy(true)
    setError(null)
    updatePreferences(patch).then(
      (s) => {
        setBusy(false)
        setDraft(fromPrefs(s.preferences))
        setNote('Saved.')
        onSaved(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(e)
      },
    )
  }

  return (
    <Block as={as} title={title} summary={summary}>
      <div style={grid}>
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
        {children(draft, set)}
        {problem && <p className="error" role="alert">{problem}</p>}
        <div className="settings-actions">
          {/* Save is a Card's main action; the Advanced Card holds several, so there it stays secondary. */}
          <button type="button" className={buttonClass(as === 'card' ? 'primary' : 'secondary')} disabled={busy || !dirty} onClick={save}>
            {busy ? 'Saving…' : 'Save'}
          </button>
          {dirty && !busy && (
            <button type="button" className={buttonClass('ghost')} onClick={() => { setDraft(base); setProblem(null) }}>
              Undo changes
            </button>
          )}
          <span className="muted" role="status">{note ?? ''}</span>
        </div>
      </div>
    </Block>
  )
}

// A Card (always open; the summary is its meta line) or a Section fold.
function Block({ as, title, summary, children }: { as: 'card' | 'section'; title: string; summary: string; children: ReactNode }) {
  if (as === 'card')
    return (
      <Card title={title} meta={summary} aria-label={title}>
        {children}
      </Card>
    )
  return (
    <Section title={title} summary={summary}>
      {children}
    </Section>
  )
}

function EndpointsSection({ settings, remote, onSettings }: { settings: SettingsOverview; remote: boolean; onSettings: (s: SettingsOverview) => void }) {
  const set = ENDPOINTS.filter((e) => settings.endpoints[e.name]).length
  const title = 'Server addresses'
  if (remote) {
    // Away from the PC the addresses aren't sent, but whether each is set is (engine_keys).
    const configured = ENDPOINTS.filter((e) => settings.engine_keys[e.name]).length
    return (
      <Section title={title} summary={`${configured} of ${ENDPOINTS.length} set · ${PC_ONLY_SUMMARY}`}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  return (
    <Section title={title} summary={`${set} of ${ENDPOINTS.length} set`}>
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
