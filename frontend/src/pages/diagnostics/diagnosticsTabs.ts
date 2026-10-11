// The Diagnostics tabs and which tab holds each card. Deep links name a card
// ("#/diagnostics?card=ports") and the page finds the tab from this table.
export type DiagnosticsTab = 'setup' | 'packages' | 'models' | 'system'

export const DIAGNOSTICS_TABS: { id: DiagnosticsTab; label: string }[] = [
  { id: 'setup', label: 'Setup' },
  { id: 'packages', label: 'Packages and installs' },
  { id: 'models', label: 'Models' },
  { id: 'system', label: 'System' },
]

export const CARD_TAB = {
  setup: 'setup',
  packages: 'packages',
  browser: 'packages',
  deno: 'packages',
  speaker: 'packages',
  'model-status': 'models',
  models: 'models',
  'loaded-models': 'models',
  'test-models': 'models',
  ports: 'system',
  log: 'system',
  reset: 'system',
} as const satisfies Record<string, DiagnosticsTab>

export type DiagnosticsCard = keyof typeof CARD_TAB

export const isDiagnosticsTab = (v: unknown): v is DiagnosticsTab => DIAGNOSTICS_TABS.some((t) => t.id === v)
export const isDiagnosticsCard = (v: unknown): v is DiagnosticsCard => typeof v === 'string' && Object.hasOwn(CARD_TAB, v)

export const cardAnchorId = (card: DiagnosticsCard) => `diag-card-${card}`
