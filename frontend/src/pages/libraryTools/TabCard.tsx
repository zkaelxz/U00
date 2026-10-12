import type { ReactNode } from 'react'

import { Section } from '../../components/Section'
import { CARDS, cardStartsOpen, cardStorageKey, type LibraryToolsCard } from './libraryToolsTabs'

/** One collapsible card on a Library tools tab; its title, start state and remembered state come from the tab model. */
export function TabCard({ card, count, summary, defaultOpen, children }: {
  card: LibraryToolsCard
  // Set when the tab's first card is not this tab's first one on screen (an empty section is hidden).
  defaultOpen?: boolean
  count?: number
  summary?: string
  children: ReactNode
}) {
  return (
    <Section title={CARDS[card].title} count={count} summary={summary} defaultOpen={defaultOpen ?? cardStartsOpen(card)} storageKey={cardStorageKey(card)}>
      {children}
    </Section>
  )
}
