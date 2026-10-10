import { createContext, useContext, type ReactNode } from 'react'

// null: no search, every card shows. Otherwise only the listed cards do.
const VisibleCardsContext = createContext<ReadonlySet<string> | null>(null)
export const VisibleCardsProvider = VisibleCardsContext.Provider

/** Wraps one Settings card so the search can hide it; a card that renders nothing leaves no gap. */
export function SettingsCard({ id, children }: { id: string; children: ReactNode }) {
  const visible = useContext(VisibleCardsContext)
  return (
    <div id={`settings-card-${id}`} className="settings-card" hidden={visible !== null && !visible.has(id)}>
      {children}
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
