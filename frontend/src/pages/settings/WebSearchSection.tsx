/*
 * Settings > Web search (roadmap item 114): the optional "Search the web"
 * fallback under an empty Sources title search, through the user's own
 * SearXNG server. Off by default, PC only. Changing the address decides
 * where Baihe sends searches, so, like the engine endpoint URLs, it works
 * only at the PC with key writes on. "Test" runs one small search.
 */
import { useEffect, useState } from 'react'

import { getPcMode, loadPcMode } from '../../api/pcOnly'
import { getWebSearchConfig, saveWebSearchConfig, testWebSearch } from '../../api/webSearch'
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_SUMMARY, usePcOnly } from '../../hooks/usePcOnly'
import type { WebSearchConfig } from '../../types/webSearch'
import { webSearchErrorMessage, webSearchSummary } from '../sources/webSearchFormat'

const TITLE = 'Web search'

export function WebSearchSection() {
  const pc = usePcOnly()
  if (pc === 'remote') {
    return (
      <Card title={TITLE} meta={PC_ONLY_SUMMARY} aria-label={TITLE}>
        <p className="muted">{PC_ONLY_BODY}</p>
      </Card>
    )
  }
  return <WebSearchControls />
}

function WebSearchControls() {
  const [cfg, setCfg] = useState<WebSearchConfig | null>(null)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<'save' | 'toggle' | 'test' | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = (c: WebSearchConfig) => {
    setCfg(c)
    setUrl(c.base_url ?? '')
  }

  // Wait for /api/meta first, so a viewer away from the PC makes no calls here.
  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getWebSearchConfig().then(
        (c) => live && load(c),
        (e: unknown) => live && setError(webSearchErrorMessage(e)),
      )
    })
    return () => {
      live = false
    }
  }, [])

  const call = <T,>(what: 'save' | 'toggle' | 'test', p: Promise<T>, done: (r: T) => void) => {
    const addressWrite = what === 'save'
    setBusy(what)
    setNote(null)
    setError(null)
    p.then(
      (r) => {
        done(r)
        setBusy(null)
      },
      (e: unknown) => {
        setError(webSearchErrorMessage(e, addressWrite))
        setBusy(null)
      },
    )
  }

  const save = () =>
    call('save', saveWebSearchConfig({ base_url: url.trim(), confirm: true }), (c) => {
      load(c)
      setNote('Saved.')
    })
  // Only the switch changes: an address typed but not saved yet stays in the box.
  const toggle = (enabled: boolean) => call('toggle', saveWebSearchConfig({ enabled }), setCfg)
  const test = () =>
    call('test', testWebSearch(), (r) =>
      setNote(`SearXNG answered with ${r.result_count} result${r.result_count === 1 ? '' : 's'}.`),
    )

  const dirty = !!cfg && url.trim() !== (cfg.base_url ?? '')

  return (
    <Card
      title={TITLE}
      meta={cfg ? webSearchSummary(cfg) : undefined}
      aria-label={TITLE}
      actions={cfg && <Toggle checked={cfg.enabled} disabled={!!busy} onChange={toggle} aria-label="Use web search" />}
    >
      <p className="settings-note">
        Optional. When a title search finds nothing on any source, Sources offers “Search the web” through your own
        SearXNG server. Results are links only: Baihe never opens them by itself. Off by default.
      </p>
      {!cfg ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <div className="setting-list">
          <Field
            label="SearXNG address"
            help="For example http://localhost:8888 or http://192.168.1.20:8888. Its settings.yml must list json under search.formats. Changing it works only on the Baihe PC with key writes on."
          >
            <input type="url" value={url} placeholder="http://localhost:8888" onChange={(e) => setUrl(e.target.value)} />
          </Field>
          <div className="actions">
            <button type="button" className={buttonClass('primary', 'sm')} disabled={!!busy || !dirty} onClick={save}>
              Save
            </button>
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={!!busy || !cfg.base_url || dirty} onClick={test}>
              {busy === 'test' ? 'Testing…' : 'Test'}
            </button>
          </div>
        </div>
      )}
      {note && <p role="status">{note}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Card>
  )
}
