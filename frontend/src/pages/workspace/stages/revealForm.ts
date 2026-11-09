/** Brings a form that opened off-screen into view and puts the cursor in its first field. */
export function revealForm(form: HTMLElement | null): void {
  form?.scrollIntoView?.({ block: 'center' })
  form?.querySelector('input')?.focus({ preventScroll: true })
}
