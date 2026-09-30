/*
 * Maintenance assistant (Step 42, read-only v1). PC only, behind Developer
 * Mode (Settings). Ask questions about the app's code and logs; the
 * assistant reads through a fixed set of read-only tools. It can suggest a
 * fix as a patch (shown, never applied) and backlog notes (added only when
 * the user presses "Add to backlog"). Answers are shown as plain text.
 *
 * Reached by URL with the mode off, the page shows only the Developer Mode
 * switch; from another device, only a "PC only" line.
 */
import { useCallback, useEffect, useState } from 'react'

import { addBacklogItem, getAssistantSettings, listBacklog, saveAssistantSettings } from '../api/assistant'
import { getPcMode, loadPcMode } from '../api/pcOnly'
import { Card } from '../components/Card'
import { Field } from '../components/Field'
import { Toggle } from '../components/Toggle'
import type { AssistantSettings, BacklogItem, BacklogKind } from '../types/assistant'
import { BacklogCard } from './assistant/BacklogCard'
import { ChangelogCard } from './assistant/ChangelogCard'
import { ChatCard } from './assistant/ChatCard'
import { DEVELOPER_MODE_HELP, PC_ONLY_TEXT, assistantErrorText, isForbidden } from './assistant/assistantFormat'
import { announceDeveloperMode } from './assistant/developerMode'
import { ToolsCard } from './assistant/ToolsCard'
import './assistant/assistant.css'

type Load = { state: 'loading' } | { state: 'pc-only' } | { state: 'error'; text: string } | { state: 'ready'; settings: AssistantSettings }

export default function AssistantPage() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [backlog, setBacklog] = useState<BacklogItem[] | null>(null)
  const [backlogError, setBacklogError] = useState<string | null>(null)
  // The engine and model for this visit's asks and changelogs; blank = the saved default / server default.
  const [engine, setEngine] = useState('')
  const [model, setModel] = useState('')

  const ready = useCallback((settings: AssistantSettings) => {
    setLoad({ state: 'ready', settings })
    setEngine(settings.engine ?? '')
    setModel(settings.model ?? '')
  }, [])

  const refresh = useCallback(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live) return
      if (getPcMode() === 'remote') return setLoad({ state: 'pc-only' })
      getAssistantSettings().then(
        (settings) => live && ready(settings),
        (e: unknown) => live && setLoad(isForbidden(e) ? { state: 'pc-only' } : { state: 'error', text: assistantErrorText(e) }),
      )
    })
    return () => {
      live = false
    }
  }, [ready])

  useEffect(refresh, [refresh])

  const on = load.state === 'ready' && load.settings.developer_mode
  useEffect(() => {
    if (!on) return
    let live = true
    listBacklog().then(
      (r) => live && setBacklog(r.items),
      (e: unknown) => live && setBacklogError(assistantErrorText(e)),
    )
    return () => {
      live = false
    }
  }, [on])

  const addToBacklog = async (kind: BacklogKind, text: string) => {
    const item = await addBacklogItem(kind, text)
    setBacklog((cur) => [...(cur ?? []), item])
    return item
  }

  const title = (
    <header className="page-head">
      <div className="page-head-text">
        <h2 className="page-title">Maintenance assistant</h2>
        <p className="page-meta">Ask about this app&apos;s code and logs. Read-only: it suggests fixes but never applies them.</p>
      </div>
    </header>
  )

  let body
  if (load.state === 'loading') body = <p className="muted">Loading…</p>
  else if (load.state === 'pc-only') {
    body = (
      <Card title="PC only" aria-label="PC only">
        <p className="muted">{PC_ONLY_TEXT}</p>
      </Card>
    )
  } else if (load.state === 'error') {
    body = (
      <p className="error" role="alert">
        {load.text}
      </p>
    )
  } else if (!load.settings.developer_mode) {
    body = <ModeOffCard onSettings={ready} />
  } else {
    const settings = load.settings
    body = (
      <>
        <ChatCard
          settings={settings}
          engine={engine}
          model={model}
          onEngine={setEngine}
          onModel={setModel}
          onSettings={(s) => setLoad({ state: 'ready', settings: s })}
          onModeOff={refresh}
          onAddToBacklog={addToBacklog}
        />
        <BacklogCard items={backlog} loadError={backlogError} onItems={setBacklog} onAdd={addToBacklog} />
        <ChangelogCard engine={engine} model={model} onModeOff={refresh} />
        <ToolsCard />
      </>
    )
  }

  return (
    <section className="panel page-narrow assistant-page" aria-label="Maintenance assistant">
      {title}
      {body}
    </section>
  )
}

function ModeOffCard({ onSettings }: { onSettings: (s: AssistantSettings) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const turnOn = (next: boolean) => {
    setBusy(true)
    setError(null)
    saveAssistantSettings({ developer_mode: next }).then(
      (s) => {
        setBusy(false)
        announceDeveloperMode(s.developer_mode)
        onSettings(s)
      },
      (e: unknown) => {
        setBusy(false)
        setError(assistantErrorText(e))
      },
    )
  }

  return (
    <Card title="Developer Mode is off" aria-label="Developer Mode is off">
      <p className="muted">Turn on Developer Mode to use the maintenance assistant.</p>
      <div className="setting-list">
        <Field label="Developer Mode" help={DEVELOPER_MODE_HELP}>
          <Toggle checked={false} disabled={busy} onChange={turnOn} />
        </Field>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Card>
  )
}
