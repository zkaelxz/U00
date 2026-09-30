// Helpers for the crash fallbacks (ErrorBoundary.tsx and bootFallback.ts).

// The text shown for a caught error: the message only (no stack), capped so
// a huge message can't swamp the page.
export function errorText(error: unknown): string {
  const text = error instanceof Error ? error.message || error.name : String(error)
  return text.length > 2000 ? `${text.slice(0, 2000)}…` : text
}
