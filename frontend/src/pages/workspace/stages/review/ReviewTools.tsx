import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { Tabs, type TabInfo } from '../../../../components/Tabs'
import { tabButtonId, tabPanelId } from '../../../../components/tabIds'
import { browserStorage } from '../../../../hooks/usePersistedState'
import { useRoute } from '../../../../router'
import { useSimple } from '../../StageContext'
import {
  groupOfTool, readAddedTools, readLastTab, seedFirstCard, shownGroups, shownTools, writeAddedTools, writeLastTab,
  type ReviewTool, type ReviewToolsGroup,
} from './reviewToolsTabs'

const ID_PREFIX = 'review-tools'

const EMPTY_TEXT: Record<ReviewToolsGroup, string> = {
  problems: 'Nothing to show here yet.',
  change: 'Nothing to show here yet. These tools appear once the title has lines.',
  records: 'Nothing to show here yet. Notes, versions and history appear as you work.',
  finish: 'Nothing to show here yet. These tools appear once the title has lines.',
}

// A group whose sections are all empty says so once, instead of showing a blank panel.
function TabBody({ group, children }: { group: ReviewToolsGroup; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  const [empty, setEmpty] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const look = () => setEmpty(el.childElementCount === 0)
    look()
    if (typeof MutationObserver === 'undefined') return
    const mo = new MutationObserver(look)
    mo.observe(el, { childList: true })
    return () => mo.disconnect()
  }, [])
  return (
    <>
      <div ref={ref} className="review-tab-body stack">{children}</div>
      {empty && <p className="muted review-tab-empty" data-testid="review-tab-empty">{EMPTY_TEXT[group]}</p>}
    </>
  )
}

interface Props {
  dramaId: number
  // The job status, shown above the groups so a running job is visible on every group.
  status: ReactNode
  tools: Record<ReviewTool, ReactNode>
  // Warning counts on a tool (Coverage shows how many things to look at), shown on the group that holds it.
  attention?: Partial<Record<ReviewTool, number>>
  // A button elsewhere asked for a tool (Compare transcription… opens its group); seq makes a repeat count.
  request?: { tool: ReviewTool; seq: number } | null
}

// The tools under the line list as one tab level of groups. A group's panel mounts
// the first time it is shown and then stays, so open folds and drafts survive a switch.
export function ReviewTools({ dramaId, status, tools, attention = {}, request = null }: Props) {
  // A link or remembered choice for a group Simple leaves out opens Look for problems.
  // A button that needs a tool Simple leaves out adds it for this title: Transcribe this gap
  // starts its run in Re-transcribe, which must be mounted to start it and to apply what it heard.
  const simple = useSimple()
  const [added, setAdded] = useState<ReadonlySet<ReviewTool>>(() => readAddedTools(browserStorage(), dramaId))
  const visibleTools = useMemo(() => shownTools(simple, added), [simple, added])
  const shown = useMemo(() => shownGroups(simple, added), [simple, added])
  const allowed = useCallback((g: ReviewToolsGroup) => shown.some((x) => x.id === g), [shown])
  const route = useRoute()
  const linked = route.name === 'drama' && route.id === dramaId ? route.tools : undefined
  const [tab, setTabState] = useState<ReviewToolsGroup>(() => {
    const wanted = linked ?? readLastTab(browserStorage(), dramaId)
    const first = allowed(wanted) ? wanted : 'problems'
    seedFirstCard(browserStorage(), first)
    return first
  })
  const [visited, setVisited] = useState<ReadonlySet<ReviewToolsGroup>>(() => new Set([tab]))

  const open = useCallback((g: ReviewToolsGroup) => {
    seedFirstCard(browserStorage(), g)
    setTabState(g)
    setVisited((v) => (v.has(g) ? v : new Set(v).add(g)))
    writeLastTab(browserStorage(), dramaId, g)
  }, [dramaId])
  const select = useCallback((g: ReviewToolsGroup) => {
    // A link for a group Simple leaves out keeps the group that is open.
    if (allowed(g)) open(g)
  }, [allowed, open])

  // A link's group wins over the remembered one, also while the stage is already open.
  // Only when the link changes: adding a tool must not send the open group back to the link's.
  const appliedLink = useRef<string | null>(null)
  useEffect(() => {
    const key = linked ? `${dramaId}:${linked}` : null
    if (linked && key !== appliedLink.current) select(linked)
    appliedLink.current = key
  }, [dramaId, linked, select])
  const selectTool = useCallback((tool: ReviewTool) => {
    if (!added.has(tool)) {
      const next = new Set(added).add(tool)
      setAdded(next)
      writeAddedTools(browserStorage(), dramaId, next)
    }
    open(groupOfTool(tool))
  }, [added, dramaId, open])
  const seenSeq = useRef(request?.seq ?? 0)
  useEffect(() => {
    if (request && request.seq !== seenSeq.current) {
      seenSeq.current = request.seq
      selectTool(request.tool)
    }
  }, [request, selectTool])

  const tabs: TabInfo<ReviewToolsGroup>[] = shown.map((g) => ({
    ...g,
    attention: visibleTools.filter((t) => t.group === g.id).reduce((n, t) => n + (attention[t.id] ?? 0), 0),
  }))
  return (
    <section className="review-toolbox" aria-label="Checks and tools">
      {status}
      <Tabs label="Review tools" idPrefix={ID_PREFIX} tabs={tabs} active={tab} onSelect={select} keepRow />
      {shown.map(({ id }) => (
        <div
          key={id}
          id={tabPanelId(ID_PREFIX, id)}
          role="tabpanel"
          aria-labelledby={tabButtonId(ID_PREFIX, id)}
          className="review-toolbox-panel"
          hidden={tab !== id}
        >
          {visited.has(id) && (
            <TabBody group={id}>
              {visibleTools.filter((t) => t.group === id).map((t) => <Fragment key={t.id}>{tools[t.id]}</Fragment>)}
            </TabBody>
          )}
        </div>
      ))}
    </section>
  )
}
