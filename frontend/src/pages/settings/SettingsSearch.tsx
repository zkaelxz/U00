import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { CardFoldContext } from '../../components/cardFold'
import { buttonClass } from '../../components/uiClasses'
import { settingsCardStartsOpen, settingsCardKey, tabOfCard, type SettingsTab } from './settingsIndex'

// null: no search, every card shows. Otherwise only the listed cards do.
const VisibleCardsContext = createContext<ReadonlySet<string> | null>(null)
export const VisibleCardsProvider = VisibleCardsContext.Provider

// Why the cards open or close without a click on them. Each number only goes up, and a card reacts to a
// change in its own number, so a card is never opened by a signal that was meant for another one.
export type FoldSignals = {
  /** Per card: bumped by a search hit or a link that names it. */
  opens: Readonly<Record<string, number>>
  /** Per tab: bumped by that tab's Expand all / Collapse all. */
  bulk: Readonly<Record<SettingsTab, { expand: number; collapse: number }>>
}
const NO_SIGNALS: FoldSignals = { opens: {}, bulk: {} as FoldSignals['bulk'] }
const FoldSignalsContext = createContext<FoldSignals>(NO_SIGNALS)
export const FoldSignalsProvider = FoldSignalsContext.Provider

/**
 * Wraps one Settings card so the search can hide it and the card folds (its open state is remembered
 * per card). `summary` is the current value shown while it is closed; a card that has its own meta line
 * can leave it out. A card that renders nothing leaves no gap.
 */
export function SettingsCard({ id, summary, children }: { id: string; summary?: string; children: ReactNode }) {
  const visible = useContext(VisibleCardsContext)
  const signals = useContext(FoldSignalsContext)
  const tab = tabOfCard(id)
  const bulk = tab ? signals.bulk[tab] : undefined
  const open = signals.opens[id] ?? 0
  const expand = bulk?.expand ?? 0
  const close = bulk?.collapse ?? 0
  const fold = useMemo(
    () => ({ storageKey: settingsCardKey(id), defaultOpen: settingsCardStartsOpen(id), summary, openSignal: open, expandSignal: expand, closeSignal: close }),
    [id, summary, open, expand, close],
  )
  return (
    <div id={`settings-card-${id}`} className="settings-card" hidden={visible !== null && !visible.has(id)}>
      <CardFoldContext.Provider value={fold}>{children}</CardFoldContext.Provider>
    </div>
  )
}

// The cards of the tab on screen: the folds the bulk button acts on.
const TAB_CARD_FOLDS = '.settings-panel:not([hidden]) > .settings-card:not([hidden]) > .card-fold > details.section'

// A card folds itself and can load late, so the button reads the page instead of remembering its last press.
function useTabFullyOpen() {
  const [fullyOpen, setFullyOpen] = useState(false)
  useEffect(() => {
    const page = document.querySelector('.settings-page')
    if (!page) return
    const measure = () => {
      const folds = page.querySelectorAll(TAB_CARD_FOLDS)
      setFullyOpen(folds.length > 0 && Array.from(folds).every((d) => (d as HTMLDetailsElement).open))
    }
    measure()
    const observer = new MutationObserver(measure)
    observer.observe(page, { subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'hidden'] })
    return () => observer.disconnect()
  }, [])
  return fullyOpen
}

export function FoldControls({ onExpand, onCollapse }: { onExpand: () => void; onCollapse: () => void }) {
  const phone = useMediaQuery('(max-width: 640px)')
  const fullyOpen = useTabFullyOpen()
  const button = (
    <button type="button" className={buttonClass('ghost', 'sm')} onClick={fullyOpen ? onCollapse : onExpand}>
      {fullyOpen ? 'Collapse all' : 'Expand all'}
    </button>
  )
  // On a phone the button would be a row of its own above the first card, so it sits in a small menu.
  return phone ? (
    <details className="settings-fold-menu">
      <summary>Expand / collapse</summary>
      <div className="settings-fold-controls">{button}</div>
    </details>
  ) : (
    <div className="settings-fold-controls">{button}</div>
  )
}

export function SettingsSearch({ query, onQuery, total }: { query: string; onQuery: (q: string) => void; total: number | null }) {
  return (
    <div className="settings-search">
      <input
        type="search"
        enterKeyHint="search"
        aria-label="Search settings"
        placeholder="Search settings"
        autoComplete="off"
        value={query}
        onChange={(ev) => onQuery(ev.target.value)}
        onKeyDown={(ev) => {
          if (ev.key === 'Escape' && query) {
            ev.preventDefault()
            onQuery('')
          }
        }}
      />
      <p className="visually-hidden" role="status" aria-live="polite">
        {total === null ? '' : `${total} ${total === 1 ? 'result' : 'results'}`}
      </p>
    </div>
  )
}
