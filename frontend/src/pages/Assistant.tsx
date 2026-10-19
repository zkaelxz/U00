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
import { useCallback, useEffect, useState, type ReactNode } from 'react'

import { addBacklogItem, getAssistantSettings, listBacklog } from '../api/assistant'
import { getGithubStatus } from '../api/assistantGithub'
import { getPcMode, loadPcMode } from '../api/pcOnly'
import { Card } from '../components/Card'
import { DeveloperModeOff } from '../components/DeveloperModeOff'
import { PageShell } from '../components/PageShell'
import { Tabs } from '../components/Tabs'
import { tabButtonId, tabPanelId } from '../components/tabIds'
import { RetryBanner } from '../components/RetryBanner'
import { usePersistedState } from '../hooks/usePersistedState'
import type { AssistantSettings, BacklogItem, BacklogKind, GithubStatus } from '../types/assistant'
import { BacklogCard } from './assistant/BacklogCard'
import { ChangelogCard } from './assistant/ChangelogCard'
import { ChatCard } from './assistant/ChatCard'
import { GithubCard } from './assistant/GithubCard'
import { ASSISTANT_TABS, resolveAssistantTab, type AssistantTab } from './assistant/assistantTabs'
import { ReviewRolesSection } from './assistant/ReviewRolesSection'
import { PC_ONLY_TEXT, assistantErrorText, isAssistantForbidden } from './assistant/assistantFormat'
import { ToolsCard } from './assistant/ToolsCard'
import './assistant/assistant.css'

type Load = { state: 'loading' } | { state: 'pc-only' } | { state: 'error'; text: string; error: unknown } | { state: 'ready'; settings: AssistantSettings }

export default function AssistantPage() {
  const [load, setLoad] = useState<Load>({ state: 'loading' })
  const [backlog, setBacklog] = useState<BacklogItem[] | null>(null)
  const [backlogError, setBacklogError] = useState<string | null>(null)
  // Null until loaded (or when the status call fails: the card and Deliver stay hidden).
  const [github, setGithub] = useState<GithubStatus | null>(null)
  // The third tab waits for the status call so its first card is the right one to open.
  const [githubKnown, setGithubKnown] = useState(false)
  const [storedTab, setTab] = usePersistedState<string>('assistant.tab', 'ask')
  const tab = resolveAssistantTab(storedTab)
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
        (e: unknown) => live && setLoad(isAssistantForbidden(e) ? { state: 'pc-only' } : { state: 'error', text: assistantErrorText(e), error: e }),
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
      (s) => {
        if (!live) return
        setGithub(s)
        setGithubKnown(true)
      },
      () => {
        if (!live) return
        setGithub(null)
        setGithubKnown(true)
      },
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

  let tabs: ReactNode = null
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
      <RetryBanner
        error={load.error}
        text={load.text}
        onRetry={() => {
          setLoad({ state: 'loading' })
          refresh()
        }}
      />
    )
  } else if (!load.settings.developer_mode) {
    body = <DeveloperModeOff />
  } else {
    const settings = load.settings
    const panel = (id: AssistantTab, children: ReactNode) => (
      <div id={tabPanelId('assistant', id)} role="tabpanel" aria-labelledby={tabButtonId('assistant', id)} className="assistant-panel" hidden={tab !== id}>
        {children}
      </div>
    )
    tabs = <Tabs label="Assistant" idPrefix="assistant" tabs={ASSISTANT_TABS} active={tab} onSelect={setTab} />
    body = (
      <>
        {panel('ask', (
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
        ))}
        {panel('work', (
          <>
            <BacklogCard items={backlog} loadError={backlogError} onItems={setBacklog} onAdd={addToBacklog} />
            <ChangelogCard engine={engine} model={model} onModeOff={refresh} />
          </>
        ))}
        {panel('tools', githubKnown && (
          <>
            {github && <GithubCard status={github} onStatus={setGithub} />}
            <ToolsCard />
            <ReviewRolesSection settings={settings} implementEngine={engine} onSettings={(s) => setLoad({ state: 'ready', settings: s })} />
          </>
        ))}
      </>
    )
  }

  return (
    <PageShell
      width="narrow"
      title="Assistant"
      label="Assistant"
      meta="Ask about this app's code and logs. Read-only: it suggests fixes but never applies them."
      tabs={tabs}
      className="assistant-page"
    >
      {body}
    </PageShell>
  )
}
