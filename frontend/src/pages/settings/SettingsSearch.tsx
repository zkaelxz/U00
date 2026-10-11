import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { CardFoldContext } from '../../components/cardFold'
import { buttonClass } from '../../components/uiClasses'
import { cardStartsOpen, tabOfCard, type SettingsTab } from './settingsIndex'

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
    () => ({ storageKey: `settings.card.${id}`, defaultOpen: cardStartsOpen(id), summary, openSignal: open, expandSignal: expand, closeSignal: close }),
    [id, summary, open, expand, close],
  )
  return (
    <div id={`settings-card-${id}`} className="settings-card" hidden={visible !== null && !visible.has(id)}>
      <CardFoldContext.Provider value={fold}>{children}</CardFoldContext.Provider>
    </div>
  )
}

export function FoldControls({ onExpand, onCollapse }: { onExpand: () => void; onCollapse: () => void }) {
  return (
    <div className="settings-fold-controls">
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={onExpand}>Expand all</button>
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={onCollapse}>Collapse all</button>
    </div>
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
