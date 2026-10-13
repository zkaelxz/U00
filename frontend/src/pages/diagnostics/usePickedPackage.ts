import { useEffect } from 'react'

/**
 * A link with "&package=audio-separator" lands on the Packages card with that package picked: the
 * folds around its row open, the row scrolls into view and its install button takes focus. `ready`
 * is true once the rows exist (the install presets have loaded); a package that is not listed
 * (already installed, or not offered) leaves the card as it is.
 */
export function usePickedPackage(root: React.RefObject<HTMLElement | null>, pkg: string | undefined, ready: boolean, signal: number) {
  useEffect(() => {
    if (!pkg || !ready) return
    const rows = Array.from(root.current?.querySelectorAll<HTMLElement>('[data-pkg]') ?? []).filter((el) => el.dataset.pkg === pkg)
    // A package is listed under its task and, if not part of one, in the leftover list; the first is the visible one.
    const el = rows[0]
    if (!el) return
    for (let d = el.closest('details'); d; d = d.parentElement?.closest('details') ?? null) d.open = true
    el.scrollIntoView({ block: 'center' })
    const row = el.closest('li')
    row?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true })
    el.setAttribute('aria-current', 'true')
    return () => el.removeAttribute('aria-current')
  }, [root, pkg, ready, signal])
}
