/** The visible reason a button is off; a title= tooltip never shows on a phone. Render only while the button is disabled for it. Pass a React useId() as id: free text such as a speaker label breaks aria-describedby. */
export function DisabledReason({ id, children }: { id: string; children: string | null | undefined | false }) {
  if (!children) return null
  return <p className="muted" id={id}>{children}</p>
}
