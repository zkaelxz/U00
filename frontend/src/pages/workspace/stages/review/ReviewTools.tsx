import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import { Tabs, type TabInfo } from '../../../../components/Tabs'
import { tabButtonId, tabPanelId } from '../../../../components/tabIds'
import { browserStorage } from '../../../../hooks/usePersistedState'
import { useRoute } from '../../../../router'
import {
  REVIEW_TOOLS_TABS, readLastTab, seedFirstCard, writeLastTab, type ReviewToolsTab,
} from './reviewToolsTabs'

const ID_PREFIX = 'review-tools'

const EMPTY_TEXT: Record<ReviewToolsTab, string> = {
  check: 'Nothing to show here yet.',
  fix: 'Nothing to show here yet.',
  coverage: 'Nothing to look at: no gaps, untranslated lines or pacing problems.',
  restructure: 'Nothing to show here yet. These tools appear once the title has lines.',
  history: 'Nothing to show here yet. Notes, versions and history appear as you work.',
  extras: 'Nothing to show here yet. These tools appear once the title has lines.',
}

// A tab whose sections are all empty says so once, instead of showing a blank panel.
function TabBody({ tab, children }: { tab: ReviewToolsTab; children: ReactNode }) {
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
      {empty && <p className="muted review-tab-empty" data-testid="review-tab-empty">{EMPTY_TEXT[tab]}</p>}
    </>
  )
}

interface Props {
  dramaId: number
  // The job status, shown above the tabs so a running job is visible on every tab.
  status: ReactNode
  panels: Record<ReviewToolsTab, ReactNode>
  // Warning counts on a tab (Coverage shows how many things to look at).
  attention?: Partial<Record<ReviewToolsTab, number>>
  // A button elsewhere asked for a tab (Compare transcription… opens History); seq makes a repeat count.
  request?: { tab: ReviewToolsTab; seq: number } | null
}

// The tools under the line list as one tab level. A tab's panel mounts the first
// time it is shown and then stays, so open folds and drafts survive a switch.
export function ReviewTools({ dramaId, status, panels, attention = {}, request = null }: Props) {
  const route = useRoute()
  const linked = route.name === 'drama' && route.id === dramaId ? route.tools : undefined
  const [tab, setTabState] = useState<ReviewToolsTab>(() => {
    const first = linked ?? readLastTab(browserStorage(), dramaId)
    seedFirstCard(browserStorage(), first)
    return first
  })
  const [visited, setVisited] = useState<ReadonlySet<ReviewToolsTab>>(() => new Set([tab]))

  const select = useCallback((t: ReviewToolsTab) => {
    seedFirstCard(browserStorage(), t)
    setTabState(t)
    setVisited((v) => (v.has(t) ? v : new Set(v).add(t)))
    writeLastTab(browserStorage(), dramaId, t)
  }, [dramaId])

  // A link's tab wins over the remembered one, also while the stage is already open.
  useEffect(() => {
    if (linked) select(linked)
  }, [linked, select])
  const seenSeq = useRef(request?.seq ?? 0)
  useEffect(() => {
    if (request && request.seq !== seenSeq.current) {
      seenSeq.current = request.seq
      select(request.tab)
    }
  }, [request, select])

  const tabs: TabInfo<ReviewToolsTab>[] = REVIEW_TOOLS_TABS.map((t) => ({ ...t, attention: attention[t.id] }))
  return (
    <section className="review-toolbox" aria-label="Checks and tools">
      {status}
      <Tabs label="Review tools" idPrefix={ID_PREFIX} tabs={tabs} active={tab} onSelect={select} />
      {REVIEW_TOOLS_TABS.map(({ id }) => (
        <div
          key={id}
          id={tabPanelId(ID_PREFIX, id)}
          role="tabpanel"
          aria-labelledby={tabButtonId(ID_PREFIX, id)}
          className="review-toolbox-panel"
          hidden={tab !== id}
        >
          {visited.has(id) && <TabBody tab={id}>{panels[id]}</TabBody>}
        </div>
      ))}
    </section>
  )
}
