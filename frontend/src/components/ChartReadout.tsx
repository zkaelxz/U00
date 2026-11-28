/** Shared readout line for the app's small charts, so a value never lives only in a mouse-only tooltip. */

export function ChartReadout({ text, hint = 'Hover, tap or tab to a point to see its value.' }: { text: string; hint?: string }) {
  return <p className="chart-readout muted" role="status" aria-live="polite" data-testid="chart-readout">{text || hint}</p>
}
