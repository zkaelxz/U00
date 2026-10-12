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
  'cuda-runtime': 'packages',
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

// Section storage keys of each tab's cards, in screen order. The conditional install cards come first but
// start closed (their summary says what is wrong); the first card that is always there starts open.
export const TAB_FOLD_KEYS: Record<DiagnosticsTab, string[]> = {
  setup: ['diagnostics.setup'],
  packages: ['diagnostics.browser', 'diagnostics.cuda-runtime', 'diagnostics.deno', 'diagnostics.packages', 'diagnostics.speaker'],
  models: ['diagnostics.modelHealth', 'diagnostics.models', 'diagnostics.realModel'],
  system: ['diagnostics.ports', 'diagnostics.log'],
}

export const cardAnchorId = (card: DiagnosticsCard) => `diag-card-${card}`
