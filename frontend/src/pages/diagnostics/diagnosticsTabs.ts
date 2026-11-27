// The Diagnostics panes (the list down the left) and which pane holds each card. Deep links name a card
// ("#/diagnostics?card=ports") and the page finds the pane from this table.
export type DiagnosticsTab =
  | 'setup' | 'packages' | 'speaker' | 'model-status' | 'models' | 'loaded' | 'test-models' | 'system' | 'log' | 'reset'

// `pcOnly`: the pane's cards show nothing away from the main PC, so the item is left out there.
export const DIAGNOSTICS_TABS: { id: DiagnosticsTab; label: string; short?: string; pcOnly?: boolean; danger?: boolean }[] = [
  { id: 'setup', label: 'Setup' },
  { id: 'packages', label: 'Packages and installs', short: 'Packages' },
  { id: 'speaker', label: 'Speaker detection' },
  { id: 'model-status', label: 'Model status' },
  { id: 'models', label: 'Models on disk' },
  { id: 'loaded', label: 'Loaded now' },
  { id: 'test-models', label: 'Test models', pcOnly: true },
  { id: 'system', label: 'Resources and ports' },
  { id: 'log', label: 'Log' },
  { id: 'reset', label: 'Reset library', pcOnly: true, danger: true },
]

export const CARD_TAB = {
  setup: 'setup',
  recommended: 'setup',
  browser: 'setup',
  ffmpeg: 'setup',
  deno: 'setup',
  'cuda-runtime': 'setup',
  packages: 'packages',
  omnivoice: 'packages',
  'qwen3-tts': 'packages',
  chatterbox: 'packages',
  speaker: 'speaker',
  'model-status': 'model-status',
  models: 'models',
  'loaded-models': 'loaded',
  'process-memory': 'loaded',
  'test-models': 'test-models',
  resources: 'system',
  ports: 'system',
  log: 'log',
  reset: 'reset',
} as const satisfies Record<string, DiagnosticsTab>

export type DiagnosticsCard = keyof typeof CARD_TAB

export const isDiagnosticsTab = (v: unknown): v is DiagnosticsTab => DIAGNOSTICS_TABS.some((t) => t.id === v)
export const isDiagnosticsCard = (v: unknown): v is DiagnosticsCard => typeof v === 'string' && Object.hasOwn(CARD_TAB, v)

export const cardAnchorId = (card: DiagnosticsCard) => `diag-card-${card}`
