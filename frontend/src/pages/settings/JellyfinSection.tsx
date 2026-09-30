/*
 * Settings > Jellyfin (roadmap Step 39): the optional connector, off by
 * default. PC only. The server address and library folder are plain
 * settings; the API key is write-only (only "Set/Missing" ever comes back)
 * and, like engine keys, needs key writes turned on at the PC.
 * "Scan library" is a read-only report: nothing changes on the server.
 */
import { useEffect, useState } from 'react'

import {
  clearJellyfinKey, getJellyfinConfig, saveJellyfinConfig, scanJellyfin, setJellyfinKey, testJellyfin,
} from '../../api/jellyfin'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { JellyfinConfig, JellyfinLanguage, JellyfinScanReport } from '../../types/jellyfin'
import { itemLabel, jellyfinErrorMessage, jellyfinSummary, scanSummary } from './jellyfin'

const TITLE = 'Jellyfin'
const LANGS: [JellyfinLanguage, string][] = [
  ['en', 'English'],
  ['zh', 'Chinese'],
  ['ja', 'Japanese'],
  ['ko', 'Korean'],
]

export function JellyfinSection() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <JellyfinControls />
}

function JellyfinControls() {
  const [cfg, setCfg] = useState<JellyfinConfig | null>(null)
  const [url, setUrl] = useState('')
  const [folder, setFolder] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lang, setLang] = useState<JellyfinLanguage>('en')
  const [report, setReport] = useState<JellyfinScanReport | null>(null)

  const load = (c: JellyfinConfig) => {
    setCfg(c)
    setUrl(c.server_url ?? '')
    setFolder(c.library_dir ?? '')
  }

  // Wait for /api/meta first, so a viewer away from the PC makes no calls here.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getJellyfinConfig().then(
        (c) => live && load(c),
        (e: unknown) => live && setError(jellyfinErrorMessage(e)),
      )
    })
    return () => {
      live = false
    }
  }, [])

  const call = <T,>(p: Promise<T>, done: (r: T) => void, keyWrite = false) => {
    setBusy(true)
    setNote(null)
    setError(null)
    p.then(
      (r) => {
        done(r)
        setBusy(false)
      },
      (e: unknown) => {
        setError(jellyfinErrorMessage(e, keyWrite))
        setBusy(false)
      },
    )
  }

  const save = () => {
    const urlChanged = url.trim() !== (cfg?.server_url ?? '')
    const body = urlChanged
      ? { server_url: url.trim(), library_dir: folder.trim(), confirm: true }
      : { library_dir: folder.trim() }
    call(saveJellyfinConfig(body), (c) => {
      load(c)
      setNote('Saved.')
    }, urlChanged)
  }
  const toggle = (enabled: boolean) => call(saveJellyfinConfig({ enabled }), load)
  const saveKey = () => {
    const value = key.trim()
    setKey('') // the request already holds it; drop it now
    call(setJellyfinKey(value), (c) => {
      load(c)
      setNote('Key saved.')
    }, true)
  }
  const test = () => call(testJellyfin(), (r) => setNote(`Connected to ${r.server_name || 'Jellyfin'} ${r.version}.`))
  const scan = () => call(scanJellyfin(lang), setReport)

  const dirty = !!cfg && (url.trim() !== (cfg.server_url ?? '') || folder.trim() !== (cfg.library_dir ?? ''))

  return (
    <Card
      title={TITLE}
      meta={cfg ? jellyfinSummary(cfg) : undefined}
      aria-label={TITLE}
      actions={cfg && <Toggle checked={cfg.enabled} disabled={busy} onChange={toggle} aria-label="Use Jellyfin" />}
    >
      <p className="settings-note">
        Optional. Puts finished subtitles into your Jellyfin library next to the video, named so Jellyfin picks
        them up, then asks it to rescan. Off by default.
      </p>
      {!cfg ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <ul className="status-list" aria-label="Jellyfin setup">
          <li>
            <div className="status-row">
              <span className="status-row-name">Server</span>
            </div>
            <div className="status-form">
              <Field label="Server address" help="For example http://localhost:8096 or http://192.168.1.20:8096. Changing it works only on the Baihe PC with key writes on (it decides where the key is sent).">
                <input type="url" value={url} placeholder="http://localhost:8096" onChange={(e) => setUrl(e.target.value)} />
              </Field>
              <Field label="Library folder" help="The folder on this PC that Jellyfin reads (the same one set in Jellyfin's library). Files are only ever written inside it.">
                <input type="text" value={folder} placeholder="D:\Media\Dramas" onChange={(e) => setFolder(e.target.value)} />
              </Field>
              <div className="settings-actions">
                <button type="button" className={buttonClass('primary', 'sm')} disabled={busy || !dirty} onClick={save}>
                  Save
                </button>
                <button type="button" className={buttonClass('secondary', 'sm')}
                  disabled={busy || !cfg.server_url || !cfg.key_configured} onClick={test}>
                  Test connection
                </button>
              </div>
            </div>
          </li>
          <li>
            <div className="status-row">
              <span className="status-row-name">API key</span>
              <span data-testid="jellyfin-key">
                <Badge tone={cfg.key_configured ? 'ok' : 'neutral'}>{cfg.key_configured ? 'Set' : 'Missing'}</Badge>
              </span>
            </div>
            <div className="status-form">
              <Field label={cfg.key_configured ? 'Replace API key' : 'API key'} help="In Jellyfin: Dashboard, API Keys, add one for Baihe. It is saved to .env on this PC and never shown again.">
                <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
              </Field>
              <div className="settings-actions">
                <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !key.trim()} onClick={saveKey}>
                  Save key
                </button>
                {cfg.key_configured && (
                  <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy}
                    onClick={() => call(clearJellyfinKey(), load, true)}>
                    Remove key
                  </button>
                )}
              </div>
            </div>
          </li>
          {cfg.enabled && (
            <li>
              <div className="status-row">
                <span className="status-row-name">Library scan</span>
              </div>
              <div className="status-form">
                <Field label="Subtitle language" help="Scan counts the videos that have no subtitle in this language.">
                  <select value={lang} onChange={(e) => setLang(e.target.value as JellyfinLanguage)}>
                    {LANGS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
                  </select>
                </Field>
                <div className="settings-actions">
                  <button type="button" className={buttonClass('secondary', 'sm')}
                    disabled={busy || !cfg.server_url || !cfg.key_configured} onClick={scan}>
                    {busy && !report ? 'Scanning…' : 'Scan library'}
                  </button>
                </div>
              </div>
            </li>
          )}
        </ul>
      )}
      {cfg && report && (
        <div data-testid="jellyfin-report">
          <p>{scanSummary(report)} Nothing was changed.</p>
          {report.items.length > 0 && (
            <ul aria-label="Missing subtitles" className="jellyfin-missing">
              {report.items.slice(0, 50).map((i) => (
                <li key={i.id}>
                  {itemLabel(i)}
                  {!i.writable && <span className="muted"> · outside the library folder</span>}
                </li>
              ))}
            </ul>
          )}
          {report.items.length > 50 && <p className="muted">…and {report.items.length - 50} more.</p>}
          <p className="settings-note">
            To add subtitles, open the matching drama in Baihe and use Export, Send to Jellyfin.
          </p>
        </div>
      )}
      {note && <p role="status">{note}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </Card>
  )
}
