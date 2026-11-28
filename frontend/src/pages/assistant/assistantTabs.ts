// The Assistant's tabs. Each tab shows its sections in full, as plain headings.
export type AssistantTab = 'ask' | 'work' | 'tools'

export const ASSISTANT_TABS: { id: AssistantTab; label: string }[] = [
  { id: 'ask', label: 'Ask' },
  { id: 'work', label: 'Work list' },
  { id: 'tools', label: 'Tools' },
]

export const isAssistantTab = (v: unknown): v is AssistantTab => ASSISTANT_TABS.some((t) => t.id === v)

/** The remembered tab when it is still a tab, else Ask. */
export const resolveAssistantTab = (stored: unknown): AssistantTab => (isAssistantTab(stored) ? stored : 'ask')
