/*
 * Settings > Notion (roadmap item 112): where "Export to Notion" puts a
 * drama's page. PC only. The page/database is a plain setting; the
 * integration token is write-only (only "Token saved" ever comes back) and,
 * like engine keys, needs key writes turned on at the PC.
 */
import { useEffect, useState } from 'react'

import { clearNotionToken, getNotionConfig, saveNotionConfig, setNotionToken, testNotion } from '../../api/notion'
import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { NotionConfig, NotionTargetType } from '../../types/notion'
import { configChanges, notionErrorMessage, notionSummary, readyToExport, testResultText } from './notion'

const TITLE = 'Notion'
const DB_PROPERTIES = [
  ['Original title', 'text'],
  ['Lines', 'number'],
  ['Translated', 'number'],
  ['Source language', 'text or select'],
  ['Exported', 'date'],
] as const

export function NotionSection() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <NotionControls />
}

function NotionControls() {
  const [cfg, setCfg] = useState<NotionConfig | null>(null)
  const [type, setType] = useState<NotionTargetType>('database')
  const [target, setTarget] = useState('')
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = (c: NotionConfig) => {
    setCfg(c)
    setType(c.target_type ?? 'database')
    setTarget(c.target_id ?? '')
  }

  // Wait for /api/meta first, so a viewer away from the PC makes no calls here.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getNotionConfig().then(
        (c) => live && load(c),
        (e: unknown) => live && setError(notionErrorMessage(e)),
      )
    })
    return () => {
      live = false
    }
  }, [])

  const call = <T,>(p: Promise<T>, done: (r: T) => void, tokenWrite = false) => {
    setBusy(true)
    setNote(null)
    setError(null)
    p.then(
      (r) => {
        done(r)
        setBusy(false)
      },
      (e: unknown) => {
        setError(notionErrorMessage(e, tokenWrite))
        setBusy(false)
      },
    )
  }

  const changes = cfg ? configChanges(cfg, type, target) : {}
  const dirty = Object.keys(changes).length > 0

  const save = () => call(saveNotionConfig(changes), (c) => {
    load(c)
    setNote('Saved.')
  })
  const saveToken = () => {
    const value = token.trim()
    setToken('') // the request already holds it; drop it now
    call(setNotionToken(value), (c) => {
      load(c)
      setNote('Token saved.')
    }, true)
  }
  const clearToken = () => call(clearNotionToken(), (c) => {
    load(c)
    setNote('Token cleared.')
  }, true)
  const test = () => call(testNotion(), (r) => setNote(testResultText(r)))

  return (
    <Card title={TITLE} meta={cfg ? notionSummary(cfg) : undefined} aria-label={TITLE}>
      <p className="settings-note">
        Optional. Exports a drama's subtitles to Notion as a page. Create an internal integration at{' '}
        <a href="https://www.notion.so/my-integrations" target="_blank" rel="noopener noreferrer">
          notion.so/my-integrations
        </a>
        , then share the target page or database with it (••• &gt; Connections).
      </p>
      {!cfg ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <ul className="status-list" aria-label="Notion setup">
          <li>
            <div className="status-row">
              <span className="status-row-name">Integration token</span>
              <span data-testid="notion-token">
                <Badge tone={cfg.token_configured ? 'ok' : 'neutral'}>
                  {cfg.token_configured ? 'Token saved' : 'No token'}
                </Badge>
              </span>
            </div>
            <div className="status-form">
              <Field label={cfg.token_configured ? 'Replace token' : 'Token'} help="The integration's Internal Integration Secret. It is saved to .env on this PC and never shown again.">
                <input type="password" autoComplete="off" spellCheck={false} value={token}
                  onChange={(e) => setToken(e.target.value)} />
              </Field>
              <div className="settings-actions">
                <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !token.trim()} onClick={saveToken}>
                  Save token
                </button>
                {cfg.token_configured && (
                  <ConfirmButton name="Notion token" label="Clear…" ariaLabel="Clear token" verb="clear"
                    busy={busy} onConfirm={clearToken} />
                )}
              </div>
            </div>
          </li>
          <li>
            <div className="status-row">
              <span className="status-row-name">Where pages go</span>
            </div>
            <div className="status-form">
              <Field label="Export into" help="A new page per drama is created inside it.">
                <select value={type} onChange={(e) => setType(e.target.value as NotionTargetType)}>
                  <option value="database">Database</option>
                  <option value="page">Page</option>
                </select>
              </Field>
              <Field label={type === 'database' ? 'Database link' : 'Page link'} help="Its link from Notion (Share, Copy link) or its id. Save it empty to clear it.">
                <input type="text" spellCheck={false} value={target} placeholder="https://www.notion.so/…"
                  onChange={(e) => setTarget(e.target.value)} />
              </Field>
              {type === 'database' && (
                <p className="settings-note" data-testid="notion-db-note">
                  Properties with these names are filled in when the database has them:{' '}
                  {DB_PROPERTIES.map(([name, kind], i) => (
                    <span key={name}>{i > 0 && ', '}"{name}" ({kind})</span>
                  ))}
                  . Other properties are left alone.
                </p>
              )}
              <div className="settings-actions">
                <button type="button" className={buttonClass('primary', 'sm')} disabled={busy || !dirty} onClick={save}>
                  Save
                </button>
                <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !readyToExport(cfg)} onClick={test}>
                  Test connection
                </button>
              </div>
            </div>
          </li>
        </ul>
      )}
      {note && <p role="status">{note}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </Card>
  )
}
