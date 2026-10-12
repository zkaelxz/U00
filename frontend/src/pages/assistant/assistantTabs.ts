// The Assistant's tabs and which tab holds each card. Only the first card of a
// tab starts open; the Section's own storage remembers any later choice.
export type AssistantTab = 'ask' | 'work' | 'tools'

export const ASSISTANT_TABS: { id: AssistantTab; label: string }[] = [
  { id: 'ask', label: 'Ask' },
  { id: 'work', label: 'Work list' },
  { id: 'tools', label: 'GitHub and tools' },
]

export const isAssistantTab = (v: unknown): v is AssistantTab => ASSISTANT_TABS.some((t) => t.id === v)

/** The remembered tab when it is still a tab, else Ask. */
export const resolveAssistantTab = (stored: unknown): AssistantTab => (isAssistantTab(stored) ? stored : 'ask')

export type AssistantCard = 'chat' | 'engine' | 'backlog' | 'changelog' | 'github' | 'tools' | 'review'

/** The cards of the third tab in on-screen order; the GitHub card is left out when GitHub is not configured. */
const TOOLS_TAB_ORDER: AssistantCard[] = ['github', 'tools', 'review']

/** Whether the card starts open the first time (nothing remembered). */
export function cardStartsOpen(card: AssistantCard, githubConfigured: boolean): boolean {
  if (card === 'chat' || card === 'backlog') return true
  const order = githubConfigured ? TOOLS_TAB_ORDER : TOOLS_TAB_ORDER.slice(1)
  return card === order[0]
}
