/** The visible reason a button is off; a title= tooltip never shows on a phone. Render only while the button is disabled for it. */
export function DisabledReason({ id, children }: { id: string; children: string | null | undefined | false }) {
  if (!children) return null
  return <span className="muted" id={id} data-testid="disabled-reason">{children}</span>
}
