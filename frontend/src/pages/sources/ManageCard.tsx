import type { ReactNode } from 'react'

import { Section } from '../../components/Section'
import { cardStartsOpen, cardStorageKey, type ManageCard as CardId, type ManageTab } from './manageTabs'

/** One fold on a Manage sources tab: the tab's first card starts open, the rest closed, and each is remembered. */
export function ManageCard({ tab, card, title, summary, count, openSignal, children }: {
  tab: ManageTab
  card: CardId
  title: string
  summary?: string
  count?: number
  openSignal?: number
  children: ReactNode
}) {
  return (
    <Section
      title={title}
      summary={summary}
      count={count}
      defaultOpen={cardStartsOpen(tab, card)}
      storageKey={cardStorageKey(tab, card)}
      openSignal={openSignal}
    >
      {children}
    </Section>
  )
}
