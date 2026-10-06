/*
 * Maintenance assistant (read-only v1). PC only, behind Developer
 * Mode (Settings). Ask questions about the app's code and logs; the
 * assistant reads through a fixed set of read-only tools. It can suggest a
 * fix as a patch (shown, never applied) and backlog notes (added only when
 * the user presses "Add to backlog"). Answers are shown as plain text.
 *
 * Reached by URL with the mode off, the page shows only a link to the Developer
 * Mode card in Settings; from another device, only a "PC only" line.
 */
import { useCallback, useEffect, useState } from 'react'

import { addBacklogItem, getAssistantSettings, listBacklog } from '../api/assistant'
import { getGithubStatus } from '../api/assistantGithub'
import { getPcMode, loadPcMode } from '../api/pcOnly'
import { Card } from '../components/Card'
import { routeHref } from '../router'
import type { AssistantSettings, BacklogItem, BacklogKind, GithubStatus } from '../types/assistant'
import { BacklogCard } from './assistant/BacklogCard'
import { ChangelogCard } from './assistant/ChangelogCard'
import { ChatCard } from './assistant/ChatCard'
import { GithubCard } from './assistant/GithubCard'
import { PC_ONLY_TEXT, assistantErrorText, isForbidden } from './assistant/assistantFormat'
import { ToolsCard } from './assistant/ToolsCard'
import './assistant/assistant.css'

type Load = { state: 'loading' } | { state: 'pc-only' } | { state: 'error'; text: string } | { state: 'ready'; settings: AssistantSettings }

export default function AssistantPage() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [backlog, setBacklog] = useState<BacklogItem[] | null>(null)
  const [backlogError, setBacklogError] = useState<string | null>(null)
  // Null until loaded (or when the status call fails: the card and Deliver stay hidden).
  const [github, setGithub] = useState<GithubStatus | null>(null)
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
    getGithubStatus().then(
      (s) => live && setGithub(s),
      () => live && setGithub(null),
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
    body = <ModeOffMessage />
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
          github={github}
        />
        <BacklogCard items={backlog} loadError={backlogError} onItems={setBacklog} onAdd={addToBacklog} />
        <ChangelogCard engine={engine} model={model} onModeOff={refresh} />
        {github && <GithubCard status={github} onStatus={setGithub} />}
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

function ModeOffMessage() {
  return (
    <p className="muted" role="status">
      Developer Mode is off. <a href={routeHref({ name: 'settings', section: 'developer-mode' })}>Turn on in Settings</a>
    </p>
  )
}
